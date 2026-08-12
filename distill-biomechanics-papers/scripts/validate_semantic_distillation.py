#!/usr/bin/env python3
"""Validate model-read semantic-distillation artifacts.

The validator deliberately checks only structural and provenance contracts.  A
successful run says that the files are internally traceable; it does not assess
whether a paper was interpreted correctly or whether a writing rule is true.

``--root`` is the external corpus root.  Artifacts are expected below
``<root>/semantic-distillation``::

    selection.jsonl
    cards/*.json
    packets/<PMCID>.md
    reports/validation.json       (written by this command)

For convenience, a path that is itself a ``semantic-distillation`` directory
is accepted as well.  ``--json`` emits the same report to stdout; the report
file is written in both output modes so that validation is reproducible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


LOCATOR_RE = re.compile(r"(?<![A-Za-z0-9])S\d{3}:C\d{2}(?![A-Za-z0-9])")
LOCATOR_FULL_RE = re.compile(r"^S\d{3}:C\d{2}$")
PACKET_HEADING_RE = re.compile(r"(?m)^\s*#{3,}\s+(S\d{3}:C\d{2})(?:\s|$)")
PACKET_SOURCE_HASH_RE = re.compile(r"(?mi)^-\s*Source SHA-256:\s*`([0-9a-f]{64})`\s*$")
PROMOTED_LOCATOR_RE = re.compile(r"^(PMC[A-Za-z0-9]+):S\d{3}:C\d{2}$", re.IGNORECASE)
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
COMPLETED_STATUSES = {"completed", "complete", "done", "read"}
PENDING_STATUSES = {"pending", "queued", "selected", "in_progress", "in-progress", "unread"}
VALID_CONFIDENCE = {"high", "moderate", "low"}
DEFAULT_READER_POLICY: dict[str, Any] = {
    "start_reading_order": 46,
    "expected_reader_provenance": {
        "reader_role": "luna_primary",
        "reader_model": "gpt-5.6-luna",
        "reasoning_effort": "max",
    },
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate semantic-distillation selection, cards, packets, and "
            "promoted rules using structural checks only."
        )
    )
    parser.add_argument(
        "--root",
        required=True,
        type=Path,
        help="External corpus root containing semantic-distillation/",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings (including pending or non-canonical artifacts) as failures",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the JSON report to stdout (it is also written to reports/validation.json)",
    )
    return parser.parse_args(argv)


def text_value(value: Any) -> str:
    """Return a trimmed scalar string, or an empty string for containers/null."""

    if isinstance(value, str):
        return value.strip()
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def nonempty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(nonempty(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return bool(value) and any(nonempty(item) for item in value)
    return value is not None


def norm_id(value: Any) -> str:
    return text_value(value).casefold()


def norm_hash(value: Any) -> str:
    return text_value(value).casefold()


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        # A single locator/string is useful in hand-authored cards and should
        # still be checked rather than silently ignored.
        return [value]
    return []


def iter_strings(value: Any, path: str = "") -> Iterable[tuple[str, str]]:
    """Yield every string in a JSON value with a human-readable JSON path."""

    if isinstance(value, str):
        yield path, value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            yield from iter_strings(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_strings(item, f"{path}[{index}]")


def extract_locators(value: Any) -> list[tuple[str, str]]:
    """Return (locator, JSON path) pairs found in strings recursively."""

    found: list[tuple[str, str]] = []
    for path, string in iter_strings(value):
        found.extend((match.group(0), path) for match in LOCATOR_RE.finditer(string))
    return found


def packet_chunk_locators(packet_text: str) -> list[str]:
    """Return stable chunk-heading locators from an unannotated packet."""

    return [match.group(1) for match in PACKET_HEADING_RE.finditer(packet_text)]


def packet_locator_sequence_error(locators: list[str]) -> str:
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def first_value(mapping: Mapping[str, Any] | None, *keys: str) -> Any:
    if not isinstance(mapping, Mapping):
        return None
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", []):
            return value
    return None


def status_kind(value: Any) -> str:
    status = text_value(value).casefold()
    if status in COMPLETED_STATUSES:
        return "completed"
    if status in PENDING_STATUSES or not status:
        return "pending"
    return "invalid"


def card_tokens(card: Mapping[str, Any]) -> set[str]:
    """Build matching tokens from the card's paper/PMCID/card identities."""

    tokens: set[str] = set()
    paper = card.get("paper") if isinstance(card.get("paper"), Mapping) else {}
    for key in ("paper_id", "pmcid", "card_id"):
        value = norm_id(card.get(key)) or norm_id(paper.get(key))
        if value:
            tokens.add(f"{key}:{value}")
            if key == "card_id" and value.startswith("semantic:"):
                tokens.add("pmcid:" + value.split(":", 1)[1])
    return tokens


