#!/usr/bin/env python3
"""Manage resumable Luna semantic-reading batches.

This command is deliberately separate from packet preparation and card
assembly.  Packets are reading material only; a paper becomes completed here
only when its selected row has a structurally valid completed semantic card.
Workers claim pending PMCID rows through short leases, then complete or
release those leases after the card assembler has written a card.  Manager
state lives below ``semantic-distillation/luna-state`` by default and is
written with an O_EXCL lock plus atomic ``os.replace`` updates.

Typical commands::

    python scripts/manage_semantic_reading.py status --root CORPUS
    python scripts/manage_semantic_reading.py plan --root CORPUS --workers 3
    python scripts/manage_semantic_reading.py claim --root CORPUS \
        --worker luna-1 --limit 5 --lease-seconds 3600
    python scripts/manage_semantic_reading.py complete --root CORPUS \
        --lease-id LEASE_ID --worker luna-1
    python scripts/manage_semantic_reading.py release --root CORPUS \
        --lease-id LEASE_ID --worker luna-1

``state.json`` is the canonical manager snapshot.  ``leases.json`` and
``progress.json`` are atomic, human-inspectable mirrors; all writes happen
under the same lock and are recoverable from the canonical snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

try:  # ``python scripts/manage_semantic_reading.py``
    from validate_semantic_distillation import SemanticValidator, packet_locator_sequence_error, text_value
except ImportError:  # pragma: no cover - ``python -m scripts...``
    from .validate_semantic_distillation import SemanticValidator, packet_locator_sequence_error, text_value


SCHEMA_VERSION = "1.0"
DEFAULT_LEASE_SECONDS = 3600
DEFAULT_BATCH_SIZE = 5
DEFAULT_WORKERS = 3
LOCK_TIMEOUT_SECONDS = 20.0
STALE_LOCK_SECONDS = 300.0
COMPLETED_STATUSES = {"completed", "complete", "done", "read"}
DEFAULT_READER_ROLE = "luna_primary"
DEFAULT_READER_MODEL = "gpt-5.6-luna"
DEFAULT_REASONING_EFFORT = "max"


class ManagerError(RuntimeError):
    """A user-facing manager error that should not corrupt state."""


@dataclass(frozen=True)
class ReadingRow:
    """Compact selected-paper identity and structural reading status."""

    index: int
    pmcid: str
    paper_id: str
    title: str
    journal: str
    year: Any
    primary_stratum: str
    reading_order: int
    status: str  # ``remaining``, ``completed``, or ``invalid``
    packet_present: bool
    row: Mapping[str, Any]

    def output(self) -> dict[str, Any]:
        packet_path = text_value(self.row.get("packet_path"))
        return {
            "pmcid": self.pmcid,
            "paper_id": self.paper_id,
            "title": self.title,
            "journal": self.journal,
            "year": self.year,
            "primary_stratum": self.primary_stratum,
            "reading_order": self.reading_order,
            "status": self.status,
            "packet_path": packet_path or None,
            # This is an availability hint, never a read/completion signal.
            "packet_present": self.packet_present,
        }


@dataclass
class CorpusSnapshot:
    """Read-only view of selection/cards/validator state."""

    root: Path
    sd: Path
    rows: list[ReadingRow]
    invalid_count: int
    validator_counts: dict[str, Any]
    validator_issues: list[dict[str, Any]]

    @property
    def by_pmcid(self) -> dict[str, ReadingRow]:
        # A duplicate selection is already marked invalid by the validator;
        # retaining the first row makes conflict messages deterministic.
        result: dict[str, ReadingRow] = {}
        for row in self.rows:
            result.setdefault(row.pmcid.casefold(), row)
        return result

    @property
    def completed(self) -> list[ReadingRow]:
        return [row for row in self.rows if row.status == "completed"]

    @property
    def remaining(self) -> list[ReadingRow]:
        return [row for row in self.rows if row.status == "remaining"]

    @property
    def invalid(self) -> list[ReadingRow]:
        return [row for row in self.rows if row.status == "invalid"]

    def counts(self) -> dict[str, int]:
        # ``invalid_count`` includes malformed/orphan cards and selection
        # parse errors reported by the structural validator.  ``invalid_rows``
        # keeps the selected-row subset visible to callers.
        remaining = len(self.remaining)
        completed = len(self.completed)
        return {
            "selected": len(self.rows),
            "remaining": remaining,
            "completed": completed,
            "invalid": int(self.invalid_count),
            "invalid_rows": len(self.invalid),
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def epoch_now() -> float:
    return time.time()


def iso_at(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_epoch(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = text_value(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def resolve_paths(root: Path) -> tuple[Path, Path]:
    """Return ``(external_root, semantic_distillation_dir)``."""

    resolved = root.expanduser().resolve()
    if resolved.name.casefold() == "semantic-distillation" and (resolved / "selection.jsonl").exists():
        return resolved.parent, resolved
    return resolved, resolved / "semantic-distillation"


def default_state_dir(sd: Path) -> Path:
    return sd / "luna-state"


def packet_exists(sd: Path, external_root: Path, row: Mapping[str, Any], pmcid: str) -> bool:
    """Resolve canonical/fallback packet paths without using them as read state."""

    canonical = sd / "packets" / f"{pmcid}.md"
    if canonical.exists():
        return True
    declared = text_value(row.get("packet_path"))
    if not declared:
        return False
    candidate = Path(declared)
    candidates = [candidate] if candidate.is_absolute() else [sd / candidate, external_root / candidate]
    return any(path.exists() for path in candidates)


def normalize_pmcid(value: Any) -> str:
    return text_value(value).strip()


def stable_hash_int(value: str) -> int:
    return int(hashlib.sha256(value.casefold().encode("utf-8")).hexdigest(), 16)


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManagerError(f"Could not read JSON state {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ManagerError(f"JSON state must be an object: {path}")
    return value


def card_status_value(card: Mapping[str, Any]) -> str:
    reading = card.get("reading")
    if isinstance(reading, Mapping):
        return text_value(reading.get("status")).casefold()
    return text_value(card.get("status")).casefold()


def card_path_for_row(snapshot: CorpusSnapshot, row: ReadingRow) -> Path | None:
    """Resolve a card without treating a packet as a card substitute."""

    cards_dir = snapshot.sd / "cards"
    canonical = cards_dir / f"{row.pmcid}.json"
    if canonical.exists():
        return canonical
    if not cards_dir.is_dir():
        return None
    wanted = {row.pmcid.casefold(), row.paper_id.casefold()}
    for path in sorted(cards_dir.glob("*.json")):
        try:
            card = read_json_object(path)
        except ManagerError:
            continue
        values = {
            text_value(card.get("pmcid")).casefold(),
            text_value(card.get("paper_id")).casefold(),
            text_value(card.get("card_id")).casefold().removeprefix("semantic:"),
        }
        if wanted.intersection(values):
            return path
    return None


def verify_row(
    snapshot: CorpusSnapshot,
    row: ReadingRow,
    expected_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a batch-scoped verification result.

    The global structural validator still supplies ``row.status``.  This
    function deliberately narrows the emitted result to one PMCID and checks
    the card's reader provenance, packet hash, and chunk locators locally, so
    a pending paper elsewhere in the corpus never blocks a completed batch.
    """

    result: dict[str, Any] = {"pmcid": row.pmcid, "paper_id": row.paper_id, "status": row.status, "ok": row.status == "completed", "issues": []}
    path = card_path_for_row(snapshot, row)
    if path is None:
        result["ok"] = False
        result["issues"].append("card_missing")
        return result
    try:
        card = read_json_object(path)
    except ManagerError as exc:
        result["ok"] = False
        result["issues"].append(f"card_unreadable: {exc}")
        return result
    result["card_path"] = str(path)
    reading = card.get("reading") if isinstance(card.get("reading"), Mapping) else {}
    if card_status_value(card) not in COMPLETED_STATUSES:
        result["ok"] = False
        result["issues"].append("card_not_completed")
    expected = {
        "reader_role": text_value((expected_provenance or {}).get("reader_role")),
        "reader_model": text_value((expected_provenance or {}).get("reader_model")),
        "reasoning_effort": text_value((expected_provenance or {}).get("reasoning_effort")),
    }
    for key, wanted in expected.items():
        if wanted and text_value(reading.get(key)) != wanted:
            result["ok"] = False
            result["issues"].append(f"{key}_mismatch")

    pmcid = normalize_pmcid(card.get("pmcid"))
    if pmcid.casefold() != row.pmcid.casefold():
        result["ok"] = False
        result["issues"].append("pmcid_mismatch")
    card_paper = text_value(card.get("paper_id"))
    if row.paper_id and card_paper.casefold() != row.paper_id.casefold():
        result["ok"] = False
        result["issues"].append("paper_id_mismatch")

    source = card.get("source") if isinstance(card.get("source"), Mapping) else {}
    provenance = card.get("provenance") if isinstance(card.get("provenance"), Mapping) else {}
    card_source_hash = text_value(
        card.get("source_record_sha256")
        or card.get("source_hash")
        or source.get("source_record_sha256")
        or source.get("source_hash")
        or provenance.get("source_record_sha256")
        or provenance.get("source_hash")
    )
    row_source_hash = text_value(row.row.get("source_record_sha256") or row.row.get("source_hash"))
    source_record_value = text_value(row.row.get("record_path") or row.row.get("source_record"))
    actual_source_hash = ""
    if not row_source_hash:
        result["ok"] = False
        result["issues"].append("selection_source_hash_missing")
    if not card_source_hash or (row_source_hash and card_source_hash.casefold() != row_source_hash.casefold()):
        result["ok"] = False
        result["issues"].append("card_source_hash_mismatch")
    if not source_record_value:
        result["ok"] = False
        result["issues"].append("source_record_path_missing")
    else:
        source_record = Path(source_record_value)
        candidates = [source_record] if source_record.is_absolute() else [snapshot.root / source_record, snapshot.sd / source_record]
        source_path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if source_path is None:
            result["ok"] = False
            result["issues"].append("source_record_missing")
        else:
            result["source_record_path"] = str(source_path)
            try:
                digest = hashlib.sha256()
                with source_path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                actual_source_hash = digest.hexdigest()
                result["source_record_sha256"] = actual_source_hash
                if not row_source_hash or row_source_hash.casefold() != actual_source_hash:
                    result["ok"] = False
                    result["issues"].append("selection_source_hash_mismatch")
                if not card_source_hash or card_source_hash.casefold() != actual_source_hash:
                    result["ok"] = False
                    result["issues"].append("card_source_hash_mismatch")
            except OSError as exc:
                result["ok"] = False
                result["issues"].append(f"source_record_read_error: {exc}")

    packet = snapshot.sd / "packets" / f"{row.pmcid}.md"
    declared_packet = text_value(reading.get("packet_sha256"))
    if not packet.exists():
        declared = text_value(row.row.get("packet_path"))
        if declared:
            candidate = Path(declared)
            candidates = [candidate] if candidate.is_absolute() else [snapshot.sd / candidate, snapshot.root / candidate]
            packet = next((item for item in candidates if item.exists()), packet)
    if not packet.exists():
        result["ok"] = False
        result["issues"].append("packet_missing")
    else:
        result["packet_path"] = str(packet)
        try:
            packet_bytes = packet.read_bytes()
            packet_hash = hashlib.sha256(packet_bytes).hexdigest()
            result["packet_sha256"] = packet_hash
            if not declared_packet or declared_packet.casefold() != packet_hash:
                result["ok"] = False
                result["issues"].append("packet_hash_mismatch")
            declared_row_hash = text_value(row.row.get("packet_sha256"))
            if not declared_row_hash:
                result["ok"] = False
                result["issues"].append("selection_packet_hash_missing")
            elif declared_row_hash.casefold() != packet_hash:
                result["ok"] = False
                result["issues"].append("selection_packet_hash_mismatch")
            packet_text = packet_bytes.decode("utf-8-sig", errors="replace")
            packet_source_match = re.search(
                r"(?mi)^-\s*Source SHA-256:\s*`([0-9a-f]{64})`\s*$",
                packet_text,
            )
            packet_source_hash = packet_source_match.group(1).casefold() if packet_source_match else ""
            if not packet_source_hash:
                result["ok"] = False
                result["issues"].append("packet_source_hash_missing")
            elif any(
                expected and packet_source_hash != expected.casefold()
                for expected in (row_source_hash, card_source_hash, actual_source_hash)
            ):
                result["ok"] = False
                result["issues"].append("packet_source_hash_mismatch")
            # Keep the locator check local to this packet/card.  The full
            # validator also checks every semantic field and remains the
            # source of the row's structural status.
            packet_locators = re.findall(r"(?m)^\s*#{3,}\s+(S\d{3}:C\d{2})(?:\s|$)", packet_text)
            duplicate_locators = sorted({item for item in packet_locators if packet_locators.count(item) > 1})
            if duplicate_locators:
                result["ok"] = False
                result["issues"].append("packet_locators_duplicate")
            sequence_error = packet_locator_sequence_error(packet_locators)
            if sequence_error:
                result["ok"] = False
                result["issues"].append(f"packet_locator_sequence: {sequence_error}")
            card_locators = reading.get("section_locators_read")
            declared_locators = [str(item).strip() for item in card_locators] if isinstance(card_locators, list) else []
            if not isinstance(card_locators, list) or declared_locators != packet_locators:
                result["ok"] = False
                result["issues"].append("locators_incomplete_or_mismatch")
        except OSError as exc:
            result["ok"] = False
            result["issues"].append(f"packet_read_error: {exc}")
    return result


