#!/usr/bin/env python3
"""Prepare auditable Luna-reading packets from verified MinerU Markdown.

The converter deliberately makes no semantic judgments.  It preserves source
blocks, assigns stable packet locators, and migrates only pending card stubs.
Completed cards are immutable and malformed/ambiguous Markdown is left pending.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from manage_semantic_reading import StateLock  # noqa: E402


SCHEMA_VERSION = "1.0"
PACKET_SCHEMA_VERSION = "1.1"
DEFAULT_MAX_WRITE_COUNT = 100
SEMANTIC_ROOTS = (
    "argument_map",
    "evidence_boundary",
    "section_moves",
    "limitations",
    "writing_capability_candidates",
    "quality",
    "summary_zh",
    "reader_notes",
)
LOCATOR_RE = re.compile(r"^S\d{3}:C\d{2}$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
REF_HEADING_RE = re.compile(
    r"^(?:[\s\W_]*)(?:\d+(?:\.\d+)*[.\s]*)?(references?|bibliography|literature\s+cited|works\s+cited)\s*$",
    re.I,
)
POST_REF_RE = re.compile(
    r"^(?:acknowledg(?:e)?ments?|funding|author\s+contributions?|data\s+availability|"
    r"conflicts?\s+of\s+interest|institutional\s+review|informed\s+consent|appendix|supplementary)",
    re.I,
)
CANONICAL_HEADING_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*[.\s]*)?(?:abstract|introduction|background|materials?(?:\s+and\s+methods?)?|"
    r"methods?|methodology|results?|discussion|conclusions?|limitations?|appendix|supplementary|"
    r"acknowledg(?:e)?ments?|funding|author\s+contributions?|data\s+availability|conflicts?\s+of\s+interest)\b",
    re.I,
)
CAPTION_RE = re.compile(r"^(?:fig(?:ure)?|scheme|table)\s*[.\-:]?\s*\d+[A-Za-z]?\b", re.I)
REF_ENTRY_RE = re.compile(r"^\s*(?:\[?\d{1,4}\]?\s*[.)]|\d{1,4}\s+)\s*\S+")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^\)]+\)")
HTML_TABLE_OPEN_RE = re.compile(r"<table\b", re.I)
HTML_TABLE_CLOSE_RE = re.compile(r"</table\s*>", re.I)
DETAILS_OPEN_RE = re.compile(r"<details\b", re.I)
DETAILS_CLOSE_RE = re.compile(r"</details\s*>", re.I)
DISPLAY_MATH_FENCE_RE = re.compile(r"^\s*\$\$\s*$")
DISPLAY_MATH_SINGLE_LINE_RE = re.compile(r"^\s*\$\$.+\$\$\s*$")
LATEX_FENCE_OPEN_RE = re.compile(r"^\s*\\\[\s*$")
LATEX_FENCE_CLOSE_RE = re.compile(r"^\s*\\\]\s*$")
WORD_RE = re.compile(r"\b[\w\-]+\b", re.UNICODE)


class PreparationError(RuntimeError):
    pass


@dataclass
class Block:
    kind: str
    text: str
    start_line: int
    end_line: int
    heading_level: int = 0
    heading_text: str = ""
    flags: list[str] = field(default_factory=list)


@dataclass
class Section:
    heading: str
    level: int
    blocks: list[Block]
    section_type: str = "other"


@dataclass
class ParseResult:
    sections: list[Section]
    quality_flags: list[str]
    blocking_reasons: list[str]
    references_detected: bool
    references_inferred: bool
    visual_unavailable: bool
    source_line_count: int
    body_char_count: int

    @property
    def semantic_eligible(self) -> bool:
        return not self.blocking_reasons and bool(self.sections)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PreparationError(f"{path}:{number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(value, dict):
                raise PreparationError(f"{path}:{number}: expected an object")
            rows.append(value)
    return rows


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    atomic_write(path, json_payload(value))


def atomic_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    atomic_write(path, jsonl_payload(rows))


def json_payload(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def jsonl_payload(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows).encode("utf-8")


def normalize_heading(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"[*_`~]+", "", value)
    return re.sub(r"\s+", " ", value).strip(" \t#■◆▪•:-")


def classify_heading(value: str) -> str:
    clean = normalize_heading(value)
    clean = re.sub(r"^\d+(?:\.\d+)*[.\s]*", "", clean).casefold()
    for name in ("abstract", "introduction", "background", "methods", "results", "discussion", "conclusion", "limitations", "appendix", "supplementary"):
        if clean.startswith(name) or (name == "methods" and clean.startswith("materials and methods")):
            return name
    return "other"


def _is_markdown_table(lines: Sequence[str], index: int) -> bool:
    if index + 1 >= len(lines) or "|" not in lines[index]:
        return False
    return bool(re.match(r"^\s*\|?\s*:?-{3,}", lines[index + 1])) and "|" in lines[index + 1]


def lex_markdown(text: str) -> tuple[list[Block], list[str]]:
    lines = text.splitlines()
    blocks: list[Block] = []
    errors: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        start = index
        lower = line.casefold()
        if HTML_TABLE_OPEN_RE.search(line):
            group = [line]
            while not any(HTML_TABLE_CLOSE_RE.search(item) for item in group):
                index += 1
                if index >= len(lines):
                    errors.append("unclosed_html_table")
                    break
                group.append(lines[index])
            blocks.append(Block("html_table", "\n".join(group), start + 1, min(index + 1, len(lines))))
            index += 1
            continue
        if DETAILS_OPEN_RE.search(line):
            group = [line]
            while not any(DETAILS_CLOSE_RE.search(item) for item in group):
                index += 1
                if index >= len(lines):
                    errors.append("unclosed_details")
                    break
                group.append(lines[index])
            joined = "\n".join(group)
            kind = "image_ocr" if re.search(r"text_image|heatmap", joined, re.I) else "details"
            blocks.append(Block(kind, joined, start + 1, min(index + 1, len(lines)), flags=["visual_unavailable"] if kind == "image_ocr" else []))
            index += 1
            continue
        if DISPLAY_MATH_SINGLE_LINE_RE.match(line):
            blocks.append(Block("display_math", line, start + 1, start + 1))
            index += 1
            continue
        if DISPLAY_MATH_FENCE_RE.match(line) or LATEX_FENCE_OPEN_RE.match(line):
            group = [line]
            close = DISPLAY_MATH_FENCE_RE if DISPLAY_MATH_FENCE_RE.match(line) else LATEX_FENCE_CLOSE_RE
            index += 1
            while index < len(lines):
                group.append(lines[index])
                if close.match(lines[index]):
                    break
                index += 1
            else:
                errors.append("unclosed_display_math")
            blocks.append(Block("display_math", "\n".join(group), start + 1, min(index + 1, len(lines))))
            index += 1
            continue
        match = HEADING_RE.match(line)
        if match:
            heading = normalize_heading(match.group(2))
            kind = "caption" if CAPTION_RE.match(heading) else "heading"
            blocks.append(Block(kind, line, start + 1, start + 1, len(match.group(1)), heading))
            index += 1
            continue
        if _is_markdown_table(lines, index):
            group = [line, lines[index + 1]]
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                group.append(lines[index])
                index += 1
            blocks.append(Block("markdown_table", "\n".join(group), start + 1, index))
            continue
        if IMAGE_RE.search(line):
            group = [line]
            index += 1
            while index < len(lines) and CAPTION_RE.match(normalize_heading(lines[index])):
                group.append(lines[index])
                index += 1
            blocks.append(Block("image", "\n".join(group), start + 1, index, flags=["visual_unavailable"]))
            continue
        # Normal paragraph/list block.  It may span wrapped lines, but stops
        # before any atomic construct so source order remains exact.
        group = [line]
        index += 1
        while index < len(lines) and lines[index].strip():
            nxt = lines[index]
            if HEADING_RE.match(nxt) or HTML_TABLE_OPEN_RE.search(nxt) or DETAILS_OPEN_RE.search(nxt) or DISPLAY_MATH_FENCE_RE.match(nxt) or LATEX_FENCE_OPEN_RE.match(nxt) or IMAGE_RE.search(nxt) or _is_markdown_table(lines, index):
                break
            group.append(nxt)
            index += 1
        blocks.append(Block("text", "\n".join(group), start + 1, index))
    return blocks, errors


def parse_markdown(text: str, page_count: int | None = None) -> ParseResult:
    blocks, errors = lex_markdown(text)
    flags: list[str] = []
    blocking = list(errors)
    replacement_count = text.count("\ufffd")
    if replacement_count:
        flags.append(f"replacement_characters:{replacement_count}")
    if any("\ufffd" in block.heading_text for block in blocks if block.kind == "heading"):
        blocking.append("replacement_character_in_heading")
    if replacement_count > 20 or (replacement_count and len(text) / replacement_count < 2000):
        blocking.append("replacement_character_density")

    visual = any("visual_unavailable" in block.flags for block in blocks)
    trusted_headings = 0
    sections: list[Section] = [Section("Front matter", 1, [], "front_matter")]
    in_references = False
    references_detected = False
    references_inferred = False
    for block in blocks:
        if block.kind == "heading":
            clean = block.heading_text
            if REF_HEADING_RE.match(clean):
                in_references = True
                references_detected = True
                continue
            if in_references:
                if POST_REF_RE.match(clean):
                    in_references = False
                else:
                    continue
            # MinerU often emits domain-specific top-level headings such as
            # ``2. Implant design``.  A numeric prefix is strong structural
            # evidence once figure/table captions have already been removed.
            trusted = bool(CANONICAL_HEADING_RE.match(clean) or re.match(r"^\d+(?:\.\d+)*[.\s]+\S", clean))
            if trusted:
                trusted_headings += 1
                sections.append(Section(clean, block.heading_level, [], classify_heading(clean)))
            else:
                # A suspicious H1/H2 from OCR remains visible as ordinary
                # text rather than silently changing reading order.
                block.kind = "text"
                block.flags.append("untrusted_heading")
                sections[-1].blocks.append(block)
            continue
        if not in_references:
            sections[-1].blocks.append(block)

    # Conservative fallback: infer only a dense numbered-reference tail in
    # the final 45% and keep any subsequent trusted post-reference section.
    if not references_detected:
        candidates = [
            (i, b) for i, b in enumerate(blocks)
            if b.start_line >= max(1, int(len(text.splitlines()) * 0.55)) and b.kind == "text" and REF_ENTRY_RE.match(b.text)
        ]
        runs: list[list[Block]] = []
        current: list[Block] = []
        previous = -2
        for index, block in candidates:
            if index > previous + 2 and current:
                runs.append(current)
                current = []
            current.append(block)
            previous = index
        if current:
            runs.append(current)
        inferred = next((run for run in runs if len(run) >= 8), None)
        if inferred:
            start_line = inferred[0].start_line
            for section in sections:
                section.blocks = [block for block in section.blocks if block.start_line < start_line]
            references_inferred = True
            flags.append("references_boundary_inferred")
        else:
            blocking.append("references_boundary_unknown")

    sections = [section for section in sections if any(block.text.strip() for block in section.blocks)]
    body_chars = sum(len(block.text) for section in sections for block in section.blocks if block.kind != "image_ocr")
    if body_chars < 1000:
        blocking.append("body_too_short")
    if page_count and page_count > 0 and body_chars / page_count < 500:
        blocking.append("low_text_density")
    if trusted_headings < 2 and not any(section.section_type == "front_matter" and len(section.blocks) >= 3 for section in sections):
        blocking.append("insufficient_trusted_structure")

    normalized_seen: dict[str, int] = {}
    repeated_chars = 0
    normal_paragraphs = 0
    repeated_paragraphs = 0
    for section in sections:
        for block in section.blocks:
            if block.kind != "text" or len(block.text) < 200:
                continue
            normal_paragraphs += 1
            key = re.sub(r"\s+", " ", block.text).strip().casefold()
            normalized_seen[key] = normalized_seen.get(key, 0) + 1
            if normalized_seen[key] > 1:
                repeated_paragraphs += 1
                repeated_chars += len(block.text)
    if repeated_paragraphs >= 3 or (body_chars and repeated_chars / body_chars > 0.05):
        blocking.append("substantive_repetition")

    return ParseResult(
        sections=sections,
        quality_flags=sorted(set(flags)),
        blocking_reasons=sorted(set(blocking)),
        references_detected=references_detected,
        references_inferred=references_inferred,
        visual_unavailable=visual,
        source_line_count=len(text.splitlines()),
        body_char_count=body_chars,
    )


def block_tokens(block: Block) -> int:
    return max(1, int(len(WORD_RE.findall(block.text)) * 1.3))


def chunk_section(section: Section, token_limit: int) -> list[list[Block]]:
    chunks: list[list[Block]] = []
    current: list[Block] = []
    current_tokens = 0
    for block in section.blocks:
        amount = block_tokens(block)
        if current and current_tokens + amount > token_limit:
            chunks.append(current)
            current = []
            current_tokens = 0
        current.append(block)
        current_tokens += amount
        if amount > token_limit:
            block.flags.append("oversize_atomic_block")
            chunks.append(current)
            current = []
            current_tokens = 0
    if current:
        chunks.append(current)
    return chunks


def render_packet(row: Mapping[str, Any], index_row: Mapping[str, Any], md_rel: str, md_hash: str, pdf_rel: str, pdf_hash: str, prior_source: str, prior_hash: str, parsed: ParseResult, token_limit: int) -> tuple[bytes, list[dict[str, Any]]]:
    lines = [
        "# Unannotated MinerU body-reading packet", "",
        "This is mechanically prepared reading material. No semantic interpretation or annotations have been added.", "",
        "## Metadata", "",
        f"- PMCID: `{row.get('pmcid','')}`",
        f"- Paper ID: `{row.get('paper_id','')}`",
        f"- Title: {row.get('title','')}",
        f"- Journal: {row.get('journal','')}",
        f"- Year: {row.get('year','')}",
        f"- DOI: `{row.get('doi','')}`",
        f"- Primary stratum: `{row.get('primary_stratum','')}`",
        f"- Article kind cue: `{row.get('article_kind','')}`",
        f"- Design feature cues: `{', '.join(row.get('design_features') or [])}`",
        f"- Source record: `{md_rel}`",
        f"- Source SHA-256: `{md_hash}`",
        f"- Source format: `mineru_markdown`",
        f"- Packet schema version: `{PACKET_SCHEMA_VERSION}`",
        f"- MinerU mode: `{index_row.get('preferred_mode','')}`",
        f"- MinerU version: `3.4.4`",
        f"- MinerU profile: `hybrid-engine/high`",
        f"- PDF source: `{pdf_rel}`",
        f"- PDF SHA-256: `{pdf_hash}`",
        f"- PDF page count: `{index_row.get('page_count','')}`",
        f"- Prior JATS source: `{prior_source}`",
        f"- Prior JATS SHA-256: `{prior_hash}`",
        f"- Visual content available to reader: `{'no' if parsed.visual_unavailable else 'not_detected'}`",
        f"- Quality flags: `{', '.join(parsed.quality_flags)}`", "",
        "## Reading text", "",
    ]
    section_rows: list[dict[str, Any]] = []
    for section_number, section in enumerate(parsed.sections, 1):
        sloc = f"S{section_number:03d}"
        lines += [f"## {sloc}: {section.heading}", ""]
        chunks = chunk_section(section, token_limit)
        chunk_rows: list[dict[str, Any]] = []
        for chunk_number, blocks in enumerate(chunks, 1):
            locator = f"{sloc}:C{chunk_number:02d}"
            lines += [f"### {locator}", ""]
            chunk_rows.append(
                {
                    "chunk_locator": locator,
                    "source_line_start": min(block.start_line for block in blocks),
                    "source_line_end": max(block.end_line for block in blocks),
                    "block_types": sorted({block.kind for block in blocks}),
                    "flags": sorted({flag for block in blocks for flag in block.flags}),
                    "word_count": sum(len(WORD_RE.findall(block.text)) for block in blocks),
                }
            )
            for block in blocks:
                flags = ",".join(sorted(set(block.flags))) or "none"
                lines += [
                    f"<!-- MINERU_BLOCK kind={block.kind} source_lines={block.start_line}-{block.end_line} flags={flags} -->",
                    block.text.rstrip(),
                    "",
                ]
        section_rows.append(
            {
                "section_locator": sloc,
                "heading": section.heading,
                "section_type": section.section_type,
                "source_line_start": min(block.start_line for block in section.blocks),
                "source_line_end": max(block.end_line for block in section.blocks),
                "chunk_count": len(chunks),
                "word_count": sum(len(WORD_RE.findall(block.text)) for block in section.blocks),
                "chunks": chunk_rows,
            }
        )
    payload = ("\n".join(lines).rstrip() + "\n").encode("utf-8")
    return payload, section_rows


def card_completed(card: Mapping[str, Any]) -> bool:
    reading = card.get("reading") if isinstance(card.get("reading"), Mapping) else {}
    statuses = {str(reading.get("status") or "").casefold(), str(card.get("status") or "").casefold()}
    return bool(statuses & {"completed", "complete", "done", "read"})


def pending_skeleton_reasons(card: Mapping[str, Any]) -> list[str]:
    """Return reasons why a card is not an untouched pending reading stub."""

    reasons: list[str] = []
    reading = card.get("reading") if isinstance(card.get("reading"), Mapping) else {}
    if str(reading.get("status") or "").casefold() != "pending":
        reasons.append("card_not_pending")
    top_status = str(card.get("status") or "").casefold()
    if top_status not in {"", "blank", "pending"}:
        reasons.append("card_top_status_not_blank")
    if reading.get("section_locators_read") not in (None, []):
        reasons.append("card_has_read_locators")
    if reading.get("omissions") not in (None, []):
        reasons.append("card_has_reading_omissions")
    for key in ("reader_role", "reader_model", "reasoning_effort", "read_at", "adjudication_status", "access"):
        if reading.get(key) not in (None, "", []):
            reasons.append("card_has_reader_provenance")
            break
    if any(card.get(key) not in (None, "", [], {}) for key in SEMANTIC_ROOTS):
        reasons.append("card_has_semantic_content")
    study = card.get("study") if isinstance(card.get("study"), Mapping) else {}
    if any(key not in {"article_kind", "design_features"} for key in study):
        reasons.append("card_has_semantic_study_content")
    return sorted(set(reasons))


def resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base / path


def active_lease_ids(sd: Path) -> set[str]:
    state_path = sd / "luna-state" / "state.json"
    mirror_path = sd / "luna-state" / "leases.json"
    path = state_path if state_path.exists() else mirror_path
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    leases_raw = data.get("leases", []) if isinstance(data, dict) else data if isinstance(data, list) else []
    # StateStore persists leases as ``{lease_id: lease}``, while older test
    # fixtures used a list.  Iterating the mapping itself would yield only
    # lease-id strings and silently miss every active paper.
    leases = leases_raw.values() if isinstance(leases_raw, Mapping) else leases_raw
    ids: set[str] = set()
    for lease in leases:
        if not isinstance(lease, Mapping) or str(lease.get("status", "")).casefold() not in {"active", "leased", "claimed"}:
            continue
        for item in lease.get("items", []):
            if isinstance(item, Mapping):
                ids.add(str(item.get("pmcid") or "").upper())
            elif item:
                ids.add(str(item).upper())
    return ids


def completed_card_evolution(current_payload: bytes, pending_payload: bytes) -> bool:
    """Whether a prepared pending card has legitimately advanced to completed."""

    try:
        current_card = json.loads(current_payload.decode("utf-8-sig"))
        pending_card = json.loads(pending_payload.decode("utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    if not isinstance(current_card, Mapping) or not isinstance(pending_card, Mapping):
        return False
    current_reading = current_card.get("reading") if isinstance(current_card.get("reading"), Mapping) else {}
    pending_reading = pending_card.get("reading") if isinstance(pending_card.get("reading"), Mapping) else {}
    return bool(
        card_completed(current_card)
        and str(current_card.get("source_record_sha256") or "").casefold()
        == str(pending_card.get("source_record_sha256") or "").casefold()
        and str(current_reading.get("packet_sha256") or "").casefold()
        == str(pending_reading.get("packet_sha256") or "").casefold()
    )


def active_reading_material_ids(sd: Path) -> set[str]:
    """Return papers whose current reading material must not be replaced."""

    ids = set(active_lease_ids(sd))
    ids.update(path.stem.upper() for path in (sd / "overlays").glob("*.json"))
    ids.update(
        path.name[: -len(".json.draft")].upper()
        for path in (sd / "overlays").glob("*.json.draft")
    )
    for path in (sd / "luna-state").rglob("*.progress"):
        match = re.search(r"PMC\d+", path.name, re.I)
        if match:
            ids.add(match.group(0).upper())
    return ids


def recover_transactions(sd: Path) -> list[str]:
    """Roll forward prepared migration transactions under the shared lock."""

    recovered: list[str] = []
    tx_root = sd / "migration" / "transactions"
    if not tx_root.exists():
        return recovered
    for tx_dir in sorted(path for path in tx_root.iterdir() if path.is_dir()):
        manifest_path = tx_dir / "transaction.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if str(manifest.get("status") or "") == "committed":
            continue
        transaction_pmcids = {
            str(value or "").upper()
            for value in (manifest.get("pmcids") if isinstance(manifest.get("pmcids"), list) else [])
            if value
        }
        conflicts = sorted(transaction_pmcids & active_reading_material_ids(sd))
        if conflicts:
            raise PreparationError(
                f"transaction recovery blocked by active reading material: {manifest_path} ({', '.join(conflicts)})"
            )
        files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
        for entry in files:
            if not isinstance(entry, Mapping):
                raise PreparationError(f"invalid transaction file entry: {manifest_path}")
            source = tx_dir / str(entry.get("payload") or "")
            target = sd / str(entry.get("target") or "")
            expected = str(entry.get("sha256") or "").casefold()
            old_hash = str(entry.get("old_sha256") or "").casefold()
            if not source.is_file() or sha256_file(source) != expected:
                raise PreparationError(f"transaction payload missing or corrupt: {source}")
            current_hash = sha256_file(target) if target.is_file() else ""
            if current_hash == expected:
                continue
            # A migrated pending card may legitimately have been completed by
            # Luna after the selection/packet commit but before this journal's
            # final status write.  Preserve it only when immutable source and
            # packet identities still match the transaction payload.
            target_rel = str(entry.get("target") or "").replace("\\", "/")
            if target_rel.startswith("cards/") and target_rel.endswith(".json") and target.is_file():
                if completed_card_evolution(target.read_bytes(), source.read_bytes()):
                    continue
            # Roll forward only from the exact state captured at prepare time.
            # Anything else is a concurrent edit and must never be overwritten.
            if current_hash != old_hash:
                raise PreparationError(f"transaction recovery conflict: {target}")
            atomic_write(target, source.read_bytes())
        manifest["status"] = "committed"
        manifest["committed_at"] = utc_now()
        atomic_json(manifest_path, manifest)
        try:
            shutil.rmtree(tx_dir / "payloads")
        except OSError:
            pass
        recovered.append(str(manifest.get("transaction_id") or tx_dir.name))
    return recovered


def prepare_transaction(sd: Path, payloads: Sequence[tuple[str, bytes]], pmcids: Sequence[str]) -> tuple[Path, dict[str, Any]]:
    """Durably stage a bounded roll-forward transaction."""

    tx_id = f"txn-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex}"
    tx_dir = sd / "migration" / "transactions" / tx_id
    payload_dir = tx_dir / "payloads"
    payload_dir.mkdir(parents=True, exist_ok=False)
    files: list[dict[str, Any]] = []
    for number, (target, payload) in enumerate(payloads, 1):
        payload_name = f"payloads/{number:05d}.bin"
        atomic_write(tx_dir / payload_name, payload)
        target_path = sd / target
        files.append(
            {
                "target": target,
                "payload": payload_name,
                "sha256": sha256_bytes(payload),
                "old_sha256": sha256_file(target_path) if target_path.is_file() else "",
            }
        )
    manifest = {
        "schema_version": "1.0",
        "transaction_id": tx_id,
        "status": "prepared",
        "prepared_at": utc_now(),
        "pmcids": list(pmcids),
        "files": files,
    }
    atomic_json(tx_dir / "transaction.json", manifest)
    return tx_dir, manifest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="External corpus root")
    parser.add_argument("--mineru-export", required=True, type=Path, help="MinerU export directory containing index.jsonl")
    parser.add_argument("--pdf-manifest", required=True, type=Path, help="10k PDF manifest.jsonl")
    parser.add_argument("--pdf-root", type=Path, help="Directory relative to which pdf_relpath is resolved (default manifest parent)")
    parser.add_argument("--pmcid", action="append", default=[])
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--chunk-tokens", type=int, default=1500)
    parser.add_argument(
        "--max-write-count",
        type=int,
        default=DEFAULT_MAX_WRITE_COUNT,
        help="Refuse a single write transaction above this many papers (default: 100; use --offset/--limit for batches)",
    )
    parser.add_argument("--write", action="store_true", help="Commit eligible pending migrations; default is dry-run")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = args.root.resolve()
    sd = root / "semantic-distillation"
    recovered_transactions: list[str] = []
    if args.write:
        # Recovery precedes every fresh snapshot, even if this invocation later
        # finds no new eligible rows.  This closes the only gap in which a
        # prepared transaction could otherwise remain stranded indefinitely.
        with StateLock(sd / "luna-state"):
            recovered_transactions = recover_transactions(sd)
    selection_path = sd / "selection.jsonl"
    mineru_index_path = args.mineru_export.resolve() / "index.jsonl"
    pdf_root = (args.pdf_root or args.pdf_manifest.parent).resolve()
    selection = jsonl(selection_path)
    mineru_rows = jsonl(mineru_index_path)
    pdf_rows = jsonl(args.pdf_manifest.resolve())
    index_by_id = {str(row.get("pmcid") or row.get("sample_id") or "").upper(): row for row in mineru_rows}
    pdf_by_id = {str(row.get("pmcid") or "").upper(): row for row in pdf_rows}
    selection_ids = [str(row.get("pmcid") or "").upper() for row in selection]
    selection_id_set = set(selection_ids)
    if (
        len(mineru_rows) != 10000
        or len(pdf_rows) != 10000
        or len(index_by_id) != 10000
        or len(pdf_by_id) != 10000
        or len(selection) != 10000
        or len(selection_id_set) != 10000
        or not all(re.fullmatch(r"PMC\d+", pmcid) for pmcid in selection_id_set)
        or not selection_id_set == set(index_by_id) == set(pdf_by_id)
    ):
        raise PreparationError("selection, MinerU index, and PDF manifest must contain the same 10,000 unique PMCID rows")
    requested = {value.upper() for value in args.pmcid}
    active = active_lease_ids(sd)
    overlay_ids = {path.stem.upper() for path in (sd / "overlays").glob("*.json")}
    overlay_draft_ids = {
        path.name[: -len(".json.draft")].upper()
        for path in (sd / "overlays").glob("*.json.draft")
    }
    checkpoint_ids: set[str] = set()
    for path in (sd / "luna-state").rglob("*.progress"):
        match = re.search(r"PMC\d+", path.name, re.I)
        if match:
            checkpoint_ids.add(match.group(0).upper())
    candidates = selection[args.offset : args.offset + args.limit if args.limit is not None else None]
    if requested:
        candidates = [row for row in selection if str(row.get("pmcid") or "").upper() in requested]

    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "dry_run": not args.write,
        "counts": {"selected": len(selection), "examined": 0, "eligible": 0, "blocked": 0, "completed_frozen": 0, "already_migrated": 0, "ledger_repair_needed": 0, "written": 0},
        "items": [], "blocking_reason_counts": {}, "recovered_transactions": recovered_transactions,
    }
    staged: list[dict[str, Any]] = []
    ledger_repairs: list[dict[str, Any]] = []
    ledger_path = sd / "migration" / "mineru-packet-migrations.jsonl"
    ledger_rows = jsonl(ledger_path) if ledger_path.exists() else []
    ledger_keys = {
        (str(entry.get("pmcid") or "").upper(), str(entry.get("new_packet_sha256") or "").casefold())
        for entry in ledger_rows
    }
    for row in candidates:
        pmcid = str(row.get("pmcid") or "").upper()
        item: dict[str, Any] = {"pmcid": pmcid, "reading_order": row.get("reading_order"), "status": "blocked", "reasons": []}
        summary["counts"]["examined"] += 1
        card_path = sd / "cards" / f"{pmcid}.json"
        card_file_sha256 = ""
        try:
            card_bytes = card_path.read_bytes()
            card_file_sha256 = sha256_bytes(card_bytes)
            card = json.loads(card_bytes.decode("utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            item["reasons"].append(f"card_unreadable:{type(exc).__name__}")
            card = {}
        if card and card_completed(card):
            item["status"] = "completed_frozen"
            summary["counts"]["completed_frozen"] += 1
            summary["items"].append(item)
            continue
        item["reasons"].extend(pending_skeleton_reasons(card))
        if pmcid in active:
            item["reasons"].append("active_lease")
        if pmcid in overlay_ids or pmcid in overlay_draft_ids:
            item["reasons"].append("pending_overlay_or_draft")
        if pmcid in checkpoint_ids:
            item["reasons"].append("reading_checkpoint_present")
        idx = index_by_id.get(pmcid)
        pdf = pdf_by_id.get(pmcid)
        if not idx or not pdf:
            item["reasons"].append("manifest_identity_missing")
        if item["reasons"]:
            pass
        else:
            md_rel_export = str(idx.get("preferred_markdown_relpath") or "")
            md_path = resolve(args.mineru_export.resolve(), md_rel_export) if md_rel_export else Path()
            md_hash = str(idx.get("preferred_markdown_sha256") or "").casefold()
            md_bytes: bytes | None = None
            # The production benchmark manifest records the remote staged
            # path, while the local reproducibility corpus stores the same
            # bytes as ``pdfs/<PMCID>.pdf``.  Legacy acquisition manifests may
            # instead expose ``pdf_relpath`` directly.
            pdf_rel_manifest = str(pdf.get("pdf_relpath") or pdf.get("local_pdf_relpath") or f"pdfs/{pmcid}.pdf")
            pdf_path = resolve(pdf_root, pdf_rel_manifest) if pdf_rel_manifest else Path()
            pdf_rel = os.path.relpath(pdf_path, root).replace("\\", "/") if pdf_rel_manifest else ""
            pdf_hash = str(pdf.get("pdf_sha256") or "").casefold()
            input_kind = str(pdf.get("input_kind") or idx.get("input_kind") or "publisher_pdf")
            if not md_rel_export or not md_path.is_file():
                item["reasons"].append("preferred_markdown_missing")
            else:
                try:
                    md_bytes = md_path.read_bytes()
                except OSError as exc:
                    item["reasons"].append(f"preferred_markdown_unreadable:{type(exc).__name__}")
                else:
                    if sha256_bytes(md_bytes) != md_hash:
                        item["reasons"].append("preferred_markdown_hash_mismatch")
            # An already migrated pending packet has already passed the PDF
            # byte/input gates recorded by its transaction.  Revalidate only
            # the live Markdown/card/packet identities here so incremental
            # scans do not reread thousands of large PDFs on every cycle.
            if not item["reasons"] and str(row.get("source_format") or "") == "mineru_markdown":
                packet_path = sd / "packets" / f"{pmcid}.md"
                reading = card.get("reading") if isinstance(card.get("reading"), Mapping) else {}
                packet_text = packet_path.read_text(encoding="utf-8-sig", errors="replace") if packet_path.is_file() else ""
                material_ok = (
                    str(row.get("source_record_sha256") or row.get("source_hash") or "").casefold() == md_hash
                    and str(card.get("source_record_sha256") or "").casefold() == md_hash
                    and packet_path.is_file()
                    and str(row.get("packet_sha256") or "").casefold() == sha256_file(packet_path)
                    and str(reading.get("packet_sha256") or "").casefold() == str(row.get("packet_sha256") or "").casefold()
                )
                renderer_current = (
                    f"- Packet schema version: `{PACKET_SCHEMA_VERSION}`" in packet_text
                    and f"- PDF source: `{pdf_rel}`" in packet_text
                )
                if material_ok and renderer_current:
                    key = (pmcid, str(row.get("packet_sha256") or "").casefold())
                    if key not in ledger_keys:
                        source_info = card.get("source") if isinstance(card.get("source"), Mapping) else {}
                        ledger_repairs.append(
                            {
                                "schema_version": SCHEMA_VERSION,
                                "migrated_at": utc_now(),
                                "pmcid": pmcid,
                                "old_source_record": source_info.get("prior_jats_record_path"),
                                "old_source_record_sha256": source_info.get("prior_jats_sha256"),
                                "new_source_record": row.get("record_path") or row.get("source_record"),
                                "new_source_record_sha256": md_hash,
                                "old_packet_sha256": None,
                                "new_packet_sha256": row.get("packet_sha256"),
                                "pdf_sha256": pdf_hash,
                                "source_format": "mineru_markdown",
                                "reconciled": True,
                            }
                        )
                        summary["counts"]["ledger_repair_needed"] += 1
                    item.update({"status": "already_migrated", "source_record_sha256": md_hash, "packet_sha256": row.get("packet_sha256")})
                    summary["counts"]["already_migrated"] += 1
                    summary["items"].append(item)
                    continue
                if not material_ok:
                    item["reasons"].append("migrated_material_stale")
                else:
                    item["quality_flags"] = ["packet_renderer_refresh"]
            # A missing Markdown row is not yet a migration candidate, so do
            # not reread its often-large PDF during every incremental dry-run.
            # Candidate PDFs are still fully hashed before any packet can be
            # staged or committed.
            if not item["reasons"]:
                if input_kind != "publisher_pdf":
                    # These few benchmark rows are PDFs rendered from the same
                    # retained JATS source because the publisher PDF vanished.
                    # Their existing JATS packet is more faithful than an OCR
                    # round-trip, so conversion is audited but migration is
                    # intentionally declined.
                    item["reasons"].append("source_preference_existing_jats")
                if not pdf_rel or not pdf_path.is_file():
                    item["reasons"].append("pdf_missing")
                elif sha256_file(pdf_path) != pdf_hash:
                    item["reasons"].append("pdf_manifest_hash_mismatch")
                if str(idx.get("input_sha256") or "").casefold() != pdf_hash:
                    item["reasons"].append("mineru_input_pdf_hash_mismatch")
                if str(idx.get("input_kind") or input_kind) != input_kind:
                    item["reasons"].append("mineru_input_kind_mismatch")
            if not item["reasons"]:
                try:
                    text = (md_bytes or b"").decode("utf-8-sig", errors="strict")
                except UnicodeDecodeError as exc:
                    item["reasons"].append(f"markdown_decode_error:{type(exc).__name__}")
                else:
                    parsed = parse_markdown(text, int(idx.get("page_count") or 0) or None)
                    item["quality_flags"] = parsed.quality_flags
                    item["visual_unavailable"] = parsed.visual_unavailable
                    item["reasons"].extend(parsed.blocking_reasons)
                    if not item["reasons"]:
                        source_rel = os.path.relpath(md_path, root).replace("\\", "/")
                        source_info = card.get("source") if isinstance(card.get("source"), Mapping) else {}
                        prior_source = str(source_info.get("prior_jats_record_path") or row.get("record_path") or row.get("source_record") or "")
                        prior_hash = str(source_info.get("prior_jats_sha256") or row.get("source_record_sha256") or row.get("source_hash") or "")
                        packet, sections = render_packet(row, idx, source_rel, md_hash, pdf_rel, pdf_hash, prior_source, prior_hash, parsed, args.chunk_tokens)
                        packet_hash = sha256_bytes(packet)
                        updated_row = dict(row)
                        updated_row.update({"record_path": source_rel, "source_record": source_rel, "source_hash": md_hash, "source_record_sha256": md_hash, "packet_path": f"semantic-distillation/packets/{pmcid}.md", "packet_sha256": packet_hash, "packet_status": "ready", "source_format": "mineru_markdown", "sections": sections, "section_count": len(sections), "chunk_count": sum(int(s["chunk_count"]) for s in sections), "body_word_count": sum(int(s["word_count"]) for s in sections)})
                        updated_card = dict(card)
                        updated_card["source_record_sha256"] = md_hash
                        reading = dict(updated_card.get("reading") or {})
                        reading.update({"status": "pending", "packet_sha256": packet_hash, "section_locators_read": [], "omissions": []})
                        updated_card["reading"] = reading
                        updated_card.pop("status", None)
                        updated_card["source"] = {"format": "mineru_markdown", "record_path": source_rel, "source_record_sha256": md_hash, "pdf_path": pdf_rel, "pdf_sha256": pdf_hash, "pdf_input_kind": input_kind, "mineru_mode": idx.get("preferred_mode"), "mineru_version": "3.4.4", "mineru_profile": "hybrid-engine/high", "packet_schema_version": PACKET_SCHEMA_VERSION, "parser_quality_flags": parsed.quality_flags, "visual_unavailable": parsed.visual_unavailable, "prior_jats_record_path": prior_source, "prior_jats_sha256": prior_hash}
                        staged.append({"pmcid": pmcid, "row": updated_row, "card": updated_card, "packet": packet, "old_row": row, "old_card_sha256": card_file_sha256, "md_path": md_path, "md_hash": md_hash, "pdf_path": pdf_path, "pdf_hash": pdf_hash, "packet_hash": packet_hash})
                        item.update({"status": "eligible", "packet_sha256": packet_hash, "source_record_sha256": md_hash, "section_count": len(sections), "chunk_count": updated_row["chunk_count"]})
                        summary["counts"]["eligible"] += 1
        if item["reasons"]:
            summary["counts"]["blocked"] += 1
            for reason in item["reasons"]:
                summary["blocking_reason_counts"][reason] = summary["blocking_reason_counts"].get(reason, 0) + 1
        summary["items"].append(item)

    if args.max_write_count < 1:
        raise PreparationError("--max-write-count must be at least 1")
    if args.write and len(staged) > args.max_write_count:
        raise PreparationError(
            f"refusing to write {len(staged)} papers in one transaction; use --offset/--limit batches no larger than {args.max_write_count}"
        )
    if args.write and (staged or ledger_repairs):
        with StateLock(sd / "luna-state"):
            current = jsonl(selection_path)
            by_id = {str(row.get("pmcid") or "").upper(): index for index, row in enumerate(current)}
            ledger_existing = ledger_path.read_text(encoding="utf-8-sig") if ledger_path.exists() else ""
            ledger_new: list[dict[str, Any]] = []
            validated: list[dict[str, Any]] = []
            locked_active = active_lease_ids(sd)
            locked_overlay_ids = {path.stem.upper() for path in (sd / "overlays").glob("*.json")}
            locked_overlay_draft_ids = {
                path.name[: -len(".json.draft")].upper()
                for path in (sd / "overlays").glob("*.json.draft")
            }
            locked_checkpoint_ids: set[str] = set()
            for path in (sd / "luna-state").rglob("*.progress"):
                match = re.search(r"PMC\d+", path.name, re.I)
                if match:
                    locked_checkpoint_ids.add(match.group(0).upper())
            for value in staged:
                pmcid = value["pmcid"]
                index = by_id.get(pmcid)
                if index is None:
                    raise PreparationError(f"selection changed: {pmcid} disappeared")
                current_card_path = sd / "cards" / f"{pmcid}.json"
                current_card_bytes = current_card_path.read_bytes()
                current_card = json.loads(current_card_bytes.decode("utf-8-sig"))
                if card_completed(current_card):
                    raise PreparationError(f"completed card became visible during migration: {pmcid}")
                if pending_skeleton_reasons(current_card):
                    raise PreparationError(f"card is no longer an untouched pending stub: {pmcid}")
                if sha256_bytes(current_card_bytes) != value["old_card_sha256"]:
                    raise PreparationError(f"card changed during migration: {pmcid}")
                if pmcid in locked_overlay_ids or pmcid in locked_overlay_draft_ids or pmcid in locked_active or pmcid in locked_checkpoint_ids:
                    raise PreparationError(f"active reading material appeared during migration: {pmcid}")
                if str(current[index].get("source_record_sha256") or current[index].get("source_hash")) != str(value["old_row"].get("source_record_sha256") or value["old_row"].get("source_hash")):
                    raise PreparationError(f"selection changed during migration: {pmcid}")
                if sha256_file(value["md_path"]) != value["md_hash"] or sha256_file(value["pdf_path"]) != value["pdf_hash"]:
                    raise PreparationError(f"source changed during migration: {pmcid}")
                current[index] = value["row"]
                ledger_new.append({"schema_version": SCHEMA_VERSION, "migrated_at": utc_now(), "pmcid": pmcid, "old_source_record": value["old_row"].get("record_path") or value["old_row"].get("source_record"), "old_source_record_sha256": value["old_row"].get("source_record_sha256") or value["old_row"].get("source_hash"), "new_source_record": value["row"].get("record_path"), "new_source_record_sha256": value["md_hash"], "old_packet_sha256": value["old_row"].get("packet_sha256"), "new_packet_sha256": value["packet_hash"], "pdf_sha256": value["pdf_hash"], "source_format": "mineru_markdown"})
                validated.append(value)
            existing_keys = {
                (str(entry.get("pmcid") or "").upper(), str(entry.get("new_packet_sha256") or "").casefold())
                for entry in jsonl(ledger_path)
            } if ledger_path.exists() else set()
            ledger_new.extend(
                entry for entry in ledger_repairs
                if (str(entry.get("pmcid") or "").upper(), str(entry.get("new_packet_sha256") or "").casefold()) not in existing_keys
            )
            payload = ledger_existing + "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in ledger_new)
            transaction_payloads: list[tuple[str, bytes]] = []
            for value in validated:
                pmcid = value["pmcid"]
                transaction_payloads.extend(
                    [
                        (f"packets/{pmcid}.md", value["packet"]),
                        (f"cards/{pmcid}.json", json_payload(value["card"])),
                    ]
                )
            if staged:
                transaction_payloads.append(("selection.jsonl", jsonl_payload(current)))
            if ledger_new:
                transaction_payloads.append(("migration/mineru-packet-migrations.jsonl", payload.encode("utf-8")))
            if transaction_payloads:
                tx_dir, tx_manifest = prepare_transaction(
                    sd,
                    transaction_payloads,
                    [value["pmcid"] for value in validated] + [str(entry.get("pmcid") or "") for entry in ledger_repairs],
                )
                for entry in tx_manifest["files"]:
                    source = tx_dir / entry["payload"]
                    target = sd / entry["target"]
                    atomic_write(target, source.read_bytes())
                tx_manifest["status"] = "committed"
                tx_manifest["committed_at"] = utc_now()
                atomic_json(tx_dir / "transaction.json", tx_manifest)
                try:
                    shutil.rmtree(tx_dir / "payloads")
                except OSError:
                    pass
            summary["counts"]["written"] = len(staged)
            committed = {value["pmcid"] for value in staged}
            for item in summary["items"]:
                if item.get("pmcid") in committed and item.get("status") == "eligible":
                    item["status"] = "written"
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
    except Exception as exc:
        result = {"schema_version": SCHEMA_VERSION, "dry_run": not bool(getattr(args, "write", False)), "error": str(exc)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None))
    return 0 if result["counts"]["blocked"] == 0 or result["counts"]["eligible"] > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