def selection_tokens(row: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in ("paper_id", "pmcid", "card_id"):
        value = norm_id(row.get(key))
        if value:
            tokens.add(f"{key}:{value}")
    return tokens


def card_status_kind(card: Mapping[str, Any]) -> str:
    """Classify template cards and prepare-script blank stubs alike."""

    reading = card.get("reading")
    if isinstance(reading, Mapping):
        return status_kind(reading.get("status"))
    # ``prepare_semantic_distillation.py --card-stubs`` intentionally emits a
    # lightweight ``status: blank``/``paper`` object before semantic reading.
    top_status = text_value(card.get("status")).casefold()
    if top_status in {"blank", "pending", "selected", "queued", "unread", "in_progress", "in-progress"}:
        return "pending"
    if top_status in COMPLETED_STATUSES:
        return "completed"
    return "invalid"


def card_identity_value(card: Mapping[str, Any], key: str) -> str:
    direct = text_value(card.get(key))
    if direct:
        return direct
    paper = card.get("paper")
    if isinstance(paper, Mapping):
        return text_value(paper.get(key))
    return ""


class SemanticValidator:
    """Stateful validator keeping issues and per-row/card status."""

    def __init__(self, root: Path, strict: bool = False) -> None:
        self.root = root.resolve()
        if self.root.name.casefold() == "semantic-distillation" and (self.root / "selection.jsonl").exists():
            self.sd = self.root
            self.external_root = self.root.parent
        else:
            self.external_root = self.root
            self.sd = self.root / "semantic-distillation"
        self.strict = strict
        self.issues: list[dict[str, Any]] = []
        self.reader_policy: dict[str, Any] = json.loads(json.dumps(DEFAULT_READER_POLICY))
        policy_path = self.sd / "reader-policy.json"
        if policy_path.is_file():
            try:
                policy_value = json.loads(policy_path.read_text(encoding="utf-8-sig"))
                expected = policy_value.get("expected_reader_provenance") if isinstance(policy_value, dict) else None
                start = int(policy_value.get("start_reading_order") or 0) if isinstance(policy_value, dict) else 0
                if (
                    isinstance(policy_value, dict)
                    and start > 0
                    and isinstance(expected, dict)
                    and all(text_value(expected.get(field)) for field in ("reader_role", "reader_model", "reasoning_effort"))
                ):
                    self.reader_policy = policy_value
                else:
                    self.error("READER_POLICY", "Reader policy has an invalid or incomplete schema", str(policy_path))
            except (OSError, json.JSONDecodeError) as exc:
                self.error("READER_POLICY", f"Could not read reader policy: {exc}", str(policy_path))
            except (TypeError, ValueError) as exc:
                self.error("READER_POLICY", f"Reader policy has an invalid start_reading_order: {exc}", str(policy_path))
        self.selection: list[dict[str, Any]] = []
        self.cards: list[tuple[Path, dict[str, Any]]] = []
        self.card_matches: dict[int, list[int]] = defaultdict(list)
        self.orphan_card_indices: set[int] = set()
        self.invalid_card_indices: set[int] = set()
        self.row_status: dict[int, str] = {}
        self.row_invalid: set[int] = set()
        self.selection_parse_errors = 0
        self.synthesis_files: list[Path] = []
        self.synthesis_rows: list[dict[str, Any]] = []

    def issue(self, severity: str, code: str, message: str, location: str | None = None) -> None:
        item: dict[str, Any] = {"severity": severity, "code": code, "message": message}
        if location:
            try:
                item["location"] = str(Path(location).resolve().relative_to(self.external_root))
            except (ValueError, OSError):
                item["location"] = str(location)
        self.issues.append(item)

    def error(self, code: str, message: str, location: str | None = None) -> None:
        self.issue("ERROR", code, message, location)

    def warning(self, code: str, message: str, location: str | None = None) -> None:
        self.issue("WARNING", code, message, location)

    def load_selection(self) -> None:
        path = self.sd / "selection.jsonl"
        if not path.exists():
            self.error("MISSING_SELECTION", "selection.jsonl is missing", str(path))
            return
        try:
            lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        except OSError as exc:
            self.error("SELECTION_READ", str(exc), str(path))
            return
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                self.selection_parse_errors += 1
                self.error("SELECTION_JSON", f"Invalid JSON: {exc.msg}", f"{path}:{line_number}")
                continue
            if not isinstance(value, dict):
                self.selection_parse_errors += 1
                self.error("SELECTION_ROW", "Selection row must be a JSON object", f"{path}:{line_number}")
                continue
            row = dict(value)
            row["_line"] = line_number
            self.selection.append(row)

        seen: dict[str, int] = {}
        for index, row in enumerate(self.selection):
            tokens = selection_tokens(row)
            if not tokens:
                self.warning(
                    "SELECTION_ID",
                    "Selection row has neither paper_id nor pmcid; a card cannot be matched reliably",
                    f"{path}:{row['_line']}",
                )
            else:
                for token in sorted(tokens):
                    if token in seen:
                        previous_index = seen[token]
                        previous_line = self.selection[previous_index]["_line"]
                        self.error(
                            "DUPLICATE_SELECTION",
                            f"Selection identity {token!r} appears more than once (rows {previous_line} and {row['_line']})",
                            f"{path}:{row['_line']}",
                        )
                        # Both copies are ambiguous and must never become
                        # claimable work items in the semantic job manager.
                        self.row_invalid.add(previous_index)
                        self.row_invalid.add(index)
                    else:
                        seen[token] = index
            if not tokens:
                self.row_invalid.add(index)
            # A missing selection status is equivalent to a selected/pending row.
            if row.get("status") not in (None, "", "selected", "pending", "completed", "complete"):
                self.warning(
                    "SELECTION_STATUS",
                    f"Unrecognised selection status: {row.get('status')!r}",
                    f"{path}:{row['_line']}",
                )

    def load_cards(self) -> None:
        cards_dir = self.sd / "cards"
        if not cards_dir.exists():
            self.warning("MISSING_CARDS_DIR", "cards/ directory is missing; selected rows remain pending", str(cards_dir))
            return
        if not cards_dir.is_dir():
            self.error("CARDS_NOT_DIR", "cards exists but is not a directory", str(cards_dir))
            return
        paths = sorted(cards_dir.glob("*.json"))
        seen_tokens: dict[str, int] = {}
        for path in paths:
            try:
                value = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
            except (OSError, json.JSONDecodeError) as exc:
                self.error("CARD_JSON", f"Could not parse card: {exc}", str(path))
                self.invalid_card_indices.add(len(self.cards))
                self.cards.append((path, {}))
                continue
            if not isinstance(value, dict):
                self.error("CARD_OBJECT", "Card must be a JSON object", str(path))
                self.invalid_card_indices.add(len(self.cards))
                self.cards.append((path, {}))
                continue
            card = dict(value)
            card_index = len(self.cards)
            self.cards.append((path, card))
            tokens = card_tokens(card)
            if not tokens:
                self.error("CARD_ID", "Card has neither paper_id, pmcid, nor card_id", str(path))
                self.invalid_card_indices.add(card_index)
                self.orphan_card_indices.add(card_index)
            for token in tokens:
                if token in seen_tokens:
                    self.error(
                        "DUPLICATE_CARD",
                        f"Card identity duplicates {self.cards[seen_tokens[token]][0].name}",
                        str(path),
                    )
                    self.invalid_card_indices.add(card_index)
                    self.invalid_card_indices.add(seen_tokens[token])
                else:
                    seen_tokens[token] = card_index

        selection_token_map: dict[str, list[int]] = defaultdict(list)
        for row_index, row in enumerate(self.selection):
            for token in selection_tokens(row):
                selection_token_map[token].append(row_index)
        for card_index, (path, card) in enumerate(self.cards):
            matches: set[int] = set()
            for token in card_tokens(card):
                matches.update(selection_token_map.get(token, []))
            if not matches:
                self.orphan_card_indices.add(card_index)
                self.warning("ORPHAN_CARD", "Card is not represented in selection.jsonl", str(path))
            elif len(matches) > 1:
                self.error("CARD_AMBIGUOUS", "Card matches multiple selection rows", str(path))
                self.invalid_card_indices.add(card_index)
            else:
                self.card_matches[next(iter(matches))].append(card_index)

    def packet_path(self, row: Mapping[str, Any] | None, pmcid: str) -> tuple[Path | None, bool]:
        """Return packet path and whether a non-canonical fallback was used."""

        canonical = self.sd / "packets" / f"{pmcid}.md"
        if canonical.exists():
            return canonical, False
        # A packet_path emitted by a builder is accepted as a useful recovery,
        # but remains a warning because the contract names packets/<PMCID>.md.
        candidate_value = text_value((row or {}).get("packet_path"))
        if candidate_value:
            candidate = Path(candidate_value)
            candidates = [candidate] if candidate.is_absolute() else [self.sd / candidate, self.external_root / candidate]
            for alternate in candidates:
                if alternate.exists():
                    self.warning(
                        "PACKET_NONCANONICAL",
                        f"Using selection packet_path because canonical packet is missing: {alternate}",
                        str(alternate),
                    )
                    return alternate, True
        return None, False

    def check_locator_list(
        self,
        values: Any,
        packet_text: str,
        location: str,
        required: bool = False,
    ) -> bool:
        """Check locators against canonical packet chunk headings."""

        items = as_list(values)
        packet_set = set(packet_chunk_locators(packet_text))
        valid = True
        if required and not items:
            self.error("LOCATORS_EMPTY", "At least one section locator is required", location)
            return False
        for index, value in enumerate(items):
            locator = text_value(value)
            item_location = f"{location}[{index}]"
            if not locator or not LOCATOR_FULL_RE.fullmatch(locator):
                self.error("LOCATOR_FORMAT", f"Invalid locator (expected Sddd:Cdd): {value!r}", item_location)
                valid = False
            elif locator not in packet_set:
                self.error("LOCATOR_MISSING", f"Locator is not a packet chunk heading: {locator}", item_location)
                valid = False
        return valid

    def validate_completed_card(self, card_index: int, row_index: int | None) -> bool:
        path, card = self.cards[card_index]
        valid = card_index not in self.invalid_card_indices
        location = str(path)
        if not card:
            return False

        row = self.selection[row_index] if row_index is not None else None
        # Identity/hash matching is intentionally required only for completed
        # cards. Pending cards can be started from a skeletal template.
        card_paper = card_identity_value(card, "paper_id")
        card_pmcid = card_identity_value(card, "pmcid")
        expected_source_hash = ""
        actual_source_hash = ""
        source = card.get("source") if isinstance(card.get("source"), Mapping) else {}
        card_source_hash = text_value(
            first_value(card, "source_record_sha256", "source_hash")
            or first_value(card.get("provenance") if isinstance(card.get("provenance"), Mapping) else None, "source_record_sha256", "source_hash")
            or first_value(source, "source_record_sha256", "source_hash")
        )
        if row is None:
            self.error("COMPLETED_ORPHAN", "Completed card has no matching selection row", location)
            valid = False
        else:
            expected_paper = text_value(row.get("paper_id"))
            expected_pmcid = text_value(row.get("pmcid"))
            expected_source_hash = text_value(first_value(row, "source_record_sha256", "source_hash"))
            for label, expected, actual in (
                ("paper_id", expected_paper, card_paper),
                ("pmcid", expected_pmcid, card_pmcid),
                ("source_record_sha256", expected_source_hash, card_source_hash),
            ):
                if not expected or not actual:
                    self.error("IDENTITY_MISSING", f"Completed card requires matching {label}", location)
                    valid = False
                elif (norm_hash(expected) if label.endswith("sha256") else norm_id(expected)) != (
                    norm_hash(actual) if label.endswith("sha256") else norm_id(actual)
                ):
                    self.error(
                        "IDENTITY_MISMATCH",
                        f"Card {label} does not match selection ({actual!r} != {expected!r})",
                        location,
                    )
                    valid = False
            if expected_source_hash and not SHA256_RE.fullmatch(expected_source_hash):
                self.warning("SOURCE_HASH_FORMAT", "Selection source_record_sha256 is not a 64-hex SHA-256", location)
            if card_source_hash and not SHA256_RE.fullmatch(card_source_hash):
                self.error("SOURCE_HASH_FORMAT", "Card source_record_sha256 is not a 64-hex SHA-256", location)
                valid = False

            source_record_value = text_value(first_value(row, "record_path", "source_record"))
            if not source_record_value:
                self.error("SOURCE_RECORD_PATH", "Selection row does not identify its source record", location)
                valid = False
            else:
                source_record = Path(source_record_value)
                source_candidates = (
                    [source_record]
                    if source_record.is_absolute()
                    else [self.external_root / source_record, self.sd / source_record]
                )
                existing_source = next((candidate for candidate in source_candidates if candidate.is_file()), None)
                if existing_source is None:
                    self.error(
                        "SOURCE_RECORD_MISSING",
                        f"Source record is missing: {source_record_value}",
                        location,
                    )
                    valid = False
                else:
                    try:
                        actual_source_hash = sha256_file(existing_source)
                    except OSError as exc:
                        self.error("SOURCE_RECORD_READ", f"Could not hash source record: {exc}", str(existing_source))
                        valid = False
                    else:
                        if expected_source_hash and norm_hash(expected_source_hash) != actual_source_hash:
                            self.error(
                                "SOURCE_RECORD_HASH_MISMATCH",
                                "Selection source_record_sha256 does not match source-record bytes",
                                str(existing_source),
                            )
                            valid = False
                        if card_source_hash and norm_hash(card_source_hash) != actual_source_hash:
                            self.error(
                                "SOURCE_RECORD_HASH_MISMATCH",
                                "Card source_record_sha256 does not match source-record bytes",
                                str(existing_source),
                            )
                            valid = False

        reading = card.get("reading")
        if not isinstance(reading, dict):
            self.error("READING_FIELDS", "Completed card requires a reading object", location)
            return False
        if text_value(reading.get("access_level")) != "full_text_read":
            self.error("READING_ACCESS", "Completed card requires reading.access_level=full_text_read", location)
            valid = False
        for field in ("reader_role", "reader_model", "reasoning_effort", "read_at", "adjudication_status"):
            if not nonempty(reading.get(field)):
                self.error("READER_METADATA", f"Completed card requires reading.{field}", location)
                valid = False
        if text_value(reading.get("reader_role")).casefold() == "luna_primary":
            if text_value(reading.get("reader_model")) != "gpt-5.6-luna":
                self.error("READER_PROVENANCE", "luna_primary requires reader_model=gpt-5.6-luna", location)
                valid = False
            if text_value(reading.get("reasoning_effort")) != "max":
                self.error("READER_PROVENANCE", "luna_primary requires reasoning_effort=max", location)
                valid = False
        if row is not None and self.reader_policy:
            try:
                row_order = int(row.get("reading_order") or (row_index or 0) + 1)
                start_order = int(self.reader_policy.get("start_reading_order") or 0)
            except (TypeError, ValueError):
                row_order = 0
                start_order = 0
            expected_reader = self.reader_policy.get("expected_reader_provenance")
            if start_order and row_order >= start_order and isinstance(expected_reader, Mapping):
                for field in ("reader_role", "reader_model", "reasoning_effort"):
                    expected = text_value(expected_reader.get(field))
                    actual = text_value(reading.get(field))
                    if expected and actual != expected:
                        self.error(
                            "READER_POLICY",
                            f"reading_order {row_order} requires {field}={expected!r}, found {actual!r}",
                            location,
                        )
                        valid = False

        locators = reading.get("section_locators_read")
        if not isinstance(locators, list) or not locators:
            self.error("LOCATORS_EMPTY", "Completed card requires nonempty reading.section_locators_read", location)
            valid = False
        omissions = reading.get("omissions")
        if omissions is None:
            self.error("OMISSIONS_UNDECLARED", "Completed card must declare reading.omissions (use [] when none)", location)
            valid = False
        elif not isinstance(omissions, list):
            self.error("OMISSIONS_TYPE", "reading.omissions must be a list", location)
            valid = False
        elif omissions:
            self.error("OMISSIONS_PRESENT", "Completed card contains omissions; completion scope is not full text", location)
            valid = False

        pmcid = card_pmcid or text_value((row or {}).get("pmcid"))
        packet, _ = self.packet_path(row, pmcid)
        packet_text = ""
        packet_available = False
        if not pmcid:
            self.error("PACKET_PMCID", "Cannot resolve packet without PMCID", location)
            valid = False
        elif packet is None:
            self.error("PACKET_MISSING", f"Packet is missing: packets/{pmcid}.md", location)
            valid = False
        else:
            try:
                packet_text = packet.read_text(encoding="utf-8-sig", errors="replace")
                packet_available = True
            except OSError as exc:
                self.error("PACKET_READ", str(exc), str(packet))
                valid = False
            try:
                actual_packet_hash = sha256_file(packet)
            except OSError as exc:
                self.error("PACKET_READ", str(exc), str(packet))
                actual_packet_hash = ""
                valid = False
            declared_packet_hash = text_value(reading.get("packet_sha256"))
            if not declared_packet_hash:
                self.error("PACKET_HASH_MISSING", "Completed card requires reading.packet_sha256", location)
                valid = False
            elif actual_packet_hash and norm_hash(declared_packet_hash) != actual_packet_hash:
                self.error(
                    "PACKET_HASH_MISMATCH",
                    f"reading.packet_sha256 does not match packet bytes ({declared_packet_hash} != {actual_packet_hash})",
                    location,
                )
                valid = False
            elif not SHA256_RE.fullmatch(declared_packet_hash):
                self.error("PACKET_HASH_FORMAT", "reading.packet_sha256 is not a 64-hex SHA-256", location)
                valid = False
            row_packet_hash = text_value((row or {}).get("packet_sha256"))
            if not row_packet_hash:
                self.error("PACKET_HASH_MISSING", "Completed selection row requires packet_sha256", location)
                valid = False
            elif not SHA256_RE.fullmatch(row_packet_hash):
                self.error("PACKET_HASH_FORMAT", "Selection packet_sha256 is not a 64-hex SHA-256", location)
                valid = False
            elif norm_hash(row_packet_hash) != actual_packet_hash:
                self.error("PACKET_HASH_MISMATCH", "selection packet_sha256 does not match packet bytes", location)
                valid = False

        if packet_available:
            packet_source_match = PACKET_SOURCE_HASH_RE.search(packet_text)
            packet_source_hash = packet_source_match.group(1).casefold() if packet_source_match else ""
            if not packet_source_hash:
                self.error("PACKET_SOURCE_HASH_MISSING", "Packet metadata does not declare Source SHA-256", str(packet))
                valid = False
            else:
                for label, expected in (
                    ("selection", expected_source_hash),
                    ("card", card_source_hash),
                    ("source record", actual_source_hash),
                ):
                    if expected and packet_source_hash != norm_hash(expected):
                        self.error(
                            "PACKET_SOURCE_HASH_MISMATCH",
                            f"Packet Source SHA-256 does not match {label}",
                            str(packet),
                        )
                        valid = False
            explicitly_checked: set[str] = set()
            if isinstance(locators, list):
                explicitly_checked.update(text_value(value) for value in locators if text_value(value))
                if not self.check_locator_list(locators, packet_text, f"{location}:reading.section_locators_read", required=True):
                    valid = False
            packet_locators = packet_chunk_locators(packet_text)
            packet_set = set(packet_locators)
            if not packet_locators:
                self.error("PACKET_LOCATORS", "Packet contains no Sddd:Cdd chunk headings", location)
                valid = False
            else:
                packet_duplicates = sorted({item for item in packet_locators if packet_locators.count(item) > 1})
                if packet_duplicates:
                    self.error(
                        "PACKET_LOCATORS_DUPLICATE",
                        f"Packet repeats chunk headings: {', '.join(packet_duplicates)}",
                        str(packet),
                    )
                    valid = False
                sequence_error = packet_locator_sequence_error(packet_locators)
                if sequence_error:
                    self.error("PACKET_LOCATOR_SEQUENCE", sequence_error, str(packet))
                    valid = False
                declared = [text_value(value) for value in as_list(locators) if text_value(value)]
                declared_set = set(declared)
                duplicates = sorted({item for item in declared if declared.count(item) > 1})
                if duplicates:
                    self.error("LOCATORS_DUPLICATE", f"reading.section_locators_read repeats: {', '.join(duplicates)}", location)
                    valid = False
                missing = sorted(packet_set - declared_set)
                extra = sorted(declared_set - packet_set)
                if missing:
                    self.error("LOCATORS_INCOMPLETE", f"section_locators_read omits packet chunks: {', '.join(missing)}", location)
                    valid = False
                if extra:
                    self.error("LOCATORS_EXTRA", f"section_locators_read cites non-packet chunks: {', '.join(extra)}", location)
                    valid = False
                if not missing and not extra and declared != packet_locators:
                    self.error(
                        "LOCATORS_ORDER",
                        "section_locators_read must preserve exact packet heading order",
                        location,
                    )
                    valid = False
            # Every explicit Sddd:Cdd occurrence in the card (section moves,
            # capabilities, notes, etc.) is a citation and must be in packet.
            seen_locators: set[str] = set()
            for locator, json_path in extract_locators(card):
                if locator in seen_locators or locator in explicitly_checked:
                    continue
                seen_locators.add(locator)
                if locator not in packet_set:
                    self.error("LOCATOR_MISSING", f"Cited locator is not a packet chunk heading: {locator}", f"{location}:{json_path}")
                    valid = False

        section_moves = card.get("section_moves")
        if not isinstance(section_moves, list) or not section_moves:
            self.error("SECTION_MOVES", "Completed card requires nonempty section_moves", location)
            valid = False
        else:
            for index, move in enumerate(section_moves):
                move_location = f"{location}:section_moves[{index}]"
                if not isinstance(move, Mapping):
                    self.error("SECTION_MOVES", "Each section move must be an object", move_location)
                    valid = False
                    continue
                move_locator = move.get("locator")
                if not self.check_locator_list([move_locator], packet_text, move_location + ":locator", required=True):
                    valid = False
                if not nonempty(move.get("transferable_principle")):
                    self.error("SECTION_MOVES", "Section move requires transferable_principle", move_location)
                    valid = False

        limitations = card.get("limitations")
        if not isinstance(limitations, Mapping):
            self.error("LIMITATIONS", "Completed card requires a limitations object", location)
            valid = False
        elif "scope_boundary" not in limitations or not nonempty(limitations.get("scope_boundary")):
            self.error("LIMITATIONS", "limitations.scope_boundary is required (state when the scope is not reported)", location)
            valid = False

        # Core semantic-card groups are checked for presence and minimally
        # informative values.  This is structural completeness, not semantic
        # grading.
        study = card.get("study")
        if not isinstance(study, dict):
            self.error("STUDY_FIELDS", "Completed card requires a study object", location)
            valid = False
        else:
            for field in ("article_kind", "research_problem", "exact_gap", "objective_or_hypothesis", "study_design"):
                if not nonempty(study.get(field)):
                    self.error("STUDY_FIELDS", f"Completed card requires study.{field}", location)
                    valid = False
            for field in ("method_spine", "primary_outcomes"):
                if not isinstance(study.get(field), list) or not study.get(field):
                    self.error("STUDY_FIELDS", f"Completed card requires nonempty study.{field}", location)
                    valid = False

        argument = card.get("argument_map")
        if not isinstance(argument, dict):
            self.error("ARGUMENT_FIELDS", "Completed card requires an argument_map object", location)
            valid = False
        else:
            for field in ("central_claim", "warrant"):
                if not nonempty(argument.get(field)):
                    self.error("ARGUMENT_FIELDS", f"Completed card requires argument_map.{field}", location)
                    valid = False
            if not isinstance(argument.get("data"), list) or not argument.get("data"):
                self.error("ARGUMENT_FIELDS", "Completed card requires nonempty argument_map.data", location)
                valid = False

        boundary = card.get("evidence_boundary")
        if not isinstance(boundary, dict):
            self.error("EVIDENCE_BOUNDARY", "Completed card requires an evidence_boundary object", location)
            valid = False
        else:
            boundary_fields = ("measured", "predicted", "inferred", "recommended")
            for field in boundary_fields:
                if field not in boundary or not isinstance(boundary.get(field), list):
                    self.error("EVIDENCE_BOUNDARY", f"Completed card requires evidence_boundary.{field} list", location)
                    valid = False
            if not any(nonempty(boundary.get(field)) for field in boundary_fields):
                self.error("EVIDENCE_BOUNDARY", "evidence_boundary must contain at least one classified item", location)
                valid = False

        quality = card.get("quality")
        if not isinstance(quality, dict):
            self.error("QUALITY_FIELDS", "Completed card requires a quality object", location)
            valid = False
        else:
            quality_fields = (
                "research_question_design_alignment",
                "methods_transparency",
                "validation_credibility",
                "outcome_uncertainty_reporting",
                "comparator_fairness",
                "conclusion_proportionality",
                "overall_credibility",
            )
            for field in quality_fields:
                if not nonempty(quality.get(field)):
                    self.error("QUALITY_FIELDS", f"Completed card requires quality.{field}", location)
                    valid = False

        summary = first_value(card, "summary_zh", "summary", "summary_en")
        if not nonempty(summary):
            self.error("SUMMARY_FIELDS", "Completed card requires a nonempty summary_zh/summary field", location)
            valid = False

        candidates = card.get("writing_capability_candidates")
        if candidates is None:
            candidates = card.get("capability_candidates")
        if not isinstance(candidates, list) or not candidates:
            self.error("CAPABILITY_CANDIDATE", "Completed card requires at least one writing capability candidate", location)
            valid = False
        else:
            candidate_ok = False
            for index, candidate in enumerate(candidates):
                c_location = f"{location}:writing_capability_candidates[{index}]"
                if not isinstance(candidate, dict):
                    self.error("CAPABILITY_CANDIDATE", "Capability candidate must be an object", c_location)
                    continue
                if not nonempty(candidate.get("capability")):
                    self.error("CAPABILITY_CANDIDATE", "Capability candidate requires capability", c_location)
                confidence = text_value(candidate.get("confidence")).casefold()
                if confidence not in VALID_CONFIDENCE:
                    self.error("CAPABILITY_CONFIDENCE", "Capability candidate requires confidence high/moderate/low", c_location)
                supports = candidate.get("supporting_locators")
                if not isinstance(supports, list) or not supports:
                    self.error("CAPABILITY_LOCATORS", "Capability candidate requires supporting_locators", c_location)
                else:
                    support_ok = self.check_locator_list(supports, packet_text, c_location + ":supporting_locators") if packet_text else False
                    if support_ok and nonempty(candidate.get("capability")) and confidence in VALID_CONFIDENCE:
                        candidate_ok = True
            if not candidate_ok:
                self.error("CAPABILITY_CANDIDATE", "No capability candidate has valid supporting locators and confidence", location)
                valid = False

        if not valid:
            self.invalid_card_indices.add(card_index)
        return valid

    def match_and_validate_cards(self) -> None:
        # Validate status for all cards, and completed cards in detail.
        for card_index, (path, card) in enumerate(self.cards):
            if not card:
                continue
            kind = card_status_kind(card)
            if kind == "invalid":
                reading = card.get("reading") if isinstance(card.get("reading"), Mapping) else None
                status_value = reading.get("status") if reading is not None else card.get("status")
                self.error("READING_STATUS", f"Unknown reading.status/status: {status_value!r}", str(path))
                self.invalid_card_indices.add(card_index)

        # Rows without a card are pending by construction. A row matched to
        # multiple cards is invalid and does not get counted as completed.
        for row_index in range(len(self.selection)):
            matches = self.card_matches.get(row_index, [])
            if not matches:
                self.row_status[row_index] = "pending"
                self.warning("MISSING_CARD", "Selected paper has no semantic card yet", f"selection.jsonl:{self.selection[row_index]['_line']}")
                continue
            if len(matches) > 1:
                self.row_status[row_index] = "invalid"
                self.row_invalid.add(row_index)
                continue
            card_index = matches[0]
            _, card = self.cards[card_index]
            kind = card_status_kind(card) if isinstance(card, dict) else "invalid"
            if kind == "pending":
                self.row_status[row_index] = "pending"
            elif kind == "completed":
                self.row_status[row_index] = "completed"
                before = len(self.invalid_card_indices)
                self.validate_completed_card(card_index, row_index)
                if card_index in self.invalid_card_indices or len(self.invalid_card_indices) > before and card_index in self.invalid_card_indices:
                    self.row_invalid.add(row_index)
                    self.row_status[row_index] = "invalid"
            else:
                self.row_status[row_index] = "invalid"
                self.row_invalid.add(row_index)

        # Orphan completed cards still receive intrinsic checks so malformed
        # files are visible, but they cannot contribute to selected coverage.
        for card_index in sorted(self.orphan_card_indices):
            _, card = self.cards[card_index]
            if isinstance(card, dict) and card_status_kind(card) == "completed":
                self.validate_completed_card(card_index, None)

    def category_values(self, row: Mapping[str, Any], category: str, card: Mapping[str, Any] | None) -> list[str]:
        if category == "primary_stratum":
            value = first_value(row, "primary_stratum", "stratum")
            if value is None:
                value = row.get("strata")
        elif category == "article_kind":
            value = first_value(row, "article_kind")
            if value is None and isinstance(card, dict):
                value = (card.get("study") or {}).get("article_kind")
        else:
            value = first_value(row, "design_features", "design_feature")
            if value is None and isinstance(card, dict):
                value = (card.get("study") or {}).get("design_features")
        if isinstance(value, list):
            values = [text_value(item) for item in value if text_value(item)]
        else:
            values = [text_value(value)] if text_value(value) else []
        return values or ["(missing)"]

    def build_coverage(self) -> dict[str, dict[str, dict[str, int]]]:
        coverage: dict[str, dict[str, dict[str, int]]] = {
            "primary_stratum": {},
            "article_kind": {},
            "design_feature": {},
        }
        for row_index, row in enumerate(self.selection):
            card: Mapping[str, Any] | None = None
            matches = self.card_matches.get(row_index, [])
            if len(matches) == 1:
                card = self.cards[matches[0]][1]
            status = self.row_status.get(row_index, "pending")
            for category in coverage:
                for value in self.category_values(row, category, card):
                    bucket = coverage[category].setdefault(
                        value,
                        {"selected": 0, "completed": 0, "pending": 0, "invalid": 0},
                    )
                    bucket["selected"] += 1
                    if status in bucket:
                        bucket[status] += 1
        return coverage

    def locate_synthesis_files(self) -> None:
        # Keep this deliberately narrow: unrelated corpus JSONL files should
        # not be interpreted as promotion rules.  Builders have used both a
        # top-level semantic-distillation file and a synthesis/ subdirectory.
        names = {
            "synthesis.jsonl",
            "promoted-rules.jsonl",
            "promoted_rules.jsonl",
            "promoted-rules.json",
            "promoted_rules.json",
        }
        roots = [self.sd]
        if self.external_root != self.sd:
            roots.append(self.external_root)
        seen: set[Path] = set()
        for base in roots:
            if not base.exists():
                continue
            for path in sorted(base.rglob("*.jsonl")):
                if path in seen:
                    continue
                lowered = path.name.casefold()
                relative_parts = {part.casefold() for part in path.relative_to(base).parts[:-1]}
                if lowered in names or "promot" in lowered or ("synth" in lowered and "cards" not in relative_parts and "packets" not in relative_parts):
                    self.synthesis_files.append(path)
                    seen.add(path)
            for path in sorted(base.glob("*.json")):
                if path in seen:
                    continue
                lowered = path.name.casefold()
                if lowered in names:
                    self.synthesis_files.append(path)
                    seen.add(path)

    def load_and_validate_synthesis(self) -> dict[str, Any]:
        if not self.synthesis_files:
            return {"files": [], "rules": 0}
        cards_by_id: dict[str, tuple[int, dict[str, Any]]] = {}
        cards_by_paper: dict[str, tuple[int, dict[str, Any]]] = {}
        for index, (_, card) in enumerate(self.cards):
            if not isinstance(card, dict):
                continue
            card_id = norm_id(card.get("card_id"))
            if card_id:
                cards_by_id[card_id] = (index, card)
            for key in ("pmcid", "paper_id"):
                value = norm_id(card.get(key))
                if value:
                    cards_by_paper[value] = (index, card)
        rules = 0
        parsed_files: list[str] = []
        for path in self.synthesis_files:
            parsed_files.append(str(path))
            try:
                lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
            except OSError as exc:
                self.error("SYNTHESIS_READ", str(exc), str(path))
                continue
            for line_number, line in enumerate(lines, 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    self.error("SYNTHESIS_JSON", f"Invalid JSON: {exc.msg}", f"{path}:{line_number}")
                    continue
                if not isinstance(value, dict):
                    self.error("SYNTHESIS_ROW", "Promoted rule must be a JSON object", f"{path}:{line_number}")
                    continue
                rules += 1
                self.synthesis_rows.append(value)
                location = f"{path}:{line_number}"
                recurrence_value = first_value(value, "recurrence", "support_count", "recurrence_count")
                try:
                    recurrence = int(recurrence_value) if recurrence_value is not None else 0
                except (TypeError, ValueError):
                    recurrence = 0
                support_values = first_value(value, "supporting_card_ids", "supporting_cards", "card_ids", "supporting_papers")
                supporting_ids: list[str] = []
                for item in as_list(support_values):
                    if isinstance(item, Mapping):
                        item_id = first_value(item, "card_id", "pmcid", "paper_id", "id")
                    else:
                        item_id = item
                    if text_value(item_id):
                        supporting_ids.append(text_value(item_id))
                if recurrence == 0 and supporting_ids:
                    recurrence = len(supporting_ids)
                if recurrence < 3:
                    self.error("SYNTHESIS_RECURRENCE", "Promoted rule requires recurrence >= 3", location)

                journals_value = first_value(value, "journals", "supporting_journals", "journal_set")
                journals = {text_value(item).casefold() for item in as_list(journals_value) if text_value(item)}
                resolved_cards: list[tuple[int, dict[str, Any]]] = []
                missing_ids: list[str] = []
                for support_id in supporting_ids:
                    token = norm_id(support_id)
                    pair = cards_by_id.get(token) or cards_by_paper.get(token)
                    if pair is None and token.startswith("semantic:"):
                        pair = cards_by_paper.get(token.split(":", 1)[1])
                    if pair is None:
                        missing_ids.append(support_id)
                    else:
                        resolved_cards.append(pair)
                        journal = text_value((pair[1].get("bibliography") or {}).get("journal"))
                        if journal:
                            journals.add(journal.casefold())
                if missing_ids or not supporting_ids:
                    self.error(
                        "SYNTHESIS_TRACEABILITY",
                        "Promoted rule requires supporting card IDs that resolve to cards" + (f" (missing: {', '.join(missing_ids)})" if missing_ids else ""),
                        location,
                    )
                # A promoted rule must retain chunk-level provenance.  Each
                # locator names its PMCID explicitly, and that PMCID must be
                # one of the traceable supporting cards and its packet.
                locator_values = first_value(value, "supporting_locators", "locators", "evidence_locators")
                supporting_locators = [text_value(item) for item in as_list(locator_values) if text_value(item)]
                if not supporting_locators:
                    self.error("SYNTHESIS_LOCATORS", "Promoted rule requires nonempty supporting_locators", location)
                resolved_pmcids: set[str] = set()
                for card_index, card in resolved_cards:
                    resolved_pmcids.add(card_identity_value(card, "pmcid").casefold())
                    if card_status_kind(card) != "completed" or card_index in self.invalid_card_indices:
                        self.error(
                            "SYNTHESIS_SUPPORT_STATUS",
                            f"Supporting card is not a valid completed card: {card.get('card_id') or card_identity_value(card, 'pmcid')}",
                            location,
                        )
                for locator in supporting_locators:
                    locator_match = PROMOTED_LOCATOR_RE.fullmatch(locator)
                    if not locator_match:
                        self.error("SYNTHESIS_LOCATOR_FORMAT", f"Invalid promoted-rule locator (expected PMCID:Sddd:Cdd): {locator}", location)
                        continue
                    pmcid = locator_match.group(1)
                    if pmcid.casefold() not in resolved_pmcids:
                        self.error(
                            "SYNTHESIS_LOCATOR_CARD",
                            f"Promoted-rule locator PMCID is not listed in supporting cards: {pmcid}",
                            location,
                        )
                    packet = self.sd / "packets" / f"{pmcid}.md"
                    if not packet.exists():
                        self.error("SYNTHESIS_LOCATOR_PACKET", f"Supporting packet is missing: packets/{pmcid}.md", location)
                    else:
                        try:
                            packet_text = packet.read_text(encoding="utf-8-sig", errors="replace")
                        except OSError as exc:
                            self.error("SYNTHESIS_LOCATOR_PACKET", str(exc), location)
                            continue
                        packet_locator = locator.split(":", 1)[1]
                        if packet_locator not in set(packet_chunk_locators(packet_text)):
                            self.error(
                                "SYNTHESIS_LOCATOR_MISSING",
                                f"Promoted-rule locator is not a packet chunk heading: {locator}",
                                location,
                            )
                if len(journals) < 2:
                    self.error("SYNTHESIS_JOURNALS", "Promoted rule requires support from at least two journals", location)
                counterexample = first_value(value, "counterexample_or_boundary", "counterexample", "boundary", "failure_mode")
                if not nonempty(counterexample):
                    self.error("SYNTHESIS_COUNTEREXAMPLE", "Promoted rule requires a counterexample or explicit boundary", location)
                scope = first_value(value, "scope", "applicability_scope", "where_it_applies")
                if not nonempty(scope):
                    self.error("SYNTHESIS_SCOPE", "Promoted rule requires a scope", location)
        return {"files": parsed_files, "rules": rules}

    def report(self) -> dict[str, Any]:
        self.load_selection()
        self.load_cards()
        self.match_and_validate_cards()
        self.locate_synthesis_files()
        synthesis = self.load_and_validate_synthesis()

        selected = len(self.selection)
        completed = sum(1 for status in self.row_status.values() if status == "completed")
        pending = sum(1 for status in self.row_status.values() if status == "pending")
        matched_card_indices = {card_index for matches in self.card_matches.values() for card_index in matches}
        orphan_invalid = self.invalid_card_indices - matched_card_indices
        invalid = len(self.row_invalid) + len(orphan_invalid) + self.selection_parse_errors
        orphan = len(self.orphan_card_indices)
        # A populated pending stub is useful queue state, but it is not a
        # completed semantic read.  Emit one aggregate warning so non-strict
        # runs report honest progress without producing thousands of lines;
        # strict mode consequently passes only at full completion.
        if pending:
            self.warning(
                "PENDING_ROWS",
                f"{pending} selected paper(s) still await completed semantic reading",
                str(self.sd / "selection.jsonl"),
            )
        errors = [item for item in self.issues if item["severity"] == "ERROR"]
        warnings = [item for item in self.issues if item["severity"] == "WARNING"]
        if errors:
            verdict = "FAIL"
        elif warnings and self.strict:
            verdict = "FAIL"
        elif warnings:
            verdict = "PASS_WITH_WARNINGS"
        else:
            verdict = "PASS"
        report: dict[str, Any] = {
            "schema_version": "1.0",
            "validator": "validate_semantic_distillation.py",
            "scope": "structural validation only; semantic correctness is not assessed",
            "semantic_correctness_assessed": False,
            "root": str(self.external_root),
            "semantic_distillation": str(self.sd),
            "verdict": verdict,
            "strict": self.strict,
            "counts": {
                "selected": selected,
                "completed": completed,
                "pending": pending,
                "invalid": invalid,
                "orphan": orphan,
                "cards": len(self.cards),
                "completed_cards": sum(1 for _, card in self.cards if isinstance(card, dict) and card_status_kind(card) == "completed"),
                "pending_cards": sum(1 for _, card in self.cards if isinstance(card, dict) and card_status_kind(card) == "pending"),
                "invalid_cards": len(self.invalid_card_indices),
                "orphan_cards": orphan,
                "selection_parse_errors": self.selection_parse_errors,
                "errors": len(errors),
                "warnings": len(warnings),
            },
            "coverage": self.build_coverage(),
            "synthesis": synthesis,
            "errors": errors,
            "warnings": warnings,
            "issues": self.issues,
        }
        return report


def write_report(report: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    validator = SemanticValidator(args.root, strict=args.strict)
    report = validator.report()
    report_path = validator.sd / "reports" / "validation.json"
    try:
        write_report(report, report_path)
    except OSError as exc:
        # Keep stdout useful even when report persistence is unavailable.
        validator.error("REPORT_WRITE", str(exc), str(report_path))
        write_issue = validator.issues[-1]
        report = dict(report)
        report["verdict"] = "FAIL"
        report["errors"] = [*report.get("errors", []), write_issue]
        report["issues"] = [*report.get("issues", []), write_issue]
        counts = dict(report.get("counts", {}))
        counts["errors"] = int(counts.get("errors", 0)) + 1
        report["counts"] = counts

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{report['verdict']}: structural semantic-distillation validation")
        print(json.dumps(report["counts"], ensure_ascii=False, sort_keys=True))
        for item in report["issues"]:
            location = f" [{item['location']}]" if item.get("location") else ""
            print(f"- {item['severity']} {item['code']}: {item['message']}{location}")
        print(f"Report: {report_path}")

    errors = int(report.get("counts", {}).get("errors", 0))
    warnings = int(report.get("counts", {}).get("warnings", 0))
    return 1 if errors or (args.strict and warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