def prepared_migration_ids(sd: Path) -> tuple[set[str], bool]:
    """Return PMCID identities held by uncommitted packet migrations."""

    ids: set[str] = set()
    unreadable = False
    transaction_root = sd / "migration" / "transactions"
    if transaction_root.exists():
        for manifest_path in transaction_root.glob("*/transaction.json"):
            try:
                transaction = read_json_object(manifest_path)
            except ManagerError:
                unreadable = True
                continue
            if text_value(transaction.get("status")).casefold() == "committed":
                continue
            ids.update(
                text_value(value).upper()
                for value in (transaction.get("pmcids") if isinstance(transaction.get("pmcids"), list) else [])
            )
    return ids, unreadable


def verify_pending_material(snapshot: CorpusSnapshot, row: ReadingRow) -> list[str]:
    """Verify immutable source/packet material before a pending row is leased."""

    issues: list[str] = []
    transaction_ids, transaction_unreadable = prepared_migration_ids(snapshot.sd)
    if transaction_unreadable:
        issues.append("migration_transaction_unreadable")
    if row.pmcid in transaction_ids:
        issues.append("migration_transaction_prepared")
    row_source_hash = text_value(row.row.get("source_record_sha256") or row.row.get("source_hash")).casefold()
    source_value = text_value(row.row.get("record_path") or row.row.get("source_record"))
    if not row_source_hash or not source_value:
        issues.append("source_identity_missing")
    else:
        source = Path(source_value)
        source_candidates = [source] if source.is_absolute() else [snapshot.root / source, snapshot.sd / source]
        source_path = next((candidate for candidate in source_candidates if candidate.is_file()), None)
        if source_path is None:
            issues.append("source_record_missing")
        else:
            try:
                digest = hashlib.sha256()
                with source_path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != row_source_hash:
                    issues.append("source_record_hash_mismatch")
            except OSError:
                issues.append("source_record_unreadable")

    packet = snapshot.sd / "packets" / f"{row.pmcid}.md"
    if not packet.is_file():
        declared = text_value(row.row.get("packet_path"))
        if declared:
            candidate = Path(declared)
            packet_candidates = [candidate] if candidate.is_absolute() else [snapshot.sd / candidate, snapshot.root / candidate]
            packet = next((item for item in packet_candidates if item.is_file()), packet)
    if not packet.is_file():
        issues.append("packet_missing")
        return issues
    try:
        packet_bytes = packet.read_bytes()
    except OSError:
        issues.append("packet_unreadable")
        return issues
    packet_hash = hashlib.sha256(packet_bytes).hexdigest()
    declared_packet_hash = text_value(row.row.get("packet_sha256")).casefold()
    if not declared_packet_hash or declared_packet_hash != packet_hash:
        issues.append("selection_packet_hash_mismatch")
    packet_text = packet_bytes.decode("utf-8-sig", errors="replace")
    source_match = re.search(r"(?mi)^-\s*Source SHA-256:\s*`([0-9a-f]{64})`\s*$", packet_text)
    packet_source_hash = source_match.group(1).casefold() if source_match else ""
    if not packet_source_hash or not row_source_hash or packet_source_hash != row_source_hash:
        issues.append("packet_source_hash_mismatch")
    locators = re.findall(r"(?m)^\s*#{3,}\s+(S\d{3}:C\d{2})(?:\s|$)", packet_text)
    if not locators:
        issues.append("packet_locators_missing")
    if len(locators) != len(set(locators)):
        issues.append("packet_locators_duplicate")
    sequence_error = packet_locator_sequence_error(locators)
    if sequence_error:
        issues.append(f"packet_locator_sequence: {sequence_error}")
    return issues


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Write JSON through a same-directory temporary file and ``os.replace``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise ManagerError(f"Could not atomically write {path}: {exc}") from exc


