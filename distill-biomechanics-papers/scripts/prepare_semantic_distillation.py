#!/usr/bin/env python3
"""Prepare deterministic, unannotated reading packets from a corpus audit sample.

The command is deliberately a *material-preparation* step.  By default it
reads the 400-paper ID sample recorded by ``distill_large_corpus.py``, joins
each ID to a parsed ``records/<PMCID>.json.gz`` record, screens obvious
non-substantive items, and chooses a small, diversity-aware set for manual/LLM
reading.  ``--all-manifest`` enables the resumable full-manifest queue: every
manifest paper is represented in ``selection.jsonl`` and gets a deterministic
packet, while existing selection rows, packets, and completed cards are
preserved.  ``--limit``/``--offset`` make the queue safe to run in bounded
batches; ``--offset`` addresses immutable manifest order, so rerunning a
completed batch is idempotent.

All outputs are written below ``<root>/semantic-distillation`` (with the
selection report in ``<root>/semantic-distillation/reports/selection.json``).  Existing packets are
preserved unless ``--overwrite-packets`` is supplied.  Card stubs, when
requested, are created only when absent and are never overwritten.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

try:  # Running as ``python scripts/...`` from the skill directory.
    from _common import read_json, sha256_file, utc_now, write_json, write_jsonl
    from manage_semantic_reading import StateLock
except ImportError:  # pragma: no cover - allows ``python -m scripts...`` usage
    from ._common import read_json, sha256_file, utc_now, write_json, write_jsonl
    from .manage_semantic_reading import StateLock


SCHEMA_VERSION = "1.0"
DEFAULT_CHUNK_WORDS = 900
DEFAULT_PER_STRATUM = 1
PACKET_SOURCE_HASH_RE = re.compile(
    r"(?mi)^-\s*Source SHA-256:\s*`([0-9a-f]{64})`\s*$"
)

# These labels are intentionally broad and cue-based.  They describe features
# visible in metadata/headings and are not claims about what a paper found.
ARTICLE_KIND_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("systematic_review", ("systematic review", "meta-analysis", "meta analysis", "scoping review")),
    ("review", ("review", "overview", "state of the art", "literature survey")),
    ("case_report", ("case report", "case series", "case study")),
    ("protocol", ("protocol", "study protocol", "registered report")),
    ("editorial", ("editorial", "commentary", "letter to the editor", "news")),
)

DESIGN_FEATURE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "finite_element_or_computational",
        (
            "finite element",
            "finite-element",
            "fem",
            "computational model",
            "numerical model",
            "in silico",
            "simulation",
            "modelling",
            "modeling",
        ),
    ),
    (
        "experimental_mechanical",
        (
            "mechanical test",
            "mechanical testing",
            "compression test",
            "tensile test",
            "fatigue test",
            "bench test",
            "biomechanical",
        ),
    ),
    ("additive_manufacturing", ("additive manufactur", "3d print", "three-dimensional print", "laser powder", "selective laser")),
    ("topology_or_structural_optimization", ("topology optim", "structural optim", "generative design", "lattice optim")),
    ("porous_or_lattice_scaffold", ("porous", "scaffold", "lattice", "tpms", "gyroid", "trabecular")),
    ("in_vitro", ("in vitro", "cell culture", "cultured cells", "in vitro study")),
    ("in_vivo_animal", ("in vivo", "animal", "rat", "mouse", "rabbit", "sheep", "goat", "canine", "porcine")),
    ("clinical_or_observational", ("clinical", "patient", "cohort", "retrospective", "prospective", "randomized", "trial")),
    ("imaging_or_characterization", ("micro-ct", "micro ct", "computed tomography", "histolog", "microscopy", "characterization", "imaging")),
    ("biological_or_mechanobiological", ("osteogen", "osseointegration", "bone ingrowth", "cell", "gene expression", "remodeling", "remodelling")),
)

CORRECTION_CUES = (
    "correction",
    "corrigendum",
    "erratum",
    "publisher correction",
)
RETRACTION_CUES = ("retraction", "retracted", "expression of concern")
EDITORIAL_CUES = ("editorial", "commentary", "letter to the editor", "news", "in memoriam")
NON_SUBSTANTIVE_TYPES = (
    "abstract",
    "meeting abstract",
    "conference abstract",
    "poster",
    "letter",
    "comment",
    "news",
    "brief communication",
    "short communication",
    "perspective",
    "viewpoint",
    "opinion",
    "conference proceeding",
    "correction",
    "erratum",
    "corrigendum",
    "retraction",
    "expression of concern",
)

REFERENCE_TYPES = {
    "reference",
    "references",
    "ref-list",
    "reference-list",
    "bibliography",
    "literature-cited",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare deterministic, unannotated body-reading packets from "
            "the 400-paper audit sample or a resumable full-manifest queue."
        )
    )
    parser.add_argument("--root", required=True, type=Path, help="External corpus root containing reports/ and records/")
    parser.add_argument(
        "--per-stratum",
        type=int,
        default=DEFAULT_PER_STRATUM,
        metavar="N",
        help=f"Maximum selected papers per primary discovery stratum (default: {DEFAULT_PER_STRATUM})",
    )
    parser.add_argument(
        "--chunk-words",
        type=int,
        default=DEFAULT_CHUNK_WORDS,
        metavar="N",
        help=f"Maximum words in each stable packet chunk (default: {DEFAULT_CHUNK_WORDS})",
    )
    parser.add_argument(
        "--overwrite-packets",
        action="store_true",
        help="Regenerate packet Markdown files even when a packet already exists",
    )
    parser.add_argument(
        "--include-pmcid",
        action="append",
        default=[],
        metavar="PMCID",
        help="Explicitly include an eligible PMCID before balanced per-stratum filling (repeatable)",
    )
    parser.add_argument(
        "--card-stubs",
        action="store_true",
        help="Create blank, non-overwriting card JSON stubs for selected papers",
    )
    parser.add_argument(
        "--card-schema",
        type=Path,
        metavar="PATH",
        help="Optional JSON object used as the base schema for --card-stubs",
    )
    parser.add_argument(
        "--all-manifest",
        "--manifest-all",
        "--from-manifest",
        "--all",
        dest="all_manifest",
        action="store_true",
        help=(
            "Queue every paper in <root>/manifest.jsonl (existing selection rows "
            "are retained; use --limit/--offset for resumable batches)"
        ),
    )
    parser.add_argument(
        "--manifest",
        dest="manifest_path",
        type=Path,
        metavar="PATH",
        help="Manifest JSONL for --all-manifest (default: <root>/manifest.jsonl)",
    )
    parser.add_argument(
        "--limit",
        "--batch-size",
        "--max-papers",
        dest="queue_limit",
        type=int,
        metavar="N",
        help=(
            "Maximum number of previously unselected manifest papers to queue in "
            "this run; omit for the full remaining queue"
        ),
    )
    parser.add_argument(
        "--offset",
        "--batch-offset",
        dest="queue_offset",
        type=int,
        default=0,
        metavar="N",
        help="Zero-based absolute manifest-row offset for a deterministic batch (default: 0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan a manifest queue batch without writing packets, cards, selection, or reports",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Explicitly document resumable queue intent (resume is the default in --all-manifest mode)",
    )
    return parser.parse_args(argv)


def _normalise_text(value: Any) -> str:
    """Return cue text with HTML entities/tags and repeated whitespace removed."""

    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _words(text: str) -> list[str]:
    # Keeping tokens rather than character slices makes chunk boundaries stable
    # across platforms and preserves every non-whitespace token.
    return re.findall(r"\S+", text)


def _sample_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    sample = report.get("qualitative_audit_sample", {}).get("papers", [])
    if not isinstance(sample, list):
        raise ValueError("reports/writing-patterns.json has no qualitative_audit_sample.papers list")
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(sample):
        if not isinstance(item, dict):
            continue
        # Retain the sample order as a provenance aid.  Selection itself is
        # hash-stable, so a regenerated report cannot change tie-breaking.
        row = dict(item)
        row["sample_index"] = index
        rows.append(row)
    return rows


def _pmcid_key(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", str(value or "")).casefold()


def _record_index(records_dir: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for path in sorted(records_dir.glob("*.json.gz")):
        index[_pmcid_key(path.name.removesuffix(".json.gz"))] = path
    return index


def _load_record(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def _section_is_reference(section: dict[str, Any]) -> bool:
    section_type = _normalise_text(section.get("section_type"))
    heading = _normalise_text(section.get("heading"))
    if section_type in REFERENCE_TYPES:
        return True
    if section_type.endswith("references") or section_type.endswith("reference"):
        return True
    return bool(re.search(r"^(?:\d+[. ):-]*)?references?$|^reference list$|^bibliography$|^literature cited$", heading))


def _non_reference_sections(record: dict[str, Any]) -> list[dict[str, Any]]:
    sections = record.get("sections")
    if not isinstance(sections, list):
        return []
    result: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict) or _section_is_reference(section):
            continue
        result.append(section)
    return result


def _cue_text(record: dict[str, Any], sections: Iterable[dict[str, Any]]) -> str:
    headings = " ".join(str(section.get("heading") or "") for section in sections)
    section_types = " ".join(str(section.get("section_type") or "") for section in sections)
    # Abstract is metadata in the parsed record and is useful as a selection
    # cue.  No full body section text is used for classification.
    return _normalise_text(
        " ".join(
            (
                str(record.get("title") or ""),
                str(record.get("publication_type") or ""),
                str(record.get("abstract") or ""),
                headings,
                section_types,
            )
        )
    )


def _classify_article_kind(record: dict[str, Any], cue_text: str) -> str:
    publication_type = _normalise_text(record.get("publication_type"))
    # Article kind is inferred from title, publication-type metadata, and
    # headings only.  Abstract wording can mention a review or case without
    # making that the article's publication kind.  Restrict heading matches
    # to heading starts so ``Institutional Review Board`` is not a review.
    title_type = _normalise_text(" ".join((str(record.get("title") or ""), publication_type)))
    headings = [
        _normalise_text(section.get("heading"))
        for section in record.get("sections") or []
        if isinstance(section, dict) and str(section.get("heading") or "").strip()
    ]
    for kind, patterns in ARTICLE_KIND_PATTERNS:
        if any(re.search(rf"\b{re.escape(pattern)}\b", title_type) for pattern in patterns):
            # Editorial cues are retained as a label even though they are
            # screened out; this makes the screening decision auditable.
            return kind
        if any(
            any(re.match(rf"^(?:\d+[. ):-]*)?{re.escape(pattern)}(?:\b|$)", heading) for pattern in patterns)
            for heading in headings
        ):
            return kind
    if "case report" in publication_type or "case-report" in publication_type:
        return "case_report"
    if "research" in publication_type or "journal article" in publication_type or "clinical trial" in publication_type:
        return "original_research"
    if "method" in publication_type or "protocol" in publication_type:
        return "protocol"
    return "other_substantive"


def _classify_design_features(record: dict[str, Any], cue_text: str, sections: Iterable[dict[str, Any]]) -> list[str]:
    features = [name for name, patterns in DESIGN_FEATURE_PATTERNS if any(pattern in cue_text for pattern in patterns)]
    section_types = {str(section.get("section_type") or "").casefold() for section in sections}
    if "methods" in section_types and "results" in section_types and "finite_element_or_computational" in features:
        features.append("methods_results_modeling")
    if not features:
        features.append("unclassified")
    return features


def _primary_stratum(sample_row: dict[str, Any], record: dict[str, Any]) -> str:
    values = sample_row.get("strata")
    if not isinstance(values, list) or not values:
        values = record.get("discovery_strata")
    if isinstance(values, list) and values:
        value = str(values[0]).strip()
        if value:
            return value
    return "unclassified"


def _exclusion_reasons(record: dict[str, Any], sections: list[dict[str, Any]], cue_text: str) -> list[str]:
    publication_type = _normalise_text(record.get("publication_type"))
    title = _normalise_text(record.get("title"))
    reasons: list[str] = []
    if any(cue in title or cue in publication_type for cue in CORRECTION_CUES):
        reasons.append("correction")
    if any(cue in title or cue in publication_type for cue in RETRACTION_CUES):
        reasons.append("retraction")
    if any(cue in title or cue in publication_type for cue in EDITORIAL_CUES):
        reasons.append("editorial")
    non_reference_text = " ".join(str(section.get("text") or "").strip() for section in sections)
    body_words = len(_words(non_reference_text))
    abstract_words = len(_words(str(record.get("abstract") or "")))
    if body_words == 0 and abstract_words == 0:
        reasons.append("zero_content")
    # A record containing only an abstract (or a very small empty shell) is a
    # non-substantive item for a full-body reading packet.
    substantive_body = [
        section
        for section in sections
        if _words(str(section.get("text") or ""))
        and _normalise_text(section.get("section_type")) != "abstract"
    ]
    if not substantive_body:
        reasons.append("non_substantive")
    if any(item in publication_type for item in NON_SUBSTANTIVE_TYPES):
        reasons.append("non_substantive")
    # Deduplicate while preserving the documented precedence/order.
    return list(dict.fromkeys(reasons))


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _candidate_key(candidate: dict[str, Any]) -> str:
    return str(candidate.get("pmcid") or candidate.get("paper_id") or "")


def _diversity_select(candidates: list[dict[str, Any]], per_stratum: int) -> list[dict[str, Any]]:
    """Greedily maximize unseen journal/kind/design/year categories.

    Every tie is resolved with a SHA-256 key derived from the stable paper ID,
    so selection does not depend on filesystem order or Python hash randomization.
    """

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped[str(candidate["primary_stratum"])].append(candidate)
    selected: list[dict[str, Any]] = []
    for stratum in sorted(grouped):
        pool = list(grouped[stratum])
        used_journals: set[str] = set()
        used_kinds: set[str] = set()
        used_features: set[str] = set()
        used_years: set[str] = set()
        # Explicit includes are honored first, even when they make a stratum
        # larger than --per-stratum.  The limit controls only balanced fill.
        explicit = [candidate for candidate in pool if candidate.get("explicit_include")]
        explicit.sort(key=lambda candidate: _stable_hash(f"{stratum}\0{_candidate_key(candidate)}"))
        for rank, candidate in enumerate(explicit, start=1):
            selected.append(candidate)
            pool.remove(candidate)
            candidate["stratum_rank"] = rank
            candidate["selection_reason"] = "explicit_include"
            used_journals.add(str(candidate.get("journal") or "Unknown"))
            used_kinds.add(str(candidate.get("article_kind") or "other_substantive"))
            used_features.update(str(value) for value in candidate.get("design_features") or [])
            used_years.add(str(candidate.get("year") or "Unknown"))
        fill_count = max(0, per_stratum - len(explicit))
        for offset in range(min(fill_count, len(pool))):
            scored: list[tuple[tuple[int, int, int, int], str, dict[str, Any]]] = []
            for candidate in pool:
                journal = str(candidate.get("journal") or "Unknown")
                kind = str(candidate.get("article_kind") or "other_substantive")
                features = set(str(value) for value in candidate.get("design_features") or [])
                year = str(candidate.get("year") or "Unknown")
                novelty = (
                    int(journal not in used_journals),
                    int(kind not in used_kinds),
                    int(bool(features - used_features)),
                    int(year not in used_years),
                )
                # Maximize novelty dimensions; then hash-stable ID order.
                tie = _stable_hash(f"{stratum}\0{_candidate_key(candidate)}")
                scored.append((novelty, tie, candidate))
            scored.sort(key=lambda item: (-item[0][0], -item[0][1], -item[0][2], -item[0][3], item[1]))
            candidate = scored[0][2]
            selected.append(candidate)
            pool.remove(candidate)
            used_journals.add(str(candidate.get("journal") or "Unknown"))
            used_kinds.add(str(candidate.get("article_kind") or "other_substantive"))
            used_features.update(str(value) for value in candidate.get("design_features") or [])
            used_years.add(str(candidate.get("year") or "Unknown"))
            candidate["stratum_rank"] = len(explicit) + offset + 1
            candidate["selection_reason"] = "balanced_stratum"
    return selected


def _chunk_text(text: str, chunk_words: int) -> list[str]:
    tokens = _words(text)
    return [" ".join(tokens[index : index + chunk_words]) for index in range(0, len(tokens), chunk_words)]


def _section_locator_rows(record: dict[str, Any], chunk_words: int) -> list[dict[str, Any]]:
    """Compute packet section/chunk metadata without materializing packet text."""

    rows: list[dict[str, Any]] = []
    for section_index, section in enumerate(_non_reference_sections(record), start=1):
        heading = re.sub(
            r"\s+",
            " ",
            str(section.get("heading") or section.get("section_type") or "Section"),
        ).strip()
        text = str(section.get("text") or "").strip()
        rows.append(
            {
                "section_locator": f"S{section_index:03d}",
                "heading": heading,
                "section_type": str(section.get("section_type") or "other"),
                "chunk_count": len(_chunk_text(text, chunk_words)),
                "word_count": len(_words(text)),
            }
        )
    return rows


def _packet_markdown(
    candidate: dict[str, Any],
    record: dict[str, Any],
    source_path: Path,
    source_hash: str,
    chunk_words: int,
) -> tuple[str, list[dict[str, Any]]]:
    sections = _non_reference_sections(record)
    lines = [
        "# Unannotated body-reading packet",
        "",
        "This is unannotated reading material. It contains source metadata and complete non-reference section text; no semantic interpretation or annotations have been added.",
        "",
        "## Metadata",
        "",
        f"- PMCID: `{candidate['pmcid']}`",
        f"- Paper ID: `{record.get('paper_id') or candidate.get('paper_id') or ''}`",
        f"- Title: {str(record.get('title') or '').strip()}",
        f"- Journal: {str(record.get('journal') or candidate.get('journal') or 'Unknown').strip()}",
        f"- Year: {record.get('year') if record.get('year') is not None else candidate.get('year') or 'Unknown'}",
        f"- DOI: `{record.get('doi') or ''}`",
        f"- Primary stratum: `{candidate['primary_stratum']}`",
        f"- Article kind cue: `{candidate['article_kind']}`",
        f"- Primary design feature cue: `{candidate.get('design_feature') or ''}`",
        f"- Design feature cues: `{', '.join(candidate['design_features'])}`",
        f"- Source record: `{source_path.as_posix() if isinstance(source_path, Path) else source_path}`",
        f"- Source SHA-256: `{source_hash}`",
        "",
        "## Reading text",
        "",
    ]
    locator_rows = _section_locator_rows(record, chunk_words)
    locator_by_key = {row["section_locator"]: row for row in locator_rows}
    for section_index, section in enumerate(sections, start=1):
        section_locator = f"S{section_index:03d}"
        heading = re.sub(r"\s+", " ", str(section.get("heading") or section.get("section_type") or "Section")).strip()
        lines.extend([f"## {section_locator}: {heading}", ""])
        text = str(section.get("text") or "").strip()
        chunks = _chunk_text(text, chunk_words)
        # ``_section_locator_rows`` is the single source of deterministic
        # counts; retain the local chunk list only for writing body text.
        section_row = locator_by_key.get(section_locator)
        if section_row is None:  # defensive fallback for malformed records
            section_row = {
                "section_locator": section_locator,
                "heading": heading,
                "section_type": str(section.get("section_type") or "other"),
                "chunk_count": len(chunks),
                "word_count": len(_words(text)),
            }
            locator_rows.append(section_row)
        for chunk_index, chunk in enumerate(chunks, start=1):
            locator = f"{section_locator}:C{chunk_index:02d}"
            lines.extend([f"### {locator}", "", chunk, ""])
    return "\n".join(lines).rstrip() + "\n", locator_rows


def _packet_source_hash(path: Path) -> str:
    """Read the source-record hash embedded in packet metadata."""

    try:
        with path.open("rb") as stream:
            header = stream.read(64 * 1024).decode("utf-8-sig", errors="replace")
    except OSError:
        return ""
    match = PACKET_SOURCE_HASH_RE.search(header)
    return match.group(1).casefold() if match else ""


def _default_card_schema() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "card_id": "",
        "paper_id": "",
        "pmcid": "",
        "source_record_sha256": "",
        "reading": {
            "status": "pending",
            "packet_sha256": "",
            "section_locators_read": [],
            "omissions": [],
        },
        "bibliography": {},
        "study": {
            "article_kind": "",
            "design_features": [],
        },
        "status": "blank",
    }


def _load_card_schema(path: Path | None) -> dict[str, Any]:
    if path is None:
        return _default_card_schema()
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError("--card-schema must point to a JSON object")
    return value


def _write_card_stub(path: Path, candidate: dict[str, Any], schema: dict[str, Any]) -> str:
    if path.exists():
        return "existing_preserved"
    card = json.loads(json.dumps(schema, ensure_ascii=False))
    card.setdefault("schema_version", SCHEMA_VERSION)
    card["card_id"] = f"semantic:{candidate.get('pmcid')}"
    card["paper_id"] = candidate.get("paper_id") or ""
    card["pmcid"] = candidate.get("pmcid") or ""
    card["source_record_sha256"] = candidate.get("source_record_sha256") or candidate.get("source_hash") or ""
    reading = card.setdefault("reading", {})
    if not isinstance(reading, dict):
        reading = {}
        card["reading"] = reading
    reading["status"] = "pending"
    reading["packet_sha256"] = candidate.get("packet_sha256") or ""
    reading.setdefault("section_locators_read", [])
    reading.setdefault("omissions", [])
    bibliography = card.setdefault("bibliography", {})
    if not isinstance(bibliography, dict):
        bibliography = {}
        card["bibliography"] = bibliography
    bibliography.update(
        {
            "title": candidate.get("title") or "",
            "authors": candidate.get("authors") or "",
            "year": candidate.get("year"),
            "journal": candidate.get("journal") or "",
            "doi": candidate.get("doi"),
            "publication_type": candidate.get("publication_type") or "",
            "discovery_strata": candidate.get("discovery_strata") or [],
        }
    )
    study = card.setdefault("study", {})
    if not isinstance(study, dict):
        study = {}
        card["study"] = study
    study["article_kind"] = candidate.get("article_kind") or ""
    study["design_features"] = candidate.get("design_features") or []
    # Keep an explicit blank marker when a minimal schema includes one, while
    # the nested reading status remains the contract's pending state.
    card.setdefault("status", "blank")
    # Queue preparation is resumable: a card stub is created atomically and
    # never replaced when the path already exists.  Completed semantic cards
    # therefore remain untouched across every batch.
    _atomic_write_json(path, card)
    return "created"


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Replace *path* atomically, keeping interrupted queue runs recoverable."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def _atomic_write_json(path: Path, value: Any) -> None:
    _atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _atomic_write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    payload = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows
    )
    _atomic_write_text(path, payload)


def _read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL artifact while retaining row order for queue idempotence."""

    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}: {exc.msg}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_number}")
            rows.append(value)
    return rows


