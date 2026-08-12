from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "1.0"
MAX_SAFE_BATCH_SIZE = 100
DYNAMIC_BLOCKERS = {
    "active_lease",
    "pending_overlay_or_draft",
    "reading_checkpoint_present",
}
COMPLETED_STATUSES = {"completed", "complete", "done", "read"}


class WatcherError(RuntimeError):
    """A safety or integrity failure that must stop unattended migration."""


class TransientCycleError(RuntimeError):
    """A temporary local-sync/read condition that may be retried later."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_signature(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"exists": False}
    except OSError as exc:
        return {"exists": False, "error": type(exc).__name__}
    return {"exists": True, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise TransientCycleError(f"could not read {path}: {exc}") from exc
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TransientCycleError(f"invalid JSONL at {path}:{number}: {exc}") from exc
        if not isinstance(value, dict):
            raise TransientCycleError(f"non-object JSONL row at {path}:{number}")
        rows.append(value)
    return rows


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        last_error: OSError | None = None
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                return
            except PermissionError as exc:
                last_error = exc
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
        if last_error is not None:
            raise last_error
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()


class SingleInstanceLock:
    """Hold a one-byte OS lock for the lifetime of a watcher process."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.stream: Any = None

    def __enter__(self) -> "SingleInstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        self.stream.seek(0, os.SEEK_END)
        if self.stream.tell() == 0:
            self.stream.write(b"\0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            self.stream = None
            raise WatcherError(f"another semantic-packet migration watcher holds {self.path}") from exc
        return self

    def __exit__(self, *_exc: Any) -> None:
        if self.stream is None:
            return
        try:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()
            self.stream = None


@dataclass(frozen=True)
class Paths:
    project_root: Path
    corpus_root: Path
    semantic_root: Path
    mineru_export: Path
    pdf_manifest: Path
    pdf_root: Path
    migrator: Path
    skill_scripts: Path
    state_path: Path
    log_path: Path
    lock_path: Path


def default_paths() -> Paths:
    project_root = Path(__file__).resolve().parents[2]
    benchmark_root = project_root / "pdf-parser-benchmark"
    corpus_root = project_root / "biomechanics-corpus"
    return Paths(
        project_root=project_root,
        corpus_root=corpus_root,
        semantic_root=corpus_root / "semantic-distillation",
        mineru_export=benchmark_root / "exports" / "corpus10000-prod" / "mineru",
        pdf_manifest=benchmark_root / "data" / "corpus-10000-manifest.jsonl",
        pdf_root=benchmark_root / "corpus-10000",
        migrator=project_root / "distill-biomechanics-papers" / "scripts" / "prepare_mineru_semantic_packets.py",
        skill_scripts=project_root / "distill-biomechanics-papers" / "scripts",
        state_path=benchmark_root / "logs" / "semantic-packet-migration-watcher-state.json",
        log_path=benchmark_root / "logs" / "semantic-packet-migration-watcher.jsonl",
        lock_path=benchmark_root / "logs" / "semantic-packet-migration-watcher.lock",
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    defaults = default_paths()
    parser = argparse.ArgumentParser(
        description=(
            "Continuously migrate newly synced, verified MinerU Markdown into pending Luna reading packets. "
            "Every write transaction is capped at 100 papers and post-validated."
        )
    )
    parser.add_argument("--corpus-root", type=Path, default=defaults.corpus_root)
    parser.add_argument("--mineru-export", type=Path, default=defaults.mineru_export)
    parser.add_argument("--pdf-manifest", type=Path, default=defaults.pdf_manifest)
    parser.add_argument("--pdf-root", type=Path, default=defaults.pdf_root)
    parser.add_argument("--migrator", type=Path, default=defaults.migrator)
    parser.add_argument("--skill-scripts", type=Path, default=defaults.skill_scripts)
    parser.add_argument("--state", type=Path, default=defaults.state_path)
    parser.add_argument("--log", type=Path, default=defaults.log_path)
    parser.add_argument("--lock", type=Path, default=defaults.lock_path)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-batches-per-cycle", type=int, default=25)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--write", action="store_true", help="Commit migrations; otherwise run candidate batches as dry-runs")
    parser.add_argument("--json", action="store_true", help="Print the final cycle/state JSON")
    args = parser.parse_args(argv)
    if args.batch_size < 1 or args.batch_size > MAX_SAFE_BATCH_SIZE:
        parser.error(f"--batch-size must be between 1 and {MAX_SAFE_BATCH_SIZE}")
    if args.max_batches_per_cycle < 1:
        parser.error("--max-batches-per-cycle must be at least 1")
    if args.interval_seconds < 5 and not args.once:
        parser.error("--interval-seconds must be at least 5 for continuous operation")
    return args


def build_paths(args: argparse.Namespace) -> Paths:
    defaults = default_paths()
    corpus_root = args.corpus_root.expanduser().resolve()
    return Paths(
        project_root=defaults.project_root,
        corpus_root=corpus_root,
        semantic_root=corpus_root / "semantic-distillation",
        mineru_export=args.mineru_export.expanduser().resolve(),
        pdf_manifest=args.pdf_manifest.expanduser().resolve(),
        pdf_root=args.pdf_root.expanduser().resolve(),
        migrator=args.migrator.expanduser().resolve(),
        skill_scripts=args.skill_scripts.expanduser().resolve(),
        state_path=args.state.expanduser().resolve(),
        log_path=args.log.expanduser().resolve(),
        lock_path=args.lock.expanduser().resolve(),
    )


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "NEW",
            "cycles": 0,
            "blocked": {},
            "created_at": utc_now(),
        }
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WatcherError(f"watcher state is unreadable: {path}: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("blocked", {}), dict):
        raise WatcherError(f"watcher state has an invalid shape: {path}")
    return value


def is_completed_card(card_path: Path) -> bool:
    try:
        value = json.loads(card_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return False
    reading = value.get("reading") if isinstance(value.get("reading"), Mapping) else {}
    statuses = {
        str(reading.get("status") or "").casefold(),
        str(value.get("status") or "").casefold(),
    }
    return bool(statuses & COMPLETED_STATUSES)


def pdf_path_for_row(pdf_root: Path, pmcid: str, row: Mapping[str, Any]) -> Path:
    relative = str(row.get("pdf_relpath") or row.get("local_pdf_relpath") or f"pdfs/{pmcid}.pdf")
    candidate = Path(relative)
    if candidate.is_absolute():
        return candidate
    return (pdf_root / candidate).resolve()


def material_fingerprint(
    pmcid: str,
    selection_row: Mapping[str, Any],
    index_row: Mapping[str, Any],
    pdf_row: Mapping[str, Any],
    paths: Paths,
    migrator_hash: str,
    checkpoint_ids: set[str],
) -> str:
    card_path = paths.semantic_root / "cards" / f"{pmcid}.json"
    overlay_path = paths.semantic_root / "overlays" / f"{pmcid}.json"
    draft_path = paths.semantic_root / "overlays" / f"{pmcid}.json.draft"
    md_relative = str(index_row.get("preferred_markdown_relpath") or "")
    md_path = (paths.mineru_export / Path(md_relative)).resolve() if md_relative else paths.mineru_export / ".missing"
    pdf_path = pdf_path_for_row(paths.pdf_root, pmcid, pdf_row)
    card_hash = sha256_file(card_path) if card_path.is_file() else ""
    overlay_hash = sha256_file(overlay_path) if overlay_path.is_file() else ""
    draft_hash = sha256_file(draft_path) if draft_path.is_file() else ""
    value = {
        "pmcid": pmcid,
        "migrator_sha256": migrator_hash,
        "selection_source_sha256": selection_row.get("source_record_sha256") or selection_row.get("source_hash"),
        "selection_packet_sha256": selection_row.get("packet_sha256"),
        "card_sha256": card_hash,
        "overlay_sha256": overlay_hash,
        "draft_sha256": draft_hash,
        "checkpoint": pmcid in checkpoint_ids,
        "preferred_markdown_relpath": md_relative,
        "preferred_markdown_sha256": index_row.get("preferred_markdown_sha256"),
        "page_count": index_row.get("page_count"),
        "mineru_input_sha256": index_row.get("input_sha256"),
        "mineru_input_kind": index_row.get("input_kind"),
        "markdown_file": {
            **file_signature(md_path),
            "sha256": sha256_file(md_path) if md_path.is_file() else "",
        },
        "pdf_manifest_sha256": pdf_row.get("pdf_sha256"),
        "pdf_input_kind": pdf_row.get("input_kind"),
        "pdf_file": file_signature(pdf_path),
    }
    return sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def reasons_are_dynamic(reasons: Sequence[str]) -> bool:
    return any(str(reason).split(":", 1)[0] in DYNAMIC_BLOCKERS for reason in reasons)


def candidate_pmcids(
    selection: Sequence[Mapping[str, Any]],
    index_by_id: Mapping[str, Mapping[str, Any]],
    pdf_by_id: Mapping[str, Mapping[str, Any]],
    blocked_state: Mapping[str, Any],
    fingerprints: Mapping[str, str],
) -> list[str]:
    candidates: list[tuple[int, int, str]] = []
    now_epoch = time.time()
    for index, row in enumerate(selection):
        pmcid = str(row.get("pmcid") or "").upper()
        idx = index_by_id.get(pmcid)
        pdf = pdf_by_id.get(pmcid)
        if not idx or not pdf or not str(idx.get("preferred_markdown_sha256") or ""):
            continue
        # ``fingerprints`` is deliberately built only for non-completed,
        # non-migrated pending rows.  Completed rows may have preferred
        # Markdown too, but must never keep their 100-row block hot forever.
        if pmcid not in fingerprints:
            continue
        if str(row.get("source_format") or "") == "mineru_markdown":
            continue
        current = fingerprints.get(pmcid, "")
        previous = blocked_state.get(pmcid) if isinstance(blocked_state.get(pmcid), Mapping) else {}
        previous_reasons = previous.get("reasons") if isinstance(previous.get("reasons"), list) else []
        fingerprint_changed = previous.get("fingerprint") != current
        if not fingerprint_changed:
            if not reasons_are_dynamic(previous_reasons):
                continue
            retry_after = float(previous.get("next_retry_epoch") or 0.0)
            if retry_after > now_epoch:
                continue
        # New/changed material is processed before unchanged dynamic blockers.
        priority = 0 if fingerprint_changed else 1
        candidates.append((priority, index, pmcid))
    candidates.sort()
    return [pmcid for _priority, _index, pmcid in candidates]


def run_migrator(
    args: argparse.Namespace,
    paths: Paths,
    *,
    pmcids: Sequence[str] = (),
    offset: int = 0,
    limit: int = 0,
) -> dict[str, Any]:
    command = [
        str(args.python),
        str(paths.migrator),
        "--root",
        str(paths.corpus_root),
        "--mineru-export",
        str(paths.mineru_export),
        "--pdf-manifest",
        str(paths.pdf_manifest),
        "--pdf-root",
        str(paths.pdf_root),
        "--max-write-count",
        str(MAX_SAFE_BATCH_SIZE),
        "--json",
    ]
    if pmcids:
        for pmcid in pmcids:
            command.extend(["--pmcid", pmcid])
    else:
        command.extend(["--offset", str(offset), "--limit", str(limit)])
    if args.write:
        command.append("--write")
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(
            command,
            cwd=str(paths.project_root),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30 * 60,
            check=False,
        )
    except (subprocess.TimeoutExpired, PermissionError, OSError) as exc:
        raise TransientCycleError(
            f"migrator invocation could not finish for {list(pmcids) or [offset, limit]}: {exc}"
        ) from exc
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise WatcherError(
            f"migrator emitted invalid JSON for {list(pmcids) or [offset, limit]} (exit={completed.returncode}): "
            f"{completed.stderr[-2000:]}"
        ) from exc
    if not isinstance(payload, dict):
        raise WatcherError(f"migrator emitted a non-object result for {list(pmcids) or [offset, limit]}")
    if completed.returncode not in (0, 2) or payload.get("error"):
        message = str(payload.get("error") or completed.stderr[-2000:])
        transient_markers = (
            "selection changed",
            "card changed during migration",
            "completed card became visible",
            "active reading material appeared",
            "source changed during migration",
            "same 10,000 unique pmcid",
            "could not read",
            "permissionerror",
            "sharing violation",
            "timed out waiting for manager lock",
        )
        if any(marker in message.casefold() for marker in transient_markers):
            raise TransientCycleError(
                f"migrator snapshot changed for {list(pmcids) or [offset, limit]}: {message}"
            )
        raise WatcherError(
            f"migrator stopped for {list(pmcids) or [offset, limit]} (exit={completed.returncode}): {message}"
        )
    return payload


def post_validate(paths: Paths, written_ids: Sequence[str]) -> dict[str, Any]:
    if str(paths.skill_scripts) not in sys.path:
        sys.path.insert(0, str(paths.skill_scripts))
    from manage_semantic_reading import (
        CorpusSnapshot,
        ManagerError,
        ReadingRow,
        StateLock,
        prepared_migration_ids,
        verify_pending_material,
    )
    from validate_semantic_distillation import SemanticValidator

    # Validate one coherent filesystem snapshot.  This is the same lock used
    # by the migrator and Luna lease manager, so another legal transaction
    # cannot appear half-written to this audit.
    try:
        with StateLock(paths.semantic_root / "luna-state", timeout=60.0):
            validator = SemanticValidator(paths.corpus_root, strict=False)
            report = validator.report()
            selected_by_id = {
                str(row.get("pmcid") or "").casefold(): (index, row)
                for index, row in enumerate(validator.selection)
            }
            snapshot = CorpusSnapshot(
                root=paths.corpus_root,
                sd=paths.semantic_root,
                rows=[],
                invalid_count=0,
                validator_counts=dict(report.get("counts") or {}),
                validator_issues=list(report.get("issues") or []),
            )
            material_issues: dict[str, list[str]] = {}
            for pmcid in written_ids:
                selected = selected_by_id.get(pmcid.casefold())
                if selected is None:
                    material_issues[pmcid] = ["selection_row_missing_after_write"]
                    continue
                index, source_row = selected
                row = ReadingRow(
                    index=index,
                    pmcid=pmcid,
                    paper_id=str(source_row.get("paper_id") or ""),
                    title=str(source_row.get("title") or ""),
                    journal=str(source_row.get("journal") or ""),
                    year=source_row.get("year"),
                    primary_stratum=str(source_row.get("primary_stratum") or ""),
                    reading_order=int(source_row.get("reading_order") or index + 1),
                    status="remaining",
                    packet_present=(paths.semantic_root / "packets" / f"{pmcid}.md").is_file(),
                    row=source_row,
                )
                issues = verify_pending_material(snapshot, row)
                if issues:
                    material_issues[pmcid] = issues
            prepared_ids, unreadable_transaction = prepared_migration_ids(paths.semantic_root)
    except ManagerError as exc:
        raise TransientCycleError(f"post-validation could not acquire a consistent semantic snapshot: {exc}") from exc
    counts = report.get("counts") if isinstance(report.get("counts"), Mapping) else {}
    result = {
        "written_ids": list(written_ids),
        "material_issues": material_issues,
        "prepared_transaction_pmcids": sorted(prepared_ids),
        "transaction_unreadable": bool(unreadable_transaction),
        "validator_verdict": report.get("verdict"),
        "validator_counts": dict(counts),
    }
    if material_issues:
        raise WatcherError(f"post-write pending-material validation failed: {material_issues}")
    if prepared_ids or unreadable_transaction:
        raise WatcherError("post-write transaction audit found an uncommitted or unreadable migration transaction")
    if int(counts.get("errors") or 0) or int(counts.get("invalid") or 0) or int(counts.get("orphan") or 0):
        raise WatcherError(f"post-write global structural validation failed: {result}")
    return result


def checkpoint_ids(semantic_root: Path) -> set[str]:
    values: set[str] = set()
    for path in (semantic_root / "luna-state").rglob("*.progress"):
        match = re.search(r"PMC\d+", path.name, re.I)
        if match:
            values.add(match.group(0).upper())
    return values


def validate_identity_sets(
    selection: Sequence[Mapping[str, Any]],
    index_rows: Sequence[Mapping[str, Any]],
    pdf_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    selection_ids = [str(row.get("pmcid") or "").upper() for row in selection]
    index_by_id = {str(row.get("pmcid") or row.get("sample_id") or "").upper(): row for row in index_rows}
    pdf_by_id = {str(row.get("pmcid") or row.get("sample_id") or "").upper(): row for row in pdf_rows}
    if (
        len(selection) != 10000
        or len(index_rows) != 10000
        or len(pdf_rows) != 10000
        or len(set(selection_ids)) != 10000
        or len(index_by_id) != 10000
        or len(pdf_by_id) != 10000
        or set(selection_ids) != set(index_by_id)
        or set(selection_ids) != set(pdf_by_id)
    ):
        raise TransientCycleError("selection, MinerU index, and PDF manifest are not the same 10,000 unique PMCIDs")
    return index_by_id, pdf_by_id


def conversion_status(paths: Paths) -> str:
    summary = paths.mineru_export / "summary.json"
    if not summary.is_file():
        return "UNKNOWN"
    try:
        value = json.loads(summary.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return "UNKNOWN"
    return str(value.get("status") or "UNKNOWN")


def run_cycle(args: argparse.Namespace, paths: Paths, state: dict[str, Any]) -> dict[str, Any]:
    # A zero-length write invocation performs durable transaction recovery
    # before any fresh snapshots are planned.  Recovery conflicts are fatal.
    if args.write:
        recovery = run_migrator(args, paths, offset=0, limit=0)
        recovered_transactions = recovery.get("recovered_transactions") or []
        # Recovery and ledger-only repairs are writes too.  Validate the
        # transaction/global state even when no fresh paper was migrated.
        recovery_audit = post_validate(paths, [])
    else:
        recovered_transactions = []
        recovery_audit = None

    selection_path = paths.semantic_root / "selection.jsonl"
    selection = load_jsonl(selection_path)
    index_rows = load_jsonl(paths.mineru_export / "index.jsonl")
    pdf_rows = load_jsonl(paths.pdf_manifest)
    index_by_id, pdf_by_id = validate_identity_sets(selection, index_rows, pdf_rows)
    migrator_hash = sha256_file(paths.migrator)
    checkpoints = checkpoint_ids(paths.semantic_root)
    fingerprints: dict[str, str] = {}
    completed_count = 0
    migrated_count = 0
    preferred_count = 0
    primary_count = 0
    fallback_count = 0

    for index_row in index_rows:
        if index_row.get("preferred_markdown_sha256"):
            preferred_count += 1
        if index_row.get("primary_markdown_sha256"):
            primary_count += 1
        if index_row.get("fallback_markdown_sha256"):
            fallback_count += 1

    for row in selection:
        pmcid = str(row.get("pmcid") or "").upper()
        card_path = paths.semantic_root / "cards" / f"{pmcid}.json"
        if is_completed_card(card_path):
            completed_count += 1
            continue
        idx = index_by_id[pmcid]
        pdf = pdf_by_id[pmcid]
        preferred_hash = str(idx.get("preferred_markdown_sha256") or "").casefold()
        if str(row.get("source_format") or "") == "mineru_markdown":
            migrated_count += 1
            selection_hash = str(row.get("source_record_sha256") or row.get("source_hash") or "").casefold()
            if preferred_hash and selection_hash != preferred_hash:
                raise WatcherError(
                    f"preferred Markdown changed after packet migration for {pmcid}; stop and audit before replacing reading material"
                )
            continue
        if preferred_hash:
            fingerprints[pmcid] = material_fingerprint(
                pmcid, row, idx, pdf, paths, migrator_hash, checkpoints
            )

    blocked_state = state.get("blocked") if isinstance(state.get("blocked"), Mapping) else {}
    candidates = candidate_pmcids(
        selection,
        index_by_id,
        pdf_by_id,
        blocked_state,
        fingerprints,
    )
    maximum = args.batch_size * args.max_batches_per_cycle
    selected_candidates = candidates[:maximum]
    batches = [
        selected_candidates[index : index + args.batch_size]
        for index in range(0, len(selected_candidates), args.batch_size)
    ]
    cycle_written: list[str] = []
    cycle_blocked: dict[str, dict[str, Any]] = {}
    batch_reports: list[dict[str, Any]] = []

    for batch_pmcids in batches:
        result = run_migrator(args, paths, pmcids=batch_pmcids)
        counts = result.get("counts") if isinstance(result.get("counts"), Mapping) else {}
        written_ids = [
            str(item.get("pmcid") or "").upper()
            for item in result.get("items", [])
            if isinstance(item, Mapping) and item.get("status") == "written"
        ]
        for item in result.get("items", []):
            if not isinstance(item, Mapping):
                continue
            pmcid = str(item.get("pmcid") or "").upper()
            status = str(item.get("status") or "")
            if status == "blocked" and pmcid in fingerprints:
                cycle_blocked[pmcid] = {
                    "fingerprint": fingerprints[pmcid],
                    "reasons": [str(reason) for reason in item.get("reasons", [])],
                    "last_attempt_at": utc_now(),
                    "next_retry_epoch": (
                        time.time() + max(900, args.interval_seconds * 3)
                        if reasons_are_dynamic([str(reason) for reason in item.get("reasons", [])])
                        else 0
                    ),
                }
            elif status in {"written", "already_migrated", "completed_frozen"}:
                state.setdefault("blocked", {}).pop(pmcid, None)
        audit: dict[str, Any] | None = None
        if args.write:
            audit = post_validate(paths, written_ids)
        cycle_written.extend(written_ids)
        batch_reports.append(
            {
                "pmcids": list(batch_pmcids),
                "limit": len(batch_pmcids),
                "counts": dict(counts),
                "blocking_reason_counts": result.get("blocking_reason_counts") or {},
                "written_ids": written_ids,
                "post_validation": audit,
            }
        )

    state_blocked = state.setdefault("blocked", {})
    state_blocked.update(cycle_blocked)
    # Reload the canonical selection after all subprocesses; this keeps state
    # cleanup aligned with the transaction-committed view, not the cycle's
    # planning snapshot.
    current_selection = load_jsonl(paths.semantic_root / "selection.jsonl")
    live_non_migrated = {
        str(row.get("pmcid") or "").upper()
        for row in current_selection
        if str(row.get("source_format") or "") != "mineru_markdown"
    }
    for pmcid in list(state_blocked):
        if pmcid not in live_non_migrated:
            state_blocked.pop(pmcid, None)

    status = "CATCHING_UP" if len(candidates) > len(selected_candidates) else "WAITING_FOR_MARKDOWN"
    if batches:
        status = "MIGRATED_BATCHES" if cycle_written else "QUALITY_GATED"
    state.update(
        {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "pid": os.getpid(),
            "updated_at": utc_now(),
            "cycles": int(state.get("cycles") or 0) + 1,
            "write_enabled": bool(args.write),
            "last_cycle": {
                "conversion_status": conversion_status(paths),
                "selected": len(selection),
                "preferred_markdown": preferred_count,
                "primary_markdown": primary_count,
                "fallback_markdown": fallback_count,
                "completed_semantic_cards": completed_count,
                "migrated_pending_materials_before_cycle": migrated_count,
                "written_this_cycle": len(cycle_written),
                "blocked_fingerprints": len(state_blocked),
                "candidate_papers": len(candidates),
                "processed_papers": len(selected_candidates),
                "remaining_candidate_papers": max(0, len(candidates) - len(selected_candidates)),
                "recovered_transactions": recovered_transactions,
                "recovery_validation": recovery_audit,
                "batches": batch_reports,
            },
        }
    )
    state.pop("last_error", None)
    state.pop("traceback", None)
    return state


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    paths = build_paths(args)
    try:
        with SingleInstanceLock(paths.lock_path):
            state = load_state(paths.state_path)
            while True:
                try:
                    state = run_cycle(args, paths, state)
                    atomic_json(paths.state_path, state)
                    append_jsonl(paths.log_path, {"event": "cycle", **state.get("last_cycle", {}), "at": utc_now()})
                except TransientCycleError as exc:
                    state.update(
                        {
                            "status": "RETRYING_TRANSIENT_ERROR",
                            "pid": os.getpid(),
                            "updated_at": utc_now(),
                            "last_error": str(exc),
                        }
                    )
                    atomic_json(paths.state_path, state)
                    append_jsonl(paths.log_path, {"event": "transient_error", "error": str(exc), "at": utc_now()})
                    if args.once:
                        if args.json:
                            print(json.dumps(state, ensure_ascii=False, indent=2))
                        return 2
                except Exception as exc:
                    state.update(
                        {
                            "status": "STOPPED_ON_ERROR",
                            "pid": os.getpid(),
                            "updated_at": utc_now(),
                            "last_error": str(exc),
                            "traceback": traceback.format_exc(),
                        }
                    )
                    atomic_json(paths.state_path, state)
                    append_jsonl(paths.log_path, {"event": "stopped_on_error", "error": str(exc), "at": utc_now()})
                    if args.json:
                        print(json.dumps(state, ensure_ascii=False, indent=2))
                    return 1
                if args.once:
                    if args.json:
                        print(json.dumps(state, ensure_ascii=False, indent=2))
                    return 0
                time.sleep(args.interval_seconds)
    except WatcherError as exc:
        if args.json:
            print(json.dumps({"status": "NOT_STARTED", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