def process_is_alive(pid: int) -> bool:
    """Return whether a local process exists without signalling it."""

    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        # On Windows os.kill(pid, 0) may map to TerminateProcess, so query a
        # process handle instead of sending any signal.
        try:
            import ctypes

            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except (AttributeError, OSError, ValueError):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class StateLock:
    """Small cross-process lock based on atomic exclusive file creation."""

    def __init__(self, state_dir: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> None:
        self.state_dir = state_dir
        self.path = state_dir / ".manager.lock"
        self.timeout = timeout
        self.token = f"{os.getpid()}:{uuid.uuid4().hex}"
        self.acquired = False

    def acquire(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps({"token": self.token, "pid": os.getpid(), "created_at": utc_now()}))
                    stream.flush()
                    os.fsync(stream.fileno())
                self.acquired = True
                return
            except FileExistsError:
                try:
                    age = epoch_now() - self.path.stat().st_mtime
                except OSError:
                    age = 0.0
                if age > STALE_LOCK_SECONDS:
                    owner_alive = True
                    try:
                        owner = read_json_object(self.path)
                        owner_pid = int(owner.get("pid") or 0)
                        owner_alive = process_is_alive(owner_pid)
                    except (ManagerError, TypeError, ValueError):
                        owner_alive = False
                    # Age alone is not evidence of abandonment: a full-corpus
                    # validator can legitimately hold the lock for minutes.
                    if not owner_alive:
                        try:
                            self.path.unlink()
                            continue
                        except OSError:
                            pass
                if time.monotonic() >= deadline:
                    raise ManagerError(f"Timed out waiting for manager lock: {self.path}")
                time.sleep(0.05)
            except OSError as exc:
                raise ManagerError(f"Could not create manager lock {self.path}: {exc}") from exc

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            # Do not remove a lock that a recovery process has replaced.
            current = read_json_object(self.path)
            if text_value(current.get("token")) != self.token:
                return
        except ManagerError:
            # A missing/corrupt lock is already released from this process's
            # perspective; never remove an unrelated replacement.
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        finally:
            self.acquired = False

    def __enter__(self) -> "StateLock":
        self.acquire()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.release()


class StateStore:
    """Canonical state plus inspectable lease/progress mirrors."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir.expanduser().resolve()
        self.state_path = self.state_dir / "state.json"
        self.leases_path = self.state_dir / "leases.json"
        self.progress_path = self.state_dir / "progress.json"

    @staticmethod
    def empty() -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "updated_at": None, "leases": {}, "progress": {}}

    def load(self) -> dict[str, Any]:
        if self.state_path.exists():
            state = read_json_object(self.state_path)
        else:
            # Recover an older/partially-created state from the mirrors.  An
            # absent state directory remains a pure read-only empty state.
            state = self.empty()
            if self.leases_path.exists():
                leases = read_json_object(self.leases_path)
                state["leases"] = leases.get("leases", leases)
            if self.progress_path.exists():
                progress = read_json_object(self.progress_path)
                state["progress"] = progress.get("progress", progress.get("items", progress))
        state.setdefault("schema_version", SCHEMA_VERSION)
        state.setdefault("updated_at", None)
        if not isinstance(state.get("leases"), dict):
            raise ManagerError(f"State leases must be an object: {self.state_path}")
        if not isinstance(state.get("progress"), dict):
            raise ManagerError(f"State progress must be an object: {self.state_path}")
        # Copy into plain dictionaries so callers can mutate without touching
        # the parsed object held by another operation.
        return {
            "schema_version": str(state.get("schema_version") or SCHEMA_VERSION),
            "updated_at": state.get("updated_at"),
            "leases": dict(state.get("leases") or {}),
            "progress": dict(state.get("progress") or {}),
        }

    def save(self, state: Mapping[str, Any]) -> None:
        canonical = {
            "schema_version": SCHEMA_VERSION,
            "updated_at": utc_now(),
            "leases": dict(state.get("leases") or {}),
            "progress": dict(state.get("progress") or {}),
        }
        # The canonical snapshot is the recovery point.  Mirrors are written
        # atomically as well and are safe to inspect while a worker is idle.
        atomic_write_json(self.state_path, canonical)
        atomic_write_json(self.leases_path, {"schema_version": SCHEMA_VERSION, "updated_at": canonical["updated_at"], "leases": canonical["leases"]})
        atomic_write_json(self.progress_path, {"schema_version": SCHEMA_VERSION, "updated_at": canonical["updated_at"], "progress": canonical["progress"]})


def _require_sd(sd: Path) -> None:
    selection = sd / "selection.jsonl"
    if not sd.exists():
        raise ManagerError(f"Semantic-distillation directory does not exist: {sd}")
    if not selection.exists():
        raise ManagerError(f"selection.jsonl is missing: {selection}")


def load_snapshot(root: Path) -> CorpusSnapshot:
    external_root, sd = resolve_paths(root)
    _require_sd(sd)
    try:
        validator = SemanticValidator(external_root)
        report = validator.report()
    except (OSError, ValueError, json.JSONDecodeError, ManagerError) as exc:
        raise ManagerError(f"Could not inspect semantic-distillation artifacts: {exc}") from exc

    rows: list[ReadingRow] = []
    invalid_card_stems = {
        path.stem.casefold()
        for card_index, (path, _card) in enumerate(validator.cards)
        if card_index in validator.invalid_card_indices
    }
    for index, source_row in enumerate(validator.selection):
        row = dict(source_row)
        pmcid = normalize_pmcid(row.get("pmcid"))
        paper_id = text_value(row.get("paper_id"))
        # A selection row without PMCID/paper identity is structurally
        # invalid.  Keep it in the selected count but never claim it.
        # This manager leases PMCID packets.  A paper_id-only row can remain a
        # useful validator diagnostic, but it is not a claimable semantic task.
        invalid = index in validator.row_invalid or not pmcid or pmcid.casefold() in invalid_card_stems
        status_kind = validator.row_status.get(index, "pending")
        if invalid or status_kind == "invalid":
            status = "invalid"
        elif status_kind == "completed":
            status = "completed"
        else:
            status = "remaining"
        display_pmcid = pmcid or f"<missing-pmcid:{index + 1}>"
        try:
            reading_order = int(row.get("reading_order") or index + 1)
        except (TypeError, ValueError):
            reading_order = index + 1
        rows.append(
            ReadingRow(
                index=index,
                pmcid=display_pmcid,
                paper_id=paper_id,
                title=text_value(row.get("title")),
                journal=text_value(row.get("journal")),
                year=row.get("year"),
                primary_stratum=text_value(row.get("primary_stratum") or row.get("stratum")),
                reading_order=reading_order,
                status=status,
                packet_present=packet_exists(sd, external_root, row, display_pmcid),
                row=row,
            )
        )
    counts = report.get("counts") if isinstance(report, Mapping) else {}
    invalid_count = max(
        int((counts or {}).get("invalid", 0)),
        len([row for row in rows if row.status == "invalid"]),
    )
    issues = report.get("issues") if isinstance(report, Mapping) else []
    return CorpusSnapshot(
        root=external_root,
        sd=sd,
        rows=rows,
        invalid_count=invalid_count,
        validator_counts=dict(counts or {}),
        validator_issues=list(issues or []),
    )


def active_leases(state: Mapping[str, Any], now: float | None = None) -> dict[str, Mapping[str, Any]]:
    now = epoch_now() if now is None else now
    result: dict[str, Mapping[str, Any]] = {}
    leases = state.get("leases") if isinstance(state.get("leases"), Mapping) else {}
    for lease_id, lease in leases.items():
        if not isinstance(lease, Mapping) or text_value(lease.get("status")).casefold() != "active":
            continue
        expiry = parse_epoch(lease.get("expires_at_epoch"))
        if expiry is None:
            expiry = parse_epoch(lease.get("expires_at"))
        if expiry is not None and expiry <= now:
            continue
        for item in lease.get("items") or []:
            if isinstance(item, Mapping):
                pmcid = normalize_pmcid(item.get("pmcid"))
                if pmcid:
                    result.setdefault(pmcid.casefold(), lease)
    return result


def lease_is_expired(lease: Mapping[str, Any], now: float | None = None) -> bool:
    expiry = parse_epoch(lease.get("expires_at_epoch"))
    if expiry is None:
        expiry = parse_epoch(lease.get("expires_at"))
    return expiry is not None and expiry <= (epoch_now() if now is None else now)


def cleanup_expired(state: dict[str, Any], now: float | None = None) -> int:
    """Mark expired leases and release only the progress they still own."""

    now = epoch_now() if now is None else now
    released = 0
    leases = state.setdefault("leases", {})
    progress = state.setdefault("progress", {})
    for lease_id, lease_value in list(leases.items()):
        if not isinstance(lease_value, dict) or text_value(lease_value.get("status")).casefold() != "active":
            continue
        if not lease_is_expired(lease_value, now):
            continue
        lease_value["status"] = "expired"
        lease_value["expired_at"] = utc_now()
        for item in lease_value.get("items") or []:
            if not isinstance(item, Mapping):
                continue
            pmcid = normalize_pmcid(item.get("pmcid"))
            set_lease_item_status(lease_value, pmcid, "released")
            progress_item = progress.get(pmcid.casefold())
            if isinstance(progress_item, dict) and text_value(progress_item.get("lease_id")) == str(lease_id):
                progress_item["status"] = "released"
                progress_item.pop("lease_id", None)
                progress_item["released_at"] = utc_now()
                progress_item["release_reason"] = "lease_expired"
                released += 1
    return released


def reconcile_progress(state: dict[str, Any], snapshot: CorpusSnapshot) -> None:
    """Repair stale manager entries without ever downgrading a completed card."""

    progress = state.setdefault("progress", {})
    leases = state.setdefault("leases", {})
    now = utc_now()
    for key, item in list(progress.items()):
        if not isinstance(item, dict):
            continue
        pmcid = normalize_pmcid(item.get("pmcid") or key)
        row = snapshot.by_pmcid.get(pmcid.casefold())
        if row is None:
            continue
        if row.status == "completed":
            lease_id = text_value(item.get("lease_id"))
            lease = leases.get(lease_id) if lease_id else None
            lease_active = (
                isinstance(lease, dict)
                and text_value(lease.get("status")).casefold() == "active"
                and not lease_is_expired(lease)
            )
            expected = (
                lease.get("expected_reader_provenance")
                if lease_active and isinstance(lease.get("expected_reader_provenance"), Mapping)
                else item.get("expected_reader_provenance")
                if isinstance(item.get("expected_reader_provenance"), Mapping)
                else None
            )
            verification = verify_row(snapshot, row, expected) if expected else {"ok": False, "issues": ["missing_expected_reader_provenance"]}
            # Automatic reconciliation is allowed only while this progress
            # entry still owns a live lease and the card satisfies that lease's
            # Luna reader provenance.  Expired/released late writes remain in
            # the global card audit but cannot silently complete an old lease.
            if lease_active and verification.get("ok"):
                if text_value(item.get("status")).casefold() != "completed":
                    item["status"] = "completed"
                    item["completed_at"] = item.get("completed_at") or now
                item.pop("verification_issues", None)
                item.pop("lease_id", None)
                set_lease_item_status(lease, pmcid, "completed")
                update_lease_terminal_status(lease, "completed")
            elif text_value(item.get("status")).casefold() in {"leased", "active"}:
                item["verification_issues"] = list(verification.get("issues") or [])
        elif text_value(item.get("status")).casefold() in {"leased", "active"}:
            lease_id = text_value(item.get("lease_id"))
            lease = leases.get(lease_id) if lease_id else None
            if not isinstance(lease, Mapping) or text_value(lease.get("status")).casefold() != "active" or lease_is_expired(lease):
                item["status"] = "stale"
                item["stale_at"] = item.get("stale_at") or now
                item.pop("lease_id", None)
        elif text_value(item.get("status")).casefold() == "completed":
            # A manually edited/stale progress mark never substitutes for a
            # completed card.  Preserve the audit trail but make it claimable.
            item["status"] = "stale"
            item["stale_at"] = item.get("stale_at") or now
            item.pop("lease_id", None)


def lease_item_status(lease: Mapping[str, Any], pmcid: str) -> str:
    for item in lease.get("items") or []:
        if isinstance(item, Mapping) and normalize_pmcid(item.get("pmcid")).casefold() == pmcid.casefold():
            return text_value(item.get("status")).casefold() or "leased"
    return "missing"


def set_lease_item_status(lease: dict[str, Any], pmcid: str, status: str) -> None:
    for item in lease.get("items") or []:
        if isinstance(item, dict) and normalize_pmcid(item.get("pmcid")).casefold() == pmcid.casefold():
            item["status"] = status
            return


def lease_remaining_items(lease: Mapping[str, Any]) -> list[str]:
    return [
        normalize_pmcid(item.get("pmcid"))
        for item in lease.get("items") or []
        if isinstance(item, Mapping) and text_value(item.get("status")).casefold() in {"", "leased", "active"}
    ]


def update_lease_terminal_status(lease: dict[str, Any], default: str) -> None:
    if lease_remaining_items(lease):
        lease["status"] = "active"
    else:
        lease["status"] = default
        lease["closed_at"] = lease.get("closed_at") or utc_now()


def state_context(args: argparse.Namespace) -> tuple[CorpusSnapshot, StateStore]:
    root_value = getattr(args, "root_sub", None) or getattr(args, "root_global", None) or getattr(args, "root", None)
    if root_value is None:
        raise ManagerError("--root is required")
    snapshot = load_snapshot(Path(root_value))
    state_value = getattr(args, "state_dir_sub", None) or getattr(args, "state_dir_global", None) or getattr(args, "state_dir", None)
    state_dir = Path(state_value) if state_value else default_state_dir(snapshot.sd)
    return snapshot, StateStore(state_dir)


def reload_snapshot(args: argparse.Namespace) -> CorpusSnapshot:
    root_value = getattr(args, "root_sub", None) or getattr(args, "root_global", None) or getattr(args, "root", None)
    if root_value is None:
        raise ManagerError("--root is required")
    return load_snapshot(Path(root_value))


def validate_worker(value: str) -> str:
    worker = text_value(value)
    if not worker:
        raise ManagerError("worker name must be non-empty")
    return worker


def selected_rows_for_claim(snapshot: CorpusSnapshot, state: Mapping[str, Any], args: argparse.Namespace) -> tuple[list[ReadingRow], list[dict[str, Any]]]:
    active = active_leases(state)
    explicit_values = [normalize_pmcid(value) for value in (getattr(args, "pmcid", None) or []) if normalize_pmcid(value)]
    conflicts: list[dict[str, Any]] = []
    if explicit_values:
        rows: list[ReadingRow] = []
        seen: set[str] = set()
        for value in explicit_values:
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            row = snapshot.by_pmcid.get(key)
            if row is None:
                conflicts.append({"pmcid": value, "reason": "not_selected"})
            elif row.status != "remaining":
                conflicts.append({"pmcid": value, "reason": row.status})
            elif key in active:
                lease = active[key]
                conflicts.append({"pmcid": value, "reason": "leased", "lease_id": text_value(lease.get("lease_id"))})
            else:
                material_issues = verify_pending_material(snapshot, row)
                if material_issues:
                    conflicts.append({"pmcid": value, "reason": "reading_material_invalid", "issues": material_issues})
                else:
                    rows.append(row)
        return rows, conflicts

    candidates = [row for row in snapshot.remaining if row.pmcid.casefold() not in active]
    shard_count = int(getattr(args, "shard_count", 1) or 1)
    shard_index = int(getattr(args, "shard_index", 0) or 0)
    if shard_count < 1:
        raise ManagerError("--workers/--shard-count must be >= 1")
    if shard_index < 0 or shard_index >= shard_count:
        raise ManagerError("--worker-index/--shard-index must be between 0 and workers-1")
    if shard_count > 1:
        candidates = [row for row in candidates if stable_hash_int(row.pmcid) % shard_count == shard_index]
    candidates.sort(key=lambda row: (row.reading_order, row.primary_stratum.casefold(), stable_hash_int(row.pmcid), row.pmcid.casefold()))
    limit = int(getattr(args, "limit", DEFAULT_BATCH_SIZE) or 0)
    if limit < 0:
        raise ManagerError("--limit must be >= 0 (0 means no limit)")
    selected = candidates if limit == 0 else candidates[:limit]
    valid_rows: list[ReadingRow] = []
    for row in selected:
        material_issues = verify_pending_material(snapshot, row)
        if material_issues:
            conflicts.append({"pmcid": row.pmcid, "reason": "reading_material_invalid", "issues": material_issues})
        else:
            valid_rows.append(row)
    return valid_rows, conflicts


def claim(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    snapshot, store = state_context(args)
    worker = validate_worker(args.worker)
    lease_seconds = int(args.lease_seconds)
    if lease_seconds <= 0:
        raise ManagerError("--lease-seconds must be > 0")
    with StateLock(store.state_dir):
        # Refresh under the manager lock so a card assembled between argument
        # parsing and claim cannot be leased as if it were still pending.
        snapshot = reload_snapshot(args)
        state = store.load()
        cleanup_expired(state)
        reconcile_progress(state, snapshot)
        rows, conflicts = selected_rows_for_claim(snapshot, state, args)
        if conflicts:
            payload = {"command": "claim", "status": "conflict", "conflicts": conflicts, "batch": []}
            store.save(state)
            return 2, payload
        if not rows:
            payload = {
                "command": "claim",
                "status": "empty",
                "worker": worker,
                "batch": [],
                "counts": snapshot.counts(),
                "state_dir": str(store.state_dir),
            }
            # Expiry/reconciliation may have changed state even for an empty
            # claim, so persist that recovery information.
            store.save(state)
            return 0, payload
        lease_id = f"lease-{uuid.uuid4().hex}"
        now = epoch_now()
        expires = now + lease_seconds
        items = [
            {
                "pmcid": row.pmcid,
                "paper_id": row.paper_id,
                "selection_index": row.index,
                "status": "leased",
            }
            for row in rows
        ]
        lease = {
            "lease_id": lease_id,
            "worker": worker,
            "status": "active",
            "created_at": utc_now(),
            "expires_at": iso_at(expires),
            "expires_at_epoch": expires,
            "shard_count": int(getattr(args, "shard_count", 1) or 1),
            "shard_index": int(getattr(args, "shard_index", 0) or 0),
            "expected_reader_provenance": {
                "reader_role": text_value(getattr(args, "reader_role", None)) or DEFAULT_READER_ROLE,
                "reader_model": text_value(getattr(args, "reader_model", None)) or DEFAULT_READER_MODEL,
                "reasoning_effort": text_value(getattr(args, "reasoning_effort", None)) or DEFAULT_REASONING_EFFORT,
            },
            "items": items,
        }
        state.setdefault("leases", {})[lease_id] = lease
        progress = state.setdefault("progress", {})
        expected_reader = dict(lease["expected_reader_provenance"])
        for row in rows:
            key = row.pmcid.casefold()
            existing = progress.get(key)
            if isinstance(existing, Mapping) and text_value(existing.get("status")).casefold() == "completed":
                # The snapshot was taken before this lock only in unusual
                # external-edit races; never overwrite a completed card.
                raise ManagerError(f"Conflict: {row.pmcid} became completed while claiming")
            attempts = int(existing.get("attempts") or 0) + 1 if isinstance(existing, Mapping) else 1
            progress[key] = {
                "pmcid": row.pmcid,
                "paper_id": row.paper_id,
                "status": "leased",
                "lease_id": lease_id,
                "worker": worker,
                "claimed_at": utc_now(),
                "attempts": attempts,
                "expected_reader_provenance": expected_reader,
            }
        store.save(state)
    payload = {
        "command": "claim",
        "status": "claimed",
        "lease_id": lease_id,
        "worker": worker,
        "expires_at": lease["expires_at"],
        "batch": [row.output() | {"lease_id": lease_id, "worker": worker} for row in rows],
        "counts": snapshot.counts(),
        "state_dir": str(store.state_dir),
    }
    return 0, payload


def target_pmcids(lease: Mapping[str, Any], requested: Iterable[str] | None) -> list[str]:
    lease_pmcids = [normalize_pmcid(item.get("pmcid")) for item in lease.get("items") or [] if isinstance(item, Mapping)]
    lease_map = {value.casefold(): value for value in lease_pmcids if value}
    requested_values = [normalize_pmcid(value) for value in (requested or []) if normalize_pmcid(value)]
    if not requested_values:
        return [value for value in lease_pmcids if value]
    selected: list[str] = []
    missing: list[str] = []
    for value in requested_values:
        if value.casefold() not in lease_map:
            missing.append(value)
        elif value.casefold() not in {item.casefold() for item in selected}:
            selected.append(lease_map[value.casefold()])
    if missing:
        raise ManagerError(f"PMCID is not part of lease {text_value(lease.get('lease_id'))}: {', '.join(missing)}")
    return selected


def mutate_lease(args: argparse.Namespace, action: str) -> tuple[int, dict[str, Any]]:
    snapshot, store = state_context(args)
    lease_id = text_value(getattr(args, "lease_id", None))
    if not lease_id:
        raise ManagerError("--lease-id is required")
    worker = text_value(getattr(args, "worker", None))
    requested = getattr(args, "pmcid", None) or []
    with StateLock(store.state_dir):
        # Refresh under the lock for stale-card/lease conflict detection.
        snapshot = reload_snapshot(args)
        state = store.load()
        cleanup_expired(state)
        reconcile_progress(state, snapshot)
        leases = state.setdefault("leases", {})
        lease = leases.get(lease_id)
        if not isinstance(lease, dict):
            raise ManagerError(f"Unknown lease: {lease_id}")
        lease_worker = text_value(lease.get("worker"))
        if worker and worker != lease_worker:
            raise ManagerError(f"Lease worker conflict: {worker!r} != {lease_worker!r}")
        targets = target_pmcids(lease, requested)
        progress = state.setdefault("progress", {})
        conflicts: list[dict[str, Any]] = []
        for pmcid in targets:
            row = snapshot.by_pmcid.get(pmcid.casefold())
            item_status = lease_item_status(lease, pmcid)
            progress_item = progress.get(pmcid.casefold())
            owns_lease = isinstance(progress_item, Mapping) and text_value(progress_item.get("lease_id")) == lease_id
            if action == "complete":
                if item_status == "completed" or (isinstance(progress_item, Mapping) and text_value(progress_item.get("status")).casefold() == "completed"):
                    if row is None or row.status != "completed":
                        conflicts.append({"pmcid": pmcid, "reason": "completed_state_no_longer_valid"})
                    else:
                        verification = verify_row(snapshot, row, lease.get("expected_reader_provenance"))
                        if not verification.get("ok"):
                            conflicts.append(
                                {
                                    "pmcid": pmcid,
                                    "reason": "idempotent_verification_failed",
                                    "issues": verification.get("issues") or [],
                                }
                            )
                    continue
                if not owns_lease:
                    conflicts.append({"pmcid": pmcid, "reason": "lease_ownership_conflict"})
                elif row is None:
                    conflicts.append({"pmcid": pmcid, "reason": "not_selected"})
                elif row.status == "invalid":
                    conflicts.append({"pmcid": pmcid, "reason": "invalid_card"})
                elif row.status != "completed":
                    conflicts.append({"pmcid": pmcid, "reason": "card_not_completed"})
                else:
                    verification = verify_row(snapshot, row, lease.get("expected_reader_provenance"))
                    if not verification.get("ok"):
                        conflicts.append(
                            {
                                "pmcid": pmcid,
                                "reason": "batch_verification_failed",
                                "issues": verification.get("issues") or [],
                            }
                        )
            else:
                if item_status in {"completed", "released"}:
                    continue
                if not owns_lease:
                    conflicts.append({"pmcid": pmcid, "reason": "lease_ownership_conflict"})
        if conflicts:
            store.save(state)
            return 2, {"command": action, "status": "conflict", "lease_id": lease_id, "conflicts": conflicts, "state_dir": str(store.state_dir)}

        changed = 0
        for pmcid in targets:
            progress_item = progress.get(pmcid.casefold())
            if not isinstance(progress_item, dict):
                continue
            if action == "complete":
                progress_item["status"] = "completed"
                progress_item.pop("lease_id", None)
                progress_item["completed_at"] = progress_item.get("completed_at") or utc_now()
                progress_item["worker"] = lease_worker
                set_lease_item_status(lease, pmcid, "completed")
            else:
                progress_item["status"] = "released"
                progress_item.pop("lease_id", None)
                progress_item["released_at"] = progress_item.get("released_at") or utc_now()
                progress_item["release_reason"] = "worker_release"
                set_lease_item_status(lease, pmcid, "released")
            changed += 1
        update_lease_terminal_status(lease, "completed" if action == "complete" else "released")
        store.save(state)
    payload = {
        "command": action,
        "status": "completed" if action == "complete" else "released",
        "lease_id": lease_id,
        "worker": lease_worker,
        "pmcids": targets,
        "changed": changed,
        "remaining_in_lease": lease_remaining_items(lease),
        "state_dir": str(store.state_dir),
    }
    return 0, payload


def verify(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """Verify only a lease batch (or explicit PMCID list), read-only."""

    snapshot, store = state_context(args)
    state = store.load()
    lease_id = text_value(getattr(args, "lease_id", None))
    lease: Mapping[str, Any] | None = None
    if lease_id:
        candidate = (state.get("leases") or {}).get(lease_id)
        if not isinstance(candidate, Mapping):
            raise ManagerError(f"Unknown lease: {lease_id}")
        lease = candidate
        requested = target_pmcids(lease, getattr(args, "pmcid", None) or [])
        expected = lease.get("expected_reader_provenance") if isinstance(lease.get("expected_reader_provenance"), Mapping) else {}
    else:
        requested = [normalize_pmcid(value) for value in (getattr(args, "pmcid", None) or []) if normalize_pmcid(value)]
        if not requested:
            raise ManagerError("verify requires --lease-id or at least one --pmcid")
        expected = {
            "reader_role": text_value(getattr(args, "reader_role", None)) or DEFAULT_READER_ROLE,
            "reader_model": text_value(getattr(args, "reader_model", None)) or DEFAULT_READER_MODEL,
            "reasoning_effort": text_value(getattr(args, "reasoning_effort", None)) or DEFAULT_REASONING_EFFORT,
        }
    results: list[dict[str, Any]] = []
    for pmcid in requested:
        row = snapshot.by_pmcid.get(pmcid.casefold())
        if row is None:
            results.append({"pmcid": pmcid, "status": "invalid", "ok": False, "issues": ["not_selected"]})
        else:
            results.append(verify_row(snapshot, row, expected))
    ok = all(bool(item.get("ok")) for item in results)
    payload = {
        "command": "verify",
        "status": "passed" if ok else "failed",
        "ok": ok,
        "lease_id": lease_id or None,
        "expected_reader_provenance": dict(expected),
        "results": results,
        "pending_outside_batch": len([row for row in snapshot.remaining if row.pmcid.casefold() not in {value.casefold() for value in requested}]),
        "read_signal": "batch-scoped card/packet/hash/locator verification; outside pending rows are not failures",
        "state_dir": str(store.state_dir),
    }
    return (0 if ok else 1), payload


def status(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    snapshot, store = state_context(args)
    state = store.load()
    now = epoch_now()
    active = active_leases(state, now)
    leases = state.get("leases") if isinstance(state.get("leases"), Mapping) else {}
    expired = [
        str(lease_id)
        for lease_id, lease in leases.items()
        if isinstance(lease, Mapping) and text_value(lease.get("status")).casefold() == "active" and lease_is_expired(lease, now)
    ]
    item_rows: list[dict[str, Any]] = []
    available = 0
    leased = 0
    for row in snapshot.rows:
        item = row.output()
        key = row.pmcid.casefold()
        lease = active.get(key)
        if row.status == "remaining" and lease is not None:
            item["status"] = "leased"
            item["lease_id"] = text_value(lease.get("lease_id"))
            item["worker"] = text_value(lease.get("worker"))
            item["lease_expires_at"] = lease.get("expires_at")
            leased += 1
        elif row.status == "remaining":
            item["status"] = "remaining"
            available += 1
        item_rows.append(item)
    counts = snapshot.counts()
    counts.update({"leased": leased, "available": available, "expired_leases": len(expired)})
    payload = {
        "command": "status",
        "root": str(snapshot.root),
        "semantic_distillation": str(snapshot.sd),
        "state_dir": str(store.state_dir),
        "counts": counts,
        "items": item_rows,
        "leases": list(leases.values()),
        "expired_lease_ids": expired,
        "validator_counts": snapshot.validator_counts,
        "validator_issues": snapshot.validator_issues,
        "read_signal": "completed cards only; packet presence is not completion",
    }
    lease_id = text_value(getattr(args, "lease_id", None))
    if lease_id:
        lease = (state.get("leases") or {}).get(lease_id)
        if not isinstance(lease, Mapping):
            raise ManagerError(f"Unknown lease: {lease_id}")
        targets = target_pmcids(lease, getattr(args, "pmcid", None) or [])
        expected = lease.get("expected_reader_provenance") if isinstance(lease.get("expected_reader_provenance"), Mapping) else {}
        payload["batch_verification"] = [
            verify_row(snapshot, snapshot.by_pmcid[pmcid.casefold()], expected)
            for pmcid in targets
            if pmcid.casefold() in snapshot.by_pmcid
        ]
    return 0, payload


def plan(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    snapshot, store = state_context(args)
    workers = int(args.workers)
    batch_size = int(args.batch_size)
    if workers < 1:
        raise ManagerError("--workers must be >= 1")
    if batch_size < 0:
        raise ManagerError("--batch-size must be >= 0 (0 means no limit)")
    active = active_leases(store.load())
    available = [row for row in snapshot.remaining if row.pmcid.casefold() not in active]
    available.sort(key=lambda row: (row.reading_order, stable_hash_int(row.pmcid), row.pmcid.casefold()))
    prefix = text_value(args.worker_prefix) or "luna"
    worker_batches: list[dict[str, Any]] = []
    for index in range(workers):
        assigned = [row for row in available if stable_hash_int(row.pmcid) % workers == index]
        if batch_size:
            assigned = assigned[:batch_size]
        worker_batches.append(
            {
                "worker_index": index,
                "worker": f"{prefix}-{index + 1}",
                "batch": [row.output() | {"worker_index": index} for row in assigned],
            }
        )
    payload = {
        "command": "plan",
        "workers": workers,
        "batch_size": batch_size,
        "worker_prefix": prefix,
        "counts": snapshot.counts() | {"available": len(available), "leased": len(snapshot.remaining) - len(available)},
        "worker_batches": worker_batches,
        "state_dir": str(store.state_dir),
        "read_signal": "planned from remaining cards; packets do not imply completion",
    }
    return 0, payload


def add_root_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", dest="root_sub", type=Path, help="External corpus root or semantic-distillation directory")
    parser.add_argument("--state-dir", dest="state_dir_sub", type=Path, help="Lease/progress state directory (default: <sd>/luna-state)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Claim, resume, complete, release, and inspect Luna semantic-reading batches.",
        epilog="Packets are unannotated reading material. Only structurally valid completed cards count as read.",
    )
    # Accept both the repository's command-first convention
    # (``status --root ROOT``) and the single-command convention
    # (``--root ROOT status``).
    parser.add_argument("--root", dest="root_global", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--state-dir", dest="state_dir_global", type=Path, help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Report remaining/completed/invalid rows and active leases")
    add_root_options(status_parser)
    status_parser.add_argument("--lease-id", "--lease", dest="lease_id", help="Include batch-scoped verification for a lease")
    status_parser.add_argument("--pmcid", action="append", help="With --lease-id, restrict batch verification to PMCID(s)")
    status_parser.add_argument("--json", action="store_true", help="Emit JSON only")

    verify_parser = subparsers.add_parser("verify", help="Verify only a lease batch's cards, packet hashes, and locators (read-only)")
    add_root_options(verify_parser)
    verify_parser.add_argument("--lease-id", "--lease", dest="lease_id", help="Lease ID returned by claim")
    verify_parser.add_argument("--pmcid", action="append", help="Verify explicit PMCID(s); with --lease-id they must belong to that lease")
    verify_parser.add_argument("--reader-role", default=DEFAULT_READER_ROLE, help=f"Expected reader role (default: {DEFAULT_READER_ROLE})")
    verify_parser.add_argument("--reader-model", default=DEFAULT_READER_MODEL, help=f"Expected reader model (default: {DEFAULT_READER_MODEL})")
    verify_parser.add_argument("--reasoning-effort", default=DEFAULT_REASONING_EFFORT, help=f"Expected reasoning effort (default: {DEFAULT_REASONING_EFFORT})")
    verify_parser.add_argument("--json", action="store_true", help="Emit JSON only")

    plan_parser = subparsers.add_parser("plan", aliases=["batches"], help="Print deterministic batches for rotating workers (read-only)")
    add_root_options(plan_parser)
    plan_parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"Number of worker shards (default: {DEFAULT_WORKERS})")
    plan_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=f"Maximum papers per worker (default: {DEFAULT_BATCH_SIZE}; 0 means no limit)")
    plan_parser.add_argument("--worker-prefix", default="luna", help="Worker name prefix (default: luna)")
    plan_parser.add_argument("--json", action="store_true", help="Emit JSON only")

    claim_parser = subparsers.add_parser("claim", help="Atomically claim a deterministic batch under a unique lease")
    add_root_options(claim_parser)
    claim_parser.add_argument("--worker", required=True, help="Luna worker name")
    claim_parser.add_argument("--limit", "--batch-size", "--count", dest="limit", type=int, default=DEFAULT_BATCH_SIZE, help=f"Maximum papers (default: {DEFAULT_BATCH_SIZE}; 0 means no limit)")
    claim_parser.add_argument("--lease-seconds", type=int, default=DEFAULT_LEASE_SECONDS, help=f"Lease duration (default: {DEFAULT_LEASE_SECONDS})")
    claim_parser.add_argument("--workers", "--shard-count", dest="shard_count", type=int, default=1, help="Deterministic shard count")
    claim_parser.add_argument("--worker-index", "--shard-index", dest="shard_index", type=int, default=0, help="Zero-based deterministic shard index")
    claim_parser.add_argument("--pmcid", action="append", help="Claim explicit PMCID(s) instead of the next deterministic rows")
    claim_parser.add_argument("--reader-role", default=DEFAULT_READER_ROLE, help=f"Expected reader role recorded in lease (default: {DEFAULT_READER_ROLE})")
    claim_parser.add_argument("--reader-model", default=DEFAULT_READER_MODEL, help=f"Expected reader model recorded in lease (default: {DEFAULT_READER_MODEL})")
    claim_parser.add_argument("--reasoning-effort", default=DEFAULT_REASONING_EFFORT, help=f"Expected reasoning effort recorded in lease (default: {DEFAULT_REASONING_EFFORT})")
    claim_parser.add_argument("--json", action="store_true", help="Emit JSON only")

    for name, help_text in (("complete", "Close a lease only after its cards are structurally completed"), ("release", "Release a lease or selected PMCID(s) for later workers")):
        command_parser = subparsers.add_parser(name, help=help_text)
        add_root_options(command_parser)
        command_parser.add_argument("--lease-id", "--lease", dest="lease_id", required=True, help="Lease ID returned by claim")
        command_parser.add_argument("--worker", help="Expected lease worker; catches stale-worker conflicts")
        command_parser.add_argument("--pmcid", action="append", help="Complete/release only these lease PMCID(s)")
        command_parser.add_argument("--json", action="store_true", help="Emit JSON only")
    return parser


def print_payload(payload: Mapping[str, Any], json_only: bool = False) -> None:
    if json_only:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    command = text_value(payload.get("command"))
    if command == "status":
        print(f"Selected: {payload['counts']['selected']}")
        print(f"Completed: {payload['counts']['completed']}")
        print(f"Remaining: {payload['counts']['remaining']} (available {payload['counts']['available']}, leased {payload['counts']['leased']})")
        print(f"Invalid: {payload['counts']['invalid']}")
        if payload.get("batch_verification") is not None:
            passed = sum(bool(item.get("ok")) for item in payload["batch_verification"])
            print(f"Batch verification: {passed}/{len(payload['batch_verification'])} passed")
        print(f"State: {payload['state_dir']}")
        return
    if command == "verify":
        print(f"Batch verify {payload.get('status', 'unknown')}: {len(payload.get('results') or [])} item(s)")
        for item in payload.get("results") or []:
            suffix = "" if item.get("ok") else f" — {', '.join(item.get('issues') or [])}"
            print(f"  {item.get('pmcid', '')}: {item.get('status', '')}{suffix}")
        return
    if command == "plan":
        print(f"Remaining: {payload['counts']['remaining']} | available: {payload['counts']['available']} | workers: {payload['workers']}")
        for worker in payload["worker_batches"]:
            print(f"\n{worker['worker']} (shard {worker['worker_index']}):")
            for index, item in enumerate(worker["batch"], 1):
                print(f"  {index}. {item['pmcid']} — {item.get('title') or '(untitled)'}")
        return
    if command == "claim":
        print(f"Claim {payload.get('status', 'unknown')}: worker={payload.get('worker', '')} lease={payload.get('lease_id', '')}")
        for index, item in enumerate(payload.get("batch") or [], 1):
            print(f"  {index}. {item['pmcid']} — {item.get('title') or '(untitled)'}")
        if payload.get("conflicts"):
            print("Conflicts:")
            for conflict in payload["conflicts"]:
                print(f"  - {conflict}")
        return
    print(f"{command}: {payload.get('status', 'ok')}")
    if payload.get("lease_id"):
        print(f"Lease: {payload['lease_id']}")
    if payload.get("conflicts"):
        for conflict in payload["conflicts"]:
            print(f"  - {conflict}")


def dispatch(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    command = text_value(args.command).casefold()
    if command == "status":
        return status(args)
    if command == "verify":
        return verify(args)
    if command in {"plan", "batches"}:
        return plan(args)
    if command == "claim":
        return claim(args)
    if command in {"complete", "release"}:
        return mutate_lease(args, command)
    raise ManagerError(f"Unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    # Windows consoles commonly inherit a legacy code page. Queue metadata
    # contains arbitrary journal/title Unicode, so make CLI output pipe-safe.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        code, payload = dispatch(args)
    except ManagerError as exc:
        payload = {"command": text_value(getattr(args, "command", "")), "status": "error", "error": str(exc)}
        code = 2
    print_payload(payload, bool(getattr(args, "json", False)))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