def _merge_rows_by_pmcid(
    existing: list[dict[str, Any]],
    incoming: Iterable[dict[str, Any]],
    *,
    update_existing: bool,
) -> list[dict[str, Any]]:
    """Merge PMCID rows without dropping concurrent append-only work."""

    incoming_rows = [dict(row) for row in incoming]
    incoming_by_key = {
        _pmcid_key(row.get("pmcid")): row for row in incoming_rows if _pmcid_key(row.get("pmcid"))
    }
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for old_row in existing:
        key = _pmcid_key(old_row.get("pmcid"))
        if update_existing and key and key in incoming_by_key:
            updated = dict(old_row)
            old_order = old_row.get("reading_order")
            updated.update(incoming_by_key[key])
            if old_order not in (None, ""):
                updated["reading_order"] = old_order
            merged.append(updated)
        else:
            merged.append(dict(old_row))
        if key:
            seen.add(key)
    for row in incoming_rows:
        key = _pmcid_key(row.get("pmcid"))
        if key and key not in seen:
            merged.append(row)
            seen.add(key)
    for reading_order, row in enumerate(merged, start=1):
        if row.get("reading_order") in (None, ""):
            row["reading_order"] = reading_order
        if not row.get("source_record") and row.get("record_path"):
            row["source_record"] = row["record_path"]
    return merged


