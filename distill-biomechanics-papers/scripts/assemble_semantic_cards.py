#!/usr/bin/env python3
"""Assemble completed semantic cards from pending cards and analyst overlays.

This is a mechanical provenance step.  It deep-merges an analyst-authored
overlay into a pending card, then derives the reading provenance from the
corresponding unannotated packet itself: all chunk-heading locators and the
packet SHA-256 are computed locally.  Overlay data cannot replace identity,
hash, or reading-provenance fields.

The command is a dry-run by default.  Pass ``--write`` to atomically replace
cards.  Existing completed cards are left untouched unless
``--overwrite-completed`` is also supplied.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

try:  # ``python scripts/assemble_semantic_cards.py``
    from _common import utc_now
    from manage_semantic_reading import StateLock
except ImportError:  # pragma: no cover - allows package/module execution
    from ._common import utc_now
    from .manage_semantic_reading import StateLock


SCHEMA_VERSION = "1.0"
LOCATOR_HEADING_RE = re.compile(r"(?m)^\s*#{3,}\s+(S\d{3}:C\d{2})(?:\s|$)")
PACKET_SOURCE_HASH_RE = re.compile(r"(?mi)^-\s*Source SHA-256:\s*`([0-9a-f]{64})`\s*$")
COMPLETED_STATUSES = {"completed", "complete", "done", "read"}
ALLOWED_READER_TUPLES = {
    ("primary_codex", "gpt-5.6-sol", "max"),
    ("luna_primary", "gpt-5.6-luna", "max"),
}
DEFAULT_READER_POLICY: dict[str, Any] = {
    "start_reading_order": 46,
    "expected_reader_provenance": {
        "reader_role": "luna_primary",
        "reader_model": "gpt-5.6-luna",
        "reasoning_effort": "max",
    },
}

# Overlay keys in these groups are provenance or source identity.  A presence
# check (rather than a value comparison) is intentional: even an equal-value
# attempt is rejected so provenance cannot be edited through an overlay.
PROTECTED_KEYS = {
    "card_id",
    "paper_id",
    "pmcid",
    "source_record_sha256",
    "source_hash",
    "packet_sha256",
    "packet_path",
    "source_record",
    "record_path",
    "record_id",
    "selection_reason",
    "primary_stratum",
    "stratum",
    "strata",
    "reading_order",
    "selection_identity",
    "source_identity",
    "status",
}
PROTECTED_ROOTS = {"reading", "selection", "provenance", "source", "identity", "bibliography"}
REQUIRED_SEMANTIC_ROOTS = {
    "study",
    "argument_map",
    "evidence_boundary",
    "section_moves",
    "limitations",
    "writing_capability_candidates",
    "quality",
    "summary_zh",
    "reader_notes",
}


class AssemblyError(ValueError):
    """A card cannot be assembled without violating a provenance contract."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Deep-merge analyst overlays into semantic cards and derive "
            "completed reading provenance from packet locators and SHA-256."
        )
    )
    parser.add_argument(
        "--root",
        required=True,
        type=Path,
        help="Corpus root or semantic-distillation directory",
    )
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument(
        "--pmcid",
        action="append",
        default=[],
        metavar="PMCID",
        help="Assemble one PMCID (repeatable)",
    )
    selector.add_argument(
        "--all",
        action="store_true",
        help="Consider every JSON card under semantic-distillation/cards/",
    )
    parser.add_argument(
        "--read-at",
        metavar="ISO8601",
        help="Reading timestamp to write (default: one current UTC timestamp for the run)",
    )
    parser.add_argument(
        "--reader-role",
        default="primary_codex",
        help="Reader role recorded in completed cards (default: primary_codex)",
    )
    parser.add_argument(
        "--reader-model",
        default="gpt-5.6-sol",
        help="Reader model recorded in completed cards (default: gpt-5.6-sol)",
    )
    parser.add_argument(
        "--reasoning-effort",
        default="max",
        help="Reader reasoning effort recorded in completed cards (default: max)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Atomically replace assembled cards; without this flag the command is a dry-run",
    )
    parser.add_argument(
        "--overwrite-completed",
        action="store_true",
        help="Allow --write to replace cards whose reading.status is already completed",
    )
    return parser.parse_args(argv)


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _pmcid_key(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", str(value or "")).casefold()


def _deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = copy.deepcopy(dict(base))
    for key, value in overlay.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _overlay_protected_paths(value: Any, path: tuple[str, ...] = ()) -> list[str]:
    """Return every overlay path that attempts to alter protected metadata."""

    violations: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            key_folded = key_text.casefold()
            child_path = path + (key_text,)
            root = path[0].casefold() if path else ""
            if key_folded in PROTECTED_KEYS or key_folded in PROTECTED_ROOTS or root in PROTECTED_ROOTS:
                violations.append(".".join(child_path))
            violations.extend(_overlay_protected_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            violations.extend(_overlay_protected_paths(child, path + (f"[{index}]",)))
    return violations


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _packet_locators(text: str) -> list[str]:
    # Heading-only extraction avoids treating prose mentions such as
    # ``S003:C02`` as a read locator. Duplicates remain visible so assembly can
    # reject a physically ambiguous packet instead of silently collapsing it.
    return [match.group(1) for match in LOCATOR_HEADING_RE.finditer(text)]


def _locator_sequence_error(locators: list[str]) -> str:
    previous_section = 0
    previous_chunk = 0
    for locator in locators:
        section = int(locator[1:4])
        chunk = int(locator[6:8])
        if previous_section == 0:
            if (section, chunk) != (1, 1):
                return f"chunk sequence must start at S001:C01, found {locator}"
        elif section == previous_section:
            if chunk != previous_chunk + 1:
                return f"non-contiguous chunk sequence at {locator}"
        elif section == previous_section + 1:
            if chunk != 1:
                return f"new section must start at C01, found {locator}"
        else:
            return f"non-contiguous section sequence at {locator}"
        previous_section, previous_chunk = section, chunk
    return ""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssemblyError(f"could not read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AssemblyError(f"JSON object expected: {path}")
    return value


def _reader_policy(sd: Path) -> dict[str, Any]:
    path = sd / "reader-policy.json"
    if not path.is_file():
        return json.loads(json.dumps(DEFAULT_READER_POLICY))
    value = _load_json(path)
    expected = value.get("expected_reader_provenance")
    try:
        start = int(value.get("start_reading_order") or 0)
    except (TypeError, ValueError) as exc:
        raise AssemblyError(f"invalid reader-policy start_reading_order: {exc}") from exc
    if (
        start <= 0
        or not isinstance(expected, Mapping)
        or not all(str(expected.get(field) or "").strip() for field in ("reader_role", "reader_model", "reasoning_effort"))
    ):
        raise AssemblyError(f"invalid or incomplete reader-policy schema: {path}")
    return value


def _resolve_roots(root: Path) -> tuple[Path, Path]:
    resolved = root.expanduser().resolve()
    if resolved.name.casefold() == "semantic-distillation" or (
        (resolved / "cards").is_dir()
        and (resolved / "packets").is_dir()
        and not (resolved / "semantic-distillation").exists()
    ):
        return resolved.parent, resolved
    return resolved, resolved / "semantic-distillation"


def _identity_from_card(card: Mapping[str, Any], path: Path) -> tuple[str, str]:
    """Return (pmcid, paper_id), using filename/card_id as fallbacks."""

    paper = card.get("paper") if isinstance(card.get("paper"), Mapping) else {}
    pmcid = str(card.get("pmcid") or paper.get("pmcid") or "").strip()
    if not pmcid:
        card_id = str(card.get("card_id") or "").strip()
        if ":" in card_id and card_id.casefold().startswith("semantic:"):
            pmcid = card_id.split(":", 1)[1].strip()
    if not pmcid:
        pmcid = path.stem
    paper_id = str(card.get("paper_id") or paper.get("paper_id") or "").strip()
    return pmcid, paper_id


def _card_status(card: Mapping[str, Any]) -> str:
    reading = card.get("reading")
    if isinstance(reading, Mapping):
        status = _norm(reading.get("status"))
    else:
        status = _norm(card.get("status"))
    return "completed" if status in COMPLETED_STATUSES else "pending"


def _casefold_paths(directory: Path, suffix: str) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    if not directory.is_dir():
        return paths
    for path in sorted(directory.glob(f"*{suffix}")):
        key = _pmcid_key(path.stem) or path.stem.casefold()
        # Keep the first lexicographically stable path if a fixture contains
        # case-only duplicates; the duplicate is reported by the caller when
        # it asks for that identity.
        paths.setdefault(key, path)
    return paths


def _selection_rows(sd: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    path = sd / "selection.jsonl"
    rows: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    if not path.exists():
        return rows, errors
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        return rows, [f"could not read {path}: {exc}"]
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path}:{line_number}: invalid JSON ({exc.msg})")
            continue
        if not isinstance(value, dict):
            errors.append(f"{path}:{line_number}: selection row is not an object")
            continue
        key = _pmcid_key(value.get("pmcid"))
        if key:
            if key in rows:
                errors.append(f"{path}:{line_number}: duplicate PMCID selection identity {value.get('pmcid')!r}")
            else:
                rows[key] = value
        else:
            errors.append(f"{path}:{line_number}: selection row has no claimable PMCID")
    return rows, errors


def _load_overlay(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, None
    try:
        value = _load_json(path)
    except AssemblyError as exc:
        return None, str(exc)
    violations = _overlay_protected_paths(value)
    if violations:
        return None, f"overlay attempts to overwrite protected fields: {', '.join(violations)}"
    missing = sorted(REQUIRED_SEMANTIC_ROOTS.difference(value))
    if missing:
        return None, f"overlay is missing required semantic roots: {', '.join(missing)}"
    for key in ("study", "argument_map", "evidence_boundary", "limitations", "quality"):
        if not isinstance(value.get(key), Mapping) or not value.get(key):
            return None, f"overlay semantic root must be a nonempty object: {key}"
    for key in ("section_moves", "writing_capability_candidates"):
        if not isinstance(value.get(key), list) or not value.get(key):
            return None, f"overlay semantic root must be a nonempty list: {key}"
    if not str(value.get("summary_zh") or "").strip():
        return None, "overlay summary_zh must be nonempty"
    if not isinstance(value.get("reader_notes"), list):
        return None, "overlay reader_notes must be a list"
    return value, None


def _packet_for(
    sd: Path,
    external_root: Path,
    pmcid: str,
    selection: Mapping[str, Any] | None,
) -> Path | None:
    canonical = sd / "packets" / f"{pmcid}.md"
    if canonical.exists():
        return canonical
    packet_key = _pmcid_key(pmcid)
    packets_dir = sd / "packets"
    if packets_dir.is_dir():
        for candidate in sorted(packets_dir.glob("*.md")):
            if _pmcid_key(candidate.stem) == packet_key:
                return candidate
    packet_value = str((selection or {}).get("packet_path") or "").strip()
    if not packet_value:
        return None
    candidate = Path(packet_value)
    alternatives = [candidate] if candidate.is_absolute() else [sd / candidate, external_root / candidate]
    for alternate in alternatives:
        if alternate.exists():
            return alternate
    return None


def _set_or_check_identity(
    card: dict[str, Any],
    pmcid: str,
    paper_id: str,
    selection: Mapping[str, Any] | None,
) -> None:
    expected_pmcid = pmcid
    current_pmcid, current_paper_id = _identity_from_card(card, Path(f"{pmcid}.json"))
    if current_pmcid and _pmcid_key(current_pmcid) != _pmcid_key(expected_pmcid):
        raise AssemblyError(f"card PMCID does not match requested identity: {current_pmcid!r} != {expected_pmcid!r}")
    selection_paper_id = str((selection or {}).get("paper_id") or paper_id or "").strip()
    if current_paper_id and selection_paper_id and _norm(current_paper_id) != _norm(selection_paper_id):
        raise AssemblyError(f"card paper_id does not match selection: {current_paper_id!r} != {selection_paper_id!r}")
    card["pmcid"] = current_pmcid if current_pmcid and _pmcid_key(current_pmcid) == _pmcid_key(expected_pmcid) else expected_pmcid
    if current_paper_id:
        card["paper_id"] = current_paper_id
    elif selection_paper_id:
        card["paper_id"] = selection_paper_id
    card_id = str(card.get("card_id") or "").strip()
    expected_card_id = f"semantic:{expected_pmcid}"
    if card_id and _norm(card_id) != _norm(expected_card_id):
        raise AssemblyError(f"card_id does not match PMCID: {card_id!r} != {expected_card_id!r}")
    card["card_id"] = card_id or expected_card_id

    expected_hash = str(
        (selection or {}).get("source_record_sha256")
        or (selection or {}).get("source_hash")
        or ""
    ).strip()
    source = card.get("source") if isinstance(card.get("source"), Mapping) else {}
    provenance = card.get("provenance") if isinstance(card.get("provenance"), Mapping) else {}
    current_hash = str(
        card.get("source_record_sha256")
        or card.get("source_hash")
        or source.get("source_record_sha256")
        or source.get("source_hash")
        or provenance.get("source_record_sha256")
        or provenance.get("source_hash")
        or ""
    ).strip()
    if current_hash and expected_hash and current_hash.casefold() != expected_hash.casefold():
        raise AssemblyError("card source_record_sha256 does not match selection")
    if not current_hash and expected_hash:
        card["source_record_sha256"] = expected_hash
    elif current_hash and not card.get("source_record_sha256"):
        card["source_record_sha256"] = current_hash


def _preserve_selection_source(card: dict[str, Any], selection: Mapping[str, Any] | None) -> None:
    """Retain compact selection/source identity without copying source text."""

    if not selection:
        return
    if "selection" not in card:
        compact_selection = {
            key: selection[key]
            for key in ("selection_reason", "primary_stratum", "stratum_rank", "reading_order")
            if key in selection
        }
        if compact_selection:
            card["selection"] = compact_selection
    if "source" not in card:
        compact_source = {
            key: selection[key]
            for key in ("record_path", "source_record", "source_record_sha256", "source_hash")
            if key in selection
        }
        if compact_source:
            card["source"] = compact_source


def _assemble(
    card: Mapping[str, Any],
    pmcid: str,
    selection: Mapping[str, Any] | None,
    overlay: Mapping[str, Any] | None,
    packet_hash: str,
    locators: list[str],
    read_at: str,
    reader_role: str,
    reader_model: str,
    reasoning_effort: str,
) -> dict[str, Any]:
    merged = _deep_merge(card, overlay or {})
    _set_or_check_identity(
        merged,
        pmcid,
        str((selection or {}).get("paper_id") or "").strip(),
        selection,
    )
    _preserve_selection_source(merged, selection)
    reading = merged.get("reading")
    if not isinstance(reading, dict):
        reading = {}
        merged["reading"] = reading
    reading.update(
        {
            "status": "completed",
            "access_level": "full_text_read",
            "reader_role": reader_role,
            "reader_model": reader_model,
            "reasoning_effort": reasoning_effort,
            "read_at": read_at,
            "packet_sha256": packet_hash,
            "section_locators_read": locators,
            "omissions": [],
            "adjudication_status": "self_reviewed",
        }
    )
    if _norm(merged.get("status")) == "blank":
        merged.pop("status", None)
    merged.setdefault("schema_version", SCHEMA_VERSION)
    return merged


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _target_cards(sd: Path, pmcids: list[str], all_cards: bool) -> list[Path]:
    cards_dir = sd / "cards"
    if all_cards or not pmcids:
        return sorted(cards_dir.glob("*.json")) if cards_dir.is_dir() else []
    by_key = _casefold_paths(cards_dir, ".json")
    targets: list[Path] = []
    for pmcid in pmcids:
        path = by_key.get(_pmcid_key(pmcid)) or by_key.get(pmcid.casefold())
        targets.append(path or cards_dir / f"{pmcid}.json")
    return targets


def run(args: argparse.Namespace) -> dict[str, Any]:
    external_root, sd = _resolve_roots(args.root)
    read_at = str(args.read_at or utc_now()).strip()
    reader_role = str(args.reader_role or "").strip()
    reader_model = str(args.reader_model or "").strip()
    reasoning_effort = str(args.reasoning_effort or "").strip()
    if not reader_role or not reader_model or not reasoning_effort:
        raise AssemblyError("reader role, model, and reasoning effort must be nonempty")
    if (reader_role, reader_model, reasoning_effort) not in ALLOWED_READER_TUPLES:
        raise AssemblyError(
            "unsupported reader provenance tuple; expected primary_codex/gpt-5.6-sol/max "
            "or luna_primary/gpt-5.6-luna/max"
        )
    requested = []
    seen_requested: set[str] = set()
    for value in args.pmcid or []:
        cleaned = str(value or "").strip()
        key = _pmcid_key(cleaned)
        if cleaned and key and key not in seen_requested:
            requested.append(cleaned)
            seen_requested.add(key)
    selection, selection_errors = _selection_rows(sd)
    reader_policy = _reader_policy(sd)
    overlay_paths = _casefold_paths(sd / "overlays", ".json")
    targets = _target_cards(sd, requested, bool(args.all))
    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "semantic_distillation": str(sd),
        "dry_run": not bool(args.write),
        "write": bool(args.write),
        "overwrite_completed": bool(args.overwrite_completed),
        "read_at": read_at,
        "reader_role": reader_role,
        "reader_model": reader_model,
        "reasoning_effort": reasoning_effort,
        "requested_pmcids": requested,
        "counts": {
            "target_cards": len(targets),
            "assembled": 0,
            "written": 0,
            "skipped_completed": 0,
            "missing_cards": 0,
            "missing_packets": 0,
            "overlay_errors": 0,
            "errors": len(selection_errors),
            "warnings": 0,
        },
        "items": [],
        "errors": list(selection_errors),
        "warnings": [],
    }
    for path in targets:
        item: dict[str, Any] = {"path": str(path), "status": "pending"}
        if not path.exists():
            item["status"] = "missing_card"
            item["error"] = f"card not found: {path}"
            summary["counts"]["missing_cards"] += 1
            summary["counts"]["errors"] += 1
            summary["errors"].append(item["error"])
            summary["items"].append(item)
            continue
        try:
            card = _load_json(path)
        except AssemblyError as exc:
            item["status"] = "error"
            item["error"] = str(exc)
            summary["counts"]["errors"] += 1
            summary["errors"].append(str(exc))
            summary["items"].append(item)
            continue
        pmcid, paper_id = _identity_from_card(card, path)
        item["pmcid"] = pmcid
        item["paper_id"] = paper_id
        current_status = _card_status(card)
        item["existing_status"] = current_status
        overlay_path = overlay_paths.get(_pmcid_key(pmcid))
        if overlay_path:
            item["overlay_path"] = str(overlay_path)
        overlay, overlay_error = _load_overlay(overlay_path) if overlay_path else (None, None)
        if overlay_error:
            item["status"] = "overlay_error"
            item["error"] = overlay_error
            summary["counts"]["overlay_errors"] += 1
            summary["counts"]["errors"] += 1
            summary["errors"].append(f"{path}: {overlay_error}")
            summary["items"].append(item)
            continue
        if current_status == "completed" and not args.overwrite_completed:
            item["status"] = "skipped_completed"
            summary["counts"]["skipped_completed"] += 1
            summary["items"].append(item)
            continue
        if overlay_path is None:
            item["status"] = "missing_overlay"
            item["error"] = f"analyst overlay not found for {pmcid}; a packet or pending card is not evidence of semantic reading"
            summary["counts"]["overlay_errors"] += 1
            summary["counts"]["errors"] += 1
            summary["errors"].append(f"{path}: {item['error']}")
            summary["items"].append(item)
            continue
        row = selection.get(_pmcid_key(pmcid))
        if reader_policy and row is not None:
            try:
                row_order = int(row.get("reading_order") or 0)
                start_order = int(reader_policy.get("start_reading_order") or 0)
            except (TypeError, ValueError):
                row_order = 0
                start_order = 0
            expected_reader = reader_policy.get("expected_reader_provenance")
            if start_order and row_order >= start_order and isinstance(expected_reader, Mapping):
                actual_tuple = (reader_role, reader_model, reasoning_effort)
                expected_tuple = tuple(str(expected_reader.get(field) or "").strip() for field in ("reader_role", "reader_model", "reasoning_effort"))
                if actual_tuple != expected_tuple:
                    item["status"] = "reader_policy_error"
                    item["error"] = f"reading_order {row_order} requires reader provenance {expected_tuple!r}"
                    summary["counts"]["errors"] += 1
                    summary["errors"].append(f"{path}: {item['error']}")
                    summary["items"].append(item)
                    continue
        packet_path = _packet_for(sd, external_root, pmcid, row)
        if packet_path is None:
            item["status"] = "missing_packet"
            item["error"] = f"packet not found for {pmcid}"
            summary["counts"]["missing_packets"] += 1
            summary["counts"]["errors"] += 1
            summary["errors"].append(f"{path}: {item['error']}")
            summary["items"].append(item)
            continue
        try:
            packet_bytes = packet_path.read_bytes()
            packet_text = packet_bytes.decode("utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            item["status"] = "packet_error"
            item["error"] = f"could not read packet {packet_path}: {exc}"
            summary["counts"]["errors"] += 1
            summary["errors"].append(f"{path}: {item['error']}")
            summary["items"].append(item)
            continue
        packet_hash = _sha256_bytes(packet_bytes)
        locators = _packet_locators(packet_text)
        if not locators:
            item["status"] = "packet_error"
            item["error"] = f"packet has no canonical Sddd:Cdd chunk headings: {packet_path}"
            summary["counts"]["errors"] += 1
            summary["errors"].append(f"{path}: {item['error']}")
            summary["items"].append(item)
            continue
        duplicate_locators = sorted({locator for locator in locators if locators.count(locator) > 1})
        sequence_error = _locator_sequence_error(locators)
        if duplicate_locators or sequence_error:
            item["status"] = "packet_error"
            item["error"] = (
                f"duplicate packet chunk headings: {', '.join(duplicate_locators)}"
                if duplicate_locators
                else sequence_error
            )
            summary["counts"]["errors"] += 1
            summary["errors"].append(f"{path}: {item['error']}")
            summary["items"].append(item)
            continue
        packet_source_match = PACKET_SOURCE_HASH_RE.search(packet_text)
        packet_source_hash = packet_source_match.group(1).casefold() if packet_source_match else ""
        selection_source_hash = str(
            (row or {}).get("source_record_sha256") or (row or {}).get("source_hash") or ""
        ).strip().casefold()
        if not packet_source_hash or not selection_source_hash or packet_source_hash != selection_source_hash:
            item["status"] = "packet_error"
            item["error"] = "packet Source SHA-256 is missing or does not match selection"
            summary["counts"]["errors"] += 1
            summary["errors"].append(f"{path}: {item['error']}")
            summary["items"].append(item)
            continue
        try:
            assembled = _assemble(
                card,
                pmcid,
                row,
                overlay,
                packet_hash,
                locators,
                read_at,
                reader_role,
                reader_model,
                reasoning_effort,
            )
        except AssemblyError as exc:
            item["status"] = "identity_error"
            item["error"] = str(exc)
            summary["counts"]["errors"] += 1
            summary["errors"].append(f"{path}: {exc}")
            summary["items"].append(item)
            continue
        item.update(
            {
                "status": "assembled",
                "packet_path": str(packet_path),
                "packet_sha256": packet_hash,
                "locator_count": len(locators),
                "overlay": bool(overlay_path),
            }
        )
        summary["counts"]["assembled"] += 1
        if args.write:
            try:
                with StateLock(sd / "luna-state"):
                    # Recheck both immutable inputs at the commit point. This
                    # closes races with another assembler or packet preparer.
                    current_card = _load_json(path)
                    if _card_status(current_card) == "completed" and not args.overwrite_completed:
                        raise AssemblyError("card became completed before write; refusing concurrent overwrite")
                    current_packet_hash = _sha256_bytes(packet_path.read_bytes())
                    if current_packet_hash != packet_hash:
                        raise AssemblyError("packet changed after assembly; rerun against the current packet")
                    _atomic_write_json(path, assembled)
            except (OSError, AssemblyError) as exc:
                item["status"] = "write_error"
                item["error"] = f"atomic write failed: {exc}"
                summary["counts"]["errors"] += 1
                summary["errors"].append(f"{path}: {item['error']}")
            else:
                item["status"] = "written"
                summary["counts"]["written"] += 1
        summary["items"].append(item)
    return summary


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        summary = run(args)
    except Exception as exc:  # Keep CLI diagnostics machine-readable.
        summary = {
            "schema_version": SCHEMA_VERSION,
            "dry_run": not bool(args.write),
            "write": bool(args.write),
            "counts": {"target_cards": 0, "assembled": 0, "written": 0, "errors": 1},
            "items": [],
            "errors": [str(exc)],
            "warnings": [],
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if int(summary.get("counts", {}).get("errors", 0)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