def _manifest_rows(path: Path) -> list[dict[str, Any]]:
    """Load and validate a manifest, adding an internal stable line ordinal."""

    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")
    rows: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid manifest JSON at {path}:{line_number}: {exc.msg}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"Manifest row must be a JSON object at {path}:{line_number}")
            pmcid = str(value.get("pmcid") or "").strip()
            key = _pmcid_key(pmcid)
            if not key:
                raise ValueError(f"Manifest row has no PMCID at {path}:{line_number}")
            if key in seen:
                raise ValueError(
                    f"Duplicate PMCID {pmcid!r} in manifest lines {seen[key]} and {line_number}"
                )
            seen[key] = line_number
            row = dict(value)
            row["_manifest_line"] = line_number
            row["_manifest_rank"] = len(rows) + 1
            row["_pmcid"] = pmcid
            rows.append(row)
    return rows


def _card_is_completed(path: Path) -> bool:
    """Return true for a completed card without treating malformed cards as writable."""

    if not path.exists():
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        # A malformed existing card is still user material; preserve it rather
        # than allowing queue preparation to overwrite it.
        return True
    if not isinstance(value, dict):
        return True
    reading = value.get("reading")
    status = reading.get("status") if isinstance(reading, dict) else value.get("status")
    return str(status or "").strip().casefold() in {"completed", "complete", "done", "read"}


def _manifest_candidate(
    manifest_row: dict[str, Any],
    record: dict[str, Any],
    source_path: Path,
    source_hash: str,
    root: Path,
) -> tuple[dict[str, Any], list[str]]:
    """Build a selection row from one manifest/record pair.

    In full-manifest mode lexical screening remains an auditable cue, but does
    not remove a manifest paper from the queue.  The active manifest is the
    user's explicit reading universe; a correction or short record is marked
    in ``screening_reasons`` while still receiving a packet.
    """

    sections = _non_reference_sections(record)
    cue_text = _cue_text(record, sections)
    article_kind = _classify_article_kind(record, cue_text)
    design_features = _classify_design_features(record, cue_text, sections)
    reasons = _exclusion_reasons(record, sections, cue_text)
    primary_stratum = _primary_stratum(
        {"strata": manifest_row.get("discovery_strata") or manifest_row.get("strata") or []},
        record,
    )
    pmcid = str(manifest_row.get("_pmcid") or manifest_row.get("pmcid") or record.get("pmcid") or "").strip()
    title = str(record.get("title") or manifest_row.get("title") or "").strip()
    journal = str(record.get("journal") or manifest_row.get("journal") or "Unknown").strip() or "Unknown"
    body_word_count = sum(len(_words(str(section.get("text") or ""))) for section in sections)
    candidate = {
        "pmcid": pmcid,
        "paper_id": str(record.get("paper_id") or manifest_row.get("paper_id") or ""),
        "title": title,
        "authors": str(record.get("authors") or manifest_row.get("authors") or ""),
        "journal": journal,
        "year": record.get("year") if record.get("year") is not None else manifest_row.get("year"),
        "doi": record.get("doi") if record.get("doi") is not None else manifest_row.get("doi"),
        "publication_type": str(record.get("publication_type") or manifest_row.get("publication_type") or ""),
        "discovery_strata": list(
            record.get("discovery_strata")
            or manifest_row.get("discovery_strata")
            or manifest_row.get("strata")
            or []
        ),
        "primary_stratum": primary_stratum,
        "article_kind": article_kind,
        "design_features": design_features,
        "design_feature": design_features[0],
        "body_word_count": body_word_count,
        "record_path": _relative_path(source_path, root),
        "source_hash": source_hash,
        "source_record_sha256": source_hash,
        "manifest_rank": int(manifest_row.get("_manifest_rank") or 0),
        "manifest_line": int(manifest_row.get("_manifest_line") or 0),
        "explicit_include": False,
        "selection_reason": "manifest_all",
        "status": "selected",
        "screening_reasons": reasons,
    }
    return candidate, reasons


def _screening_row(candidate: dict[str, Any], *, selected: bool = True) -> dict[str, Any]:
    """Return a compact auditable screening row without source text."""

    return {
        "manifest_rank": candidate.get("manifest_rank"),
        "pmcid": candidate.get("pmcid"),
        "paper_id": candidate.get("paper_id"),
        "title": candidate.get("title"),
        "journal": candidate.get("journal"),
        "year": candidate.get("year"),
        "primary_stratum": candidate.get("primary_stratum"),
        "article_kind": candidate.get("article_kind"),
        "design_features": candidate.get("design_features") or [],
        "design_feature": candidate.get("design_feature"),
        "body_word_count": candidate.get("body_word_count", 0),
        "record_path": candidate.get("record_path"),
        "screening_reasons": candidate.get("screening_reasons") or [],
        # A manifest queue intentionally retains every manifest row; this
        # field distinguishes a lexical cue from an exclusion decision.
        "status": "included" if selected else "excluded",
        "selected": bool(selected),
        "selection_reason": candidate.get("selection_reason") if selected else None,
    }


def _prepare_manifest_queue(args: argparse.Namespace) -> dict[str, Any]:
    """Prepare/resume the full-manifest semantic packet queue.

    Selection rows are the durable queue ledger.  A row is appended only after
    its packet exists, so a process interruption leaves either the previous
    complete state or an unselected manifest row to retry.  Existing card
    files (especially completed cards) are never overwritten.
    """

    if args.queue_limit is not None and args.queue_limit < 0:
        raise ValueError("--limit/--batch-size must be non-negative")
    if args.queue_offset < 0:
        raise ValueError("--offset/--batch-offset must be non-negative")
    if args.include_pmcid:
        raise ValueError("--include-pmcid is for audit-sample mode; omit it with --all-manifest")

    root = args.root.expanduser().resolve()
    manifest_path = args.manifest_path
    if manifest_path is None:
        manifest_path = root / "manifest.jsonl"
    elif not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    manifest_path = manifest_path.expanduser().resolve()
    rows = _manifest_rows(manifest_path)
    manifest_by_key = {_pmcid_key(row["_pmcid"]): row for row in rows}
    records_dir = root / "records"
    if not records_dir.is_dir():
        raise FileNotFoundError(f"Records directory not found: {records_dir}")
    record_paths = _record_index(records_dir)
    semantic_root = root / "semantic-distillation"
    packet_dir = semantic_root / "packets"
    card_dir = semantic_root / "cards"
    selection_path = semantic_root / "selection.jsonl"
    screening_path = semantic_root / "screening.jsonl"
    selection_report_path = semantic_root / "reports" / "selection.json"
    queue_state_path = semantic_root / "reports" / "queue-state.json"

    existing_selection = _read_jsonl_rows(selection_path)
    existing_by_key: dict[str, dict[str, Any]] = {}
    existing_order: list[str] = []
    duplicate_existing: list[str] = []
    for row in existing_selection:
        key = _pmcid_key(row.get("pmcid"))
        if not key:
            # Preserve legacy rows exactly, but they are not counted as a
            # manifest identity and remain visible to the validator.
            existing_order.append("")
            continue
        if key in existing_by_key:
            duplicate_existing.append(str(row.get("pmcid") or ""))
            continue
        existing_by_key[key] = row
        existing_order.append(key)

    manifest_keys = {_pmcid_key(row["_pmcid"]) for row in rows}
    existing_manifest_keys = [key for key in existing_by_key if key in manifest_keys]
    orphan_existing = [key for key in existing_by_key if key not in manifest_keys]
    missing_rows = [row for row in rows if _pmcid_key(row["_pmcid"]) not in existing_by_key]
    # ``--offset`` addresses the immutable manifest order, not the shrinking
    # unselected queue.  Re-running the same offset/limit therefore observes
    # the same identities and is a no-op after the first successful run.
    batch_window = rows[args.queue_offset :]
    if args.queue_limit is not None:
        batch_window = batch_window[: args.queue_limit]
    batch_rows = [
        row for row in batch_window if _pmcid_key(row["_pmcid"]) not in existing_by_key
    ]

    # Repair rows from interrupted/partially copied runs before consuming new
    # queue slots.  This is intentionally independent of --limit: a missing
    # packet is a recoverable artifact, not semantic reading progress.
    repair_rows: list[dict[str, Any]] = []
    for row in existing_selection:
        pmcid = str(row.get("pmcid") or "").strip()
        if not pmcid or _pmcid_key(pmcid) not in manifest_keys:
            continue
        canonical = packet_dir / f"{pmcid}.md"
        packet_value = str(row.get("packet_path") or "").strip()
        alternate = Path(packet_value) if packet_value else None
        packet_exists = canonical.exists()
        if not packet_exists and alternate is not None:
            packet_exists = (alternate if alternate.is_absolute() else root / alternate).exists()
        if not packet_exists:
            repair_rows.append(row)

    card_schema = _load_card_schema(args.card_schema) if args.card_stubs else None
    loaded_records = 0
    packet_written = 0
    packet_preserved = 0
    packet_missing_records = 0
    card_created = 0
    card_preserved = 0
    errors: list[str] = []
    new_selection_rows: list[dict[str, Any]] = []
    new_screening_rows: list[dict[str, Any]] = []
    updated_existing_rows: dict[str, dict[str, Any]] = {}

    work: list[tuple[str, dict[str, Any], bool]] = []
    # Repairs retain their original selection row and do not create a second
    # identity.  New rows are processed in manifest order for stable output.
    work.extend(("repair", row, True) for row in repair_rows)
    work.extend(("new", row, False) for row in batch_rows)

    for kind, source_row, is_existing in work:
        if kind == "repair":
            pmcid = str(source_row.get("pmcid") or "").strip()
            manifest_row = manifest_by_key.get(_pmcid_key(pmcid))
            if manifest_row is None:
                continue
        else:
            manifest_row = source_row
            pmcid = str(manifest_row["_pmcid"])
        record_path = record_paths.get(_pmcid_key(pmcid))
        if record_path is None:
            packet_missing_records += 1
            errors.append(f"record missing for manifest PMCID {pmcid}")
            continue
        if args.dry_run:
            # Dry runs intentionally avoid decompressing 10k records.  The
            # manifest and record filename join are enough to report queue
            # coverage and missing inputs.
            continue
        try:
            record = _load_record(record_path)
            source_hash = sha256_file(record_path)
            loaded_records += 1
            candidate, reasons = _manifest_candidate(
                manifest_row, record, record_path, source_hash, root
            )
        except Exception as exc:
            errors.append(f"{pmcid}: {exc}")
            continue

        packet_path = packet_dir / f"{pmcid}.md"
        packet_exists = packet_path.exists()
        locator_rows: list[dict[str, Any]] = []
        if not packet_exists:
            packet_value = str(source_row.get("packet_path") or "").strip() if is_existing else ""
            alternate = Path(packet_value) if packet_value else None
            if alternate is not None:
                alternate_path = alternate if alternate.is_absolute() else root / alternate
                if alternate_path.exists():
                    packet_path = alternate_path
                    packet_exists = True
        card_path = card_dir / f"{pmcid}.json"
        completed_card = _card_is_completed(card_path)
        if completed_card and not packet_exists:
            errors.append(
                f"{pmcid}: completed card has no recoverable packet; restore the packet matching its recorded hash"
            )
            continue
        packet_source_hash = _packet_source_hash(packet_path) if packet_exists else ""
        stale_packet = packet_exists and packet_source_hash != source_hash.casefold()
        if stale_packet and completed_card:
            errors.append(
                f"{pmcid}: completed card packet source hash is stale or missing "
                f"({packet_source_hash or 'missing'} != {source_hash})"
            )
            continue
        can_overwrite_packet = (
            (bool(args.overwrite_packets) and not completed_card)
            or (stale_packet and not completed_card)
        )
        if packet_exists and not can_overwrite_packet:
            packet_preserved += 1
            packet_hash = sha256_file(packet_path)
            locator_rows = _section_locator_rows(record, args.chunk_words)
        else:
            packet_text, locator_rows = _packet_markdown(
                candidate,
                record,
                _relative_path(record_path, root),
                source_hash,
                args.chunk_words,
            )
            with StateLock(semantic_root / "luna-state"):
                if _card_is_completed(card_path):
                    raise RuntimeError(
                        f"{pmcid}: card became completed before packet write; refusing to alter frozen reading material"
                    )
                _atomic_write_text(packet_path, packet_text)
            packet_written += 1
            packet_hash = sha256_file(packet_path)
        candidate.update(
            {
                "packet_path": _relative_path(packet_path, root),
                "packet_sha256": packet_hash,
                "packet_status": "ready",
            }
        )
        candidate["sections"] = locator_rows
        candidate["section_count"] = len(locator_rows)
        candidate["chunk_count"] = sum(int(item["chunk_count"]) for item in locator_rows)
        if args.card_stubs and card_schema is not None:
            with StateLock(semantic_root / "luna-state"):
                card_status = _write_card_stub(card_path, candidate, card_schema)
            if card_status == "existing_preserved":
                card_preserved += 1
            else:
                card_created += 1
            candidate["card_status"] = "available"
        if is_existing:
            # Preserve all existing fields and only fill packet identity needed
            # to make a repaired row auditable.  Never alter a completed card.
            merged = dict(source_row)
            for key in (
                "packet_path",
                "packet_sha256",
                "packet_status",
                "source_record_sha256",
                "source_hash",
                "record_path",
            ):
                merged[key] = candidate.get(key, merged.get(key))
            updated_existing_rows[_pmcid_key(pmcid)] = merged
        else:
            candidate.pop("screening_reasons", None)
            new_selection_rows.append(candidate)
            candidate_for_screening = dict(candidate)
            candidate_for_screening["screening_reasons"] = reasons
            new_screening_rows.append(_screening_row(candidate_for_screening))

    # Dry-run planning counts packets without reading records.  Existing rows
    # are considered ready when their canonical/declared packet exists.
    if args.dry_run:
        for row in batch_rows:
            pmcid = str(row["_pmcid"])
            if record_paths.get(_pmcid_key(pmcid)) is None:
                continue
            canonical = packet_dir / f"{pmcid}.md"
            if canonical.exists():
                packet_preserved += 1
            else:
                packet_written += 1
        for row in repair_rows:
            pmcid = str(row.get("pmcid") or "")
            if not pmcid or record_paths.get(_pmcid_key(pmcid)) is None:
                continue
            if pmcid and (packet_dir / f"{pmcid}.md").exists():
                packet_preserved += 1
            else:
                packet_written += 1

    # Existing rows first, then manifest-order additions.  This keeps the
    # original 45 selection lines stable while making later batches append-only
    # at the logical queue level.
    final_selection: list[dict[str, Any]] = []
    appended_keys: set[str] = set()
    for row in existing_selection:
        key = _pmcid_key(row.get("pmcid"))
        if key and key in updated_existing_rows:
            final_selection.append(updated_existing_rows[key])
        else:
            final_selection.append(row)
        if key:
            appended_keys.add(key)
    for row in new_selection_rows:
        key = _pmcid_key(row.get("pmcid"))
        if key and key not in appended_keys:
            final_selection.append(row)
            appended_keys.add(key)

    normalized_selection = False
    for reading_order, row in enumerate(final_selection, start=1):
        if row.get("reading_order") in (None, ""):
            row["reading_order"] = reading_order
            normalized_selection = True
        if not row.get("source_record") and row.get("record_path"):
            row["source_record"] = row["record_path"]
            normalized_selection = True

    existing_screening = _read_jsonl_rows(screening_path)
    final_screening = list(existing_screening)
    screening_keys = {_pmcid_key(row.get("pmcid")) for row in existing_screening if row.get("pmcid")}
    for row in new_screening_rows:
        key = _pmcid_key(row.get("pmcid"))
        if key and key not in screening_keys:
            final_screening.append(row)
            screening_keys.add(key)

    selection_changed = bool(new_selection_rows or updated_existing_rows or normalized_selection)
    screening_changed = bool(new_screening_rows)
    if not args.dry_run:
        with StateLock(semantic_root / "luna-state"):
            latest_selection = _read_jsonl_rows(selection_path)
            selection_delta = list(updated_existing_rows.values()) + list(new_selection_rows)
            committed_selection = _merge_rows_by_pmcid(
                latest_selection,
                selection_delta,
                update_existing=True,
            )
            if selection_changed or committed_selection != latest_selection:
                _atomic_write_jsonl(selection_path, committed_selection)
            final_selection = committed_selection

            latest_screening = _read_jsonl_rows(screening_path)
            committed_screening = _merge_rows_by_pmcid(
                latest_screening,
                new_screening_rows,
                update_existing=False,
            )
            if screening_changed or committed_screening != latest_screening:
                _atomic_write_jsonl(screening_path, committed_screening)
            final_screening = committed_screening

    manifest_hash = sha256_file(manifest_path)
    planned_new = (
        sum(1 for row in batch_rows if record_paths.get(_pmcid_key(row.get("_pmcid"))))
        if args.dry_run
        else len(new_selection_rows)
    )
    selected_total = (
        len({_pmcid_key(row.get("pmcid")) for row in final_selection if _pmcid_key(row.get("pmcid")) in manifest_keys})
        if not args.dry_run
        else len(existing_manifest_keys) + planned_new
    )
    remaining = max(0, len(rows) - selected_total)
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "corpus_root": str(root),
        "queue_mode": "all_manifest",
        "source_manifest": {
            "path": _relative_path(manifest_path, root),
            "sha256": manifest_hash,
            "requested_size": len(rows),
            "actual_size": len(rows),
            "selection_basis": "manifest.jsonl PMCID join; every row retained",
        },
        "method": {
            "selection": "manifest-order append with existing selection rows preserved",
            "classification": "metadata/headings/abstract cues only; labels are selection cues, not semantic interpretation",
            "packet": "complete non-reference section text, chunked with stable S###:C## locators",
            "unannotated_reading_material": True,
            "recovery": "selection.jsonl is the durable ledger; packets are atomically replaced; cards are create-if-absent",
        },
        "config": {
            "all_manifest": True,
            "manifest": _relative_path(manifest_path, root),
            "chunk_words": args.chunk_words,
            "overwrite_packets": bool(args.overwrite_packets),
            "offset": args.queue_offset,
            "limit": args.queue_limit,
            "dry_run": bool(args.dry_run),
            "card_stubs": bool(args.card_stubs),
        },
        "counts": {
            "manifest_rows": len(rows),
            "existing_selection_rows": len(existing_selection),
            "existing_manifest_selected": len(existing_manifest_keys),
            "existing_orphan_selection": len(orphan_existing),
            "duplicate_existing_selection": len(duplicate_existing),
            "remaining_before_batch": len(missing_rows),
            "batch_window": len(batch_window),
            "batch_requested": len(batch_rows),
            "repair_rows": len(repair_rows),
            "new_selected": planned_new,
            "new_selection_written": len(new_selection_rows),
            "selected_total": selected_total,
            "remaining_after_batch": remaining,
            "loaded_records": loaded_records,
            "packet_written": packet_written,
            "packet_preserved": packet_preserved,
            "packet_missing_records": packet_missing_records,
            "card_created": card_created,
            "card_preserved": card_preserved,
            "errors": len(errors),
        },
        "queue": {
            "offset": args.queue_offset,
            "limit": args.queue_limit,
            "next_offset": args.queue_offset + len(batch_window),
            "complete": (not args.dry_run) and remaining == 0 and not errors,
            "would_complete": args.dry_run and remaining == 0 and not errors,
            "selected_papers": [row.get("pmcid") for row in final_selection if row.get("pmcid")],
        },
        "outputs": {
            "selection": _relative_path(selection_path, root),
            "screening": _relative_path(screening_path, root),
            "packets": _relative_path(packet_dir, root),
            "report": _relative_path(selection_report_path, root),
            "queue_state": _relative_path(queue_state_path, root),
        },
        "errors": errors,
    }
    if not args.dry_run:
        with StateLock(semantic_root / "luna-state"):
            live_selection = _read_jsonl_rows(selection_path)
            live_selected = len(
                {_pmcid_key(row.get("pmcid")) for row in live_selection if _pmcid_key(row.get("pmcid")) in manifest_keys}
            )
            live_remaining = max(0, len(rows) - live_selected)
            report["counts"]["selected_total"] = live_selected
            report["counts"]["remaining_after_batch"] = live_remaining
            report["queue"]["complete"] = live_remaining == 0 and not errors
            report["queue"]["selected_papers"] = [row.get("pmcid") for row in live_selection if row.get("pmcid")]
            _atomic_write_json(selection_report_path, report)
            _atomic_write_json(queue_state_path, report)
    return report


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    if args.all_manifest:
        return _prepare_manifest_queue(args)
    if args.per_stratum <= 0:
        raise ValueError("--per-stratum must be positive")
    if args.chunk_words <= 0:
        raise ValueError("--chunk-words must be positive")
    root = args.root.expanduser().resolve()
    reports_dir = root / "reports"
    records_dir = root / "records"
    patterns_path = reports_dir / "writing-patterns.json"
    if not patterns_path.exists():
        raise FileNotFoundError(f"Writing-pattern report not found: {patterns_path}")
    if not records_dir.is_dir():
        raise FileNotFoundError(f"Records directory not found: {records_dir}")
    patterns = read_json(patterns_path)
    sample_rows = _sample_rows(patterns)
    record_paths = _record_index(records_dir)
    screening: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    loaded_records: dict[str, tuple[dict[str, Any], Path, str]] = {}
    include_values: list[str] = []
    include_keys: set[str] = set()
    for value in args.include_pmcid or []:
        cleaned = str(value or "").strip()
        key = _pmcid_key(cleaned)
        if cleaned and key and key not in include_keys:
            include_values.append(cleaned)
            include_keys.add(key)
    seen_sample_keys: set[str] = set()

    def process_sample_row(sample_row: dict[str, Any], explicit_requested: bool) -> None:
        pmcid_value = str(sample_row.get("pmcid") or "").strip()
        sample_key = _pmcid_key(pmcid_value)
        path = record_paths.get(_pmcid_key(pmcid_value))
        base: dict[str, Any] = {
            "sample_index": sample_row.get("sample_index"),
            "pmcid": pmcid_value,
            "paper_id": sample_row.get("paper_id"),
            "sample_strata": sample_row.get("strata") if isinstance(sample_row.get("strata"), list) else [],
            "explicit_include_requested": explicit_requested,
            "status": "excluded",
            "selected": False,
        }
        if sample_key and sample_key in seen_sample_keys:
            base.update({"screening_reasons": ["duplicate_pmcid"], "record_path": _relative_path(path, root) if path else None})
            screening.append(base)
            return
        if sample_key:
            seen_sample_keys.add(sample_key)
        if path is None:
            base.update({"screening_reasons": ["missing_record"], "record_path": None})
            screening.append(base)
            return
        try:
            record = _load_record(path)
            sections = _non_reference_sections(record)
            cue_text = _cue_text(record, sections)
            article_kind = _classify_article_kind(record, cue_text)
            design_features = _classify_design_features(record, cue_text, sections)
            reasons = _exclusion_reasons(record, sections, cue_text)
            primary_stratum = _primary_stratum(sample_row, record)
            source_hash = sha256_file(path)
            body_word_count = sum(len(_words(str(section.get("text") or ""))) for section in sections)
            candidate = {
                "pmcid": str(record.get("pmcid") or pmcid_value),
                "paper_id": str(record.get("paper_id") or sample_row.get("paper_id") or ""),
                "title": str(record.get("title") or sample_row.get("title") or ""),
                "authors": str(record.get("authors") or sample_row.get("authors") or ""),
                "journal": str(record.get("journal") or sample_row.get("journal") or "Unknown"),
                "year": record.get("year") if record.get("year") is not None else sample_row.get("year"),
                "doi": record.get("doi") if record.get("doi") is not None else sample_row.get("doi"),
                "publication_type": str(record.get("publication_type") or sample_row.get("publication_type") or ""),
                "discovery_strata": list(record.get("discovery_strata") or sample_row.get("strata") or []),
                "primary_stratum": primary_stratum,
                "article_kind": article_kind,
                "design_features": design_features,
                "design_feature": design_features[0],
                "body_word_count": body_word_count,
                "record_path": _relative_path(path, root),
                "source_hash": source_hash,
                "explicit_include": explicit_requested,
            }
            base.update(
                {
                    "record_path": candidate["record_path"],
                    "title": candidate["title"],
                    "journal": candidate["journal"],
                    "year": candidate["year"],
                    "primary_stratum": primary_stratum,
                    "article_kind": article_kind,
                    "design_features": design_features,
                    "design_feature": design_features[0],
                    "body_word_count": body_word_count,
                    "screening_reasons": reasons,
                }
            )
            loaded_records[candidate["pmcid"]] = (record, path, source_hash)
            if not reasons:
                base["status"] = "included"
                candidates.append(candidate)
            screening.append(base)
        except Exception as exc:  # Keep a malformed sample row auditable and continue.
            base.update({"screening_reasons": ["record_error"], "record_error": str(exc)[:500], "record_path": _relative_path(path, root)})
            screening.append(base)

    for sample_row in sample_rows:
        sample_key = _pmcid_key(sample_row.get("pmcid"))
        process_sample_row(sample_row, sample_key in include_keys)
    # An explicit PMCID may be outside the 400-paper audit sample.  It is still
    # eligible when a parsed record is present; its discovery stratum is taken
    # from the record metadata by _primary_stratum.
    for pmcid_value in include_values:
        if _pmcid_key(pmcid_value) not in seen_sample_keys:
            process_sample_row(
                {"pmcid": pmcid_value, "paper_id": None, "strata": [], "sample_index": None},
                True,
            )

    selected = _diversity_select(candidates, args.per_stratum)
    selected_by_key = {_candidate_key(candidate): candidate for candidate in selected}
    selected_keys = set(selected_by_key)
    for row in screening:
        if row.get("status") == "included":
            row["selected"] = _candidate_key(row) in selected_keys
            if row["selected"]:
                row["selection_reason"] = selected_by_key[_candidate_key(row)].get("selection_reason")
        elif row.get("explicit_include_requested"):
            row["selection_reason"] = "explicit_include_ineligible"

    semantic_root = root / "semantic-distillation"
    packet_dir = semantic_root / "packets"
    card_dir = semantic_root / "cards"
    selection_rows: list[dict[str, Any]] = []
    card_schema = _load_card_schema(args.card_schema) if args.card_stubs else None
    packet_status_counts: Counter[str] = Counter()
    card_status_counts: Counter[str] = Counter()
    for candidate in selected:
        record, source_path, source_hash = loaded_records[candidate["pmcid"]]
        packet_path = packet_dir / f"{candidate['pmcid']}.md"
        packet_status = "existing_preserved"
        locator_rows: list[dict[str, Any]] = []
        had_packet = packet_path.exists()
        card_path = card_dir / f"{candidate['pmcid']}.json"
        completed_card = _card_is_completed(card_path)
        if completed_card and not packet_path.exists():
            raise RuntimeError(
                f"refusing to regenerate missing packet for completed card {candidate['pmcid']}; "
                "restore the packet matching its recorded hash first"
            )
        packet_source_hash = _packet_source_hash(packet_path) if packet_path.exists() else ""
        stale_packet = packet_path.exists() and packet_source_hash != source_hash.casefold()
        if stale_packet and completed_card:
            raise RuntimeError(
                f"refusing to overwrite stale packet for completed card {candidate['pmcid']} "
                f"({packet_source_hash or 'missing'} != {source_hash})"
            )
        if (args.overwrite_packets and not completed_card) or stale_packet or not packet_path.exists():
            packet_text, locator_rows = _packet_markdown(candidate, record, _relative_path(source_path, root), source_hash, args.chunk_words)
            with StateLock(semantic_root / "luna-state"):
                if _card_is_completed(card_path):
                    raise RuntimeError(
                        f"card became completed before packet write for {candidate['pmcid']}; refusing concurrent overwrite"
                    )
                _atomic_write_text(packet_path, packet_text)
            packet_status = "written_overwrite" if args.overwrite_packets and had_packet else "written"
        else:
            # Existing packets remain useful outputs even when no rewrite was
            # requested; report locators only when we generated the file.
            packet_status = "existing_preserved"
        if not locator_rows:
            # Compute the manifest without writing the packet, so selection.jsonl
            # remains informative when a packet was preserved.
            _, locator_rows = _packet_markdown(candidate, record, _relative_path(source_path, root), source_hash, args.chunk_words)
        packet_sha256 = sha256_file(packet_path)
        candidate["packet_path"] = _relative_path(packet_path, root)
        candidate["source_record_sha256"] = source_hash
        candidate["packet_sha256"] = packet_sha256
        # Keep selection.jsonl deterministic across reruns.  The packet is
        # guaranteed to be present after this block; whether it was newly
        # written or preserved is an operational detail, not selection data.
        candidate["packet_status"] = "ready"
        candidate["sections"] = locator_rows
        candidate["section_count"] = len(locator_rows)
        candidate["chunk_count"] = sum(int(section["chunk_count"]) for section in locator_rows)
        packet_status_counts["ready"] += 1
        if args.card_stubs and card_schema is not None:
            with StateLock(semantic_root / "luna-state"):
                card_status = _write_card_stub(card_dir / f"{candidate['pmcid']}.json", candidate, card_schema)
            # As with packets, expose a stable availability label rather than
            # making selection output depend on whether this is the first run.
            card_status_counts["available"] += 1
            candidate["card_status"] = "available"
        selection_rows.append(dict(candidate))

    # Stable order is primary stratum then within-stratum selection rank and ID.
    selection_rows.sort(key=lambda row: (str(row.get("primary_stratum")), int(row.get("stratum_rank", 0)), _candidate_key(row)))
    for reading_order, row in enumerate(selection_rows, start=1):
        row["reading_order"] = reading_order
        # Keep relative source paths only; no absolute path enters JSONL.
        row["source_record"] = row.get("record_path") or f"records/{row['pmcid']}.json.gz"

    selection_path = semantic_root / "selection.jsonl"
    screening_path = semantic_root / "screening.jsonl"
    selection_report_path = semantic_root / "reports" / "selection.json"
    with StateLock(semantic_root / "luna-state"):
        selection_rows = _merge_rows_by_pmcid(
            _read_jsonl_rows(selection_path),
            selection_rows,
            update_existing=True,
        )
        screening = _merge_rows_by_pmcid(
            _read_jsonl_rows(screening_path),
            screening,
            update_existing=False,
        )
        _atomic_write_jsonl(selection_path, selection_rows)
        _atomic_write_jsonl(screening_path, screening)

    by_stratum: dict[str, dict[str, int]] = {}
    for row in screening:
        stratum_value = row.get("primary_stratum")
        if not stratum_value and row.get("sample_strata"):
            stratum_value = row["sample_strata"][0]
        stratum = str(stratum_value or "unclassified")
        item = by_stratum.setdefault(stratum, {"screened": 0, "eligible": 0, "selected": 0})
        item["screened"] += 1
        item["eligible"] += int(row.get("status") == "included")
        item["selected"] += int(bool(row.get("selected")))
    reason_counts = Counter(reason for row in screening for reason in row.get("screening_reasons") or [])
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "corpus_root": str(root),
        "source_sample": {
            "path": _relative_path(patterns_path, root),
            "requested_size": patterns.get("qualitative_audit_sample", {}).get("requested_size"),
            "actual_size": len(sample_rows),
            "selection_basis": "qualitative_audit_sample.papers PMCID join",
        },
        "method": {
            "selection": "deterministic greedy diversity across primary stratum, journal, article kind, design feature, and year",
            "classification": "metadata/headings/abstract cues only; labels are selection cues, not semantic interpretation",
            "packet": "complete non-reference section text, chunked with stable S###:C## locators",
            "unannotated_reading_material": True,
        },
        "config": {
            "per_stratum": args.per_stratum,
            "chunk_words": args.chunk_words,
            "overwrite_packets": bool(args.overwrite_packets),
            "include_pmcids": include_values,
            "card_stubs": bool(args.card_stubs),
        },
        "counts": {
            "sample_rows": len(sample_rows),
            "screened": len(screening),
            "eligible": len(candidates),
            "selected": len(selection_rows),
            "screening_reasons": dict(sorted(reason_counts.items())),
            "packet_status": dict(sorted(packet_status_counts.items())),
            "card_status": dict(sorted(card_status_counts.items())),
        },
        "strata": by_stratum,
        "selected_papers": [row["pmcid"] for row in selection_rows],
        "outputs": {
            "selection": _relative_path(selection_path, root),
            "screening": _relative_path(screening_path, root),
            "packets": _relative_path(packet_dir, root),
            "report": _relative_path(selection_report_path, root),
        },
    }
    with StateLock(semantic_root / "luna-state"):
        # Re-read the committed ledger so a concurrent append cannot be hidden
        # by a stale report writer.
        live_selection = _read_jsonl_rows(selection_path)
        report["counts"]["selected"] = len(live_selection)
        report["selected_papers"] = [row.get("pmcid") for row in live_selection if row.get("pmcid")]
        _atomic_write_json(selection_report_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = prepare(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "queue_mode": report.get("queue_mode", "audit_sample"),
                "sample_rows": report["counts"].get("sample_rows"),
                "eligible": report["counts"].get("eligible"),
                "selected": report["counts"].get("selected"),
                "manifest_rows": report["counts"].get("manifest_rows"),
                "existing_manifest_selected": report["counts"].get("existing_manifest_selected"),
                "new_selected": report["counts"].get("new_selected"),
                "remaining_after_batch": report["counts"].get("remaining_after_batch"),
                "packet_written": report["counts"].get("packet_written"),
                "packet_preserved": report["counts"].get("packet_preserved"),
                "dry_run": report.get("config", {}).get("dry_run", False),
                "selection": report["outputs"]["selection"],
                "screening": report["outputs"]["screening"],
                "packets": report["outputs"]["packets"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
