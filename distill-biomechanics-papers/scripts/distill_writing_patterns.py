#!/usr/bin/env python3
"""Distill section structure and rhetorical patterns from a full-text corpus.

The corpus directory should contain paired ``<id>.txt`` and ``<id>.bib`` files.
The text may come from Zotero's indexed full-text API or another lawful local
source. Outputs contain metrics and short diagnostic excerpts, never the source
documents themselves.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from _common import normalize_doi, utc_now, write_json


SECTION_ALIASES = {
    "abstract": "abstract",
    "introduction": "introduction",
    "background": "introduction",
    "method": "methods",
    "methods": "methods",
    "methodology": "methods",
    "materials and methods": "methods",
    "materials & methods": "methods",
    "experimental methods": "methods",
    "results": "results",
    "results and discussion": "results_discussion",
    "results & discussion": "results_discussion",
    "discussion": "discussion",
    "limitations": "limitations",
    "conclusion": "conclusion",
    "conclusions": "conclusion",
    "references": "references",
    "bibliography": "references",
}

MOVE_PATTERNS = {
    "problem": (
        r"\b(?:problem|challenge|stress shielding|failure|defect|clinical need|drawback)s?\b",
        r"\b(?:however|nevertheless|despite|although)\b",
    ),
    "gap": (
        r"\b(?:few studies|little is known|remains? (?:unclear|unknown|to be)|has not been|have not been)\b",
        r"\b(?:limited|lack of|to the authors.? best knowledge|not yet)\b",
    ),
    "aim": (
        r"\b(?:this study|the present study|this work) (?:aims?|sought|investigates?|examines?|develops?|proposes?)\b",
        r"\b(?:the objective|the purpose|we (?:aim|propose|develop|investigate|examine|evaluate))\b",
    ),
    "novelty": (
        r"\b(?:novel|for the first time|first study|new approach|original contribution)\b",
    ),
    "method_action": (
        r"\b(?:was|were) (?:used|employed|modeled|modelled|simulated|fabricated|generated|measured|tested|calculated|derived|validated)\b",
        r"\bwe (?:used|employed|modeled|modelled|simulated|fabricated|generated|measured|tested|calculated|derived|validated)\b",
    ),
    "result": (
        r"\b(?:results?|findings?) (?:show|showed|indicate|indicated|demonstrate|demonstrated|reveal|revealed)\b",
        r"\b(?:increased|decreased|reduced|improved|higher|lower|significant(?:ly)?)\b",
    ),
    "comparison": (
        r"\b(?:compared with|compared to|relative to|whereas|in contrast|than the)\b",
    ),
    "mechanism": (
        r"\b(?:because|due to|attributed to|resulted from|mechanism|explained by)\b",
    ),
    "caution": (
        r"\b(?:may|might|could|appears? to|suggests?|likely|potentially)\b",
    ),
    "limitation": (
        r"\b(?:limitation|limited by|should be interpreted|caution|future work|further validation)\b",
    ),
    "implication": (
        r"\b(?:implication|potential for|could be used|may provide|useful tool|clinical application|design guideline)\b",
    ),
}

DOMAIN_PATTERNS = {
    "finite_element_biomechanics": r"finite element|biomechan|spine|joint|muscle load|bone modulus",
    "bone_implant": r"implant|prosthe|fixation|bone plate|fusion cage|stress shielding",
    "topology_optimization": r"topolog|simp|level set|design optimization",
    "porous_scaffold": r"porous|scaffold|lattice|tpms|gyroid|trabecular",
    "additive_manufacturing": r"additive manufact|3d print|selective laser|laser powder|slm|fabricat",
    "mechanobiology": r"remodel|bone growth|mechanobiolog|osseointegr",
}

WORD_RE = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)?")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
HEADING_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?"
    r"(abstract|introduction|background|materials\s+(?:and|&)\s+methods|experimental\s+methods|"
    r"methodology|methods?|results\s+(?:and|&)\s+discussion|results|discussion|limitations|"
    r"conclusions?|references|bibliography)\b",
    re.IGNORECASE,
)
SUBHEADING_RE = re.compile(r"^\s*\d+(?:\.\d+)+\.?\s+[A-Za-z][^=]{1,90}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path, help="Directory containing paired .txt and .bib files")
    parser.add_argument("--out-json", type=Path, help="Machine-readable report path")
    parser.add_argument("--out-md", type=Path, help="Human-readable report path")
    parser.add_argument("--max-excerpts", type=int, default=2, help="Short diagnostic excerpts per rhetorical move")
    return parser.parse_args()


def parse_bibtex(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    metadata: dict[str, str] = {}
    for name in ("title", "author", "journal", "journaltitle", "year", "doi", "keywords", "abstract"):
        match = re.search(
            rf"(?ims)^\s*{re.escape(name)}\s*=\s*(?:\{{((?:[^{{}}]|\{{[^{{}}]*\}})*)\}}|\"([^\"]*)\")\s*,?",
            text,
        )
        if match:
            value = match.group(1) if match.group(1) is not None else match.group(2)
            value = re.sub(r"\\[&%_]", lambda found: found.group(0)[1:], value)
            value = value.replace("{", "").replace("}", "").replace("~", " ")
            metadata["journal" if name == "journaltitle" else name] = re.sub(r"\s+", " ", value).strip()
    key_match = re.search(r"(?m)^@\w+\{([^,]+),", text)
    if key_match:
        metadata["citekey"] = key_match.group(1).strip()
    metadata["doi"] = normalize_doi(metadata.get("doi")) or ""
    return metadata


def clean_text(text: str) -> str:
    text = text.replace("\r", "").replace("\f", "\n")
    text = re.sub(r"(?<=\w)-\n(?=[a-z])", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def canonical_heading(raw: str) -> str:
    normalized = re.sub(r"\s+", " ", raw.casefold().replace("&", "and")).strip(" .:")
    return SECTION_ALIASES.get(normalized, normalized)


def split_sections(text: str) -> tuple[dict[str, str], list[str]]:
    lines = text.splitlines()
    starts: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        compact = re.sub(r"\s+", " ", line).strip()
        if len(compact) > 100:
            continue
        match = HEADING_RE.match(compact)
        if not match:
            continue
        raw = match.group(1)
        section = canonical_heading(raw)
        if starts and starts[-1][1] == section and index - starts[-1][0] < 3:
            continue
        starts.append((index, section, compact))

    sections: dict[str, str] = {}
    headings: list[str] = []
    for position, (start, section, heading) in enumerate(starts):
        if section == "references":
            break
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        body = "\n".join(lines[start + 1 : end]).strip()
        if not body:
            continue
        headings.append(heading)
        if section in sections:
            sections[section] += "\n" + body
        else:
            sections[section] = body
    return sections, headings


def sentences(text: str) -> list[str]:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return []
    values = [value.strip() for value in SENTENCE_RE.split(compact)]
    return [value for value in values if len(WORD_RE.findall(value)) >= 4]


def short_excerpt(sentence: str, word_limit: int = 18) -> str:
    words = sentence.split()
    clipped = words[:word_limit]
    value = " ".join(clipped)
    if len(words) > word_limit:
        value += " …"
    return value


def safe_mean(values: list[float]) -> float:
    return round(statistics.mean(values), 2) if values else 0.0


def safe_stdev(values: list[float]) -> float:
    return round(statistics.pstdev(values), 2) if len(values) > 1 else 0.0


def count_markers(text: str) -> dict[str, int]:
    return {
        name: sum(len(re.findall(pattern, text, flags=re.IGNORECASE)) for pattern in patterns)
        for name, patterns in MOVE_PATTERNS.items()
    }


def move_sequence(text: str) -> list[str]:
    positions: list[tuple[int, str]] = []
    for name, patterns in MOVE_PATTERNS.items():
        found = [match.start() for pattern in patterns for match in re.finditer(pattern, text, flags=re.IGNORECASE)]
        if found:
            positions.append((min(found), name))
    return [name for _, name in sorted(positions)]


def extract_move_examples(section_map: dict[str, str], limit: int) -> dict[str, list[dict[str, str]]]:
    examples: dict[str, list[dict[str, str]]] = defaultdict(list)
    if limit <= 0:
        return {}
    for section_name, section_text in section_map.items():
        for sentence in sentences(section_text):
            for move_name, patterns in MOVE_PATTERNS.items():
                if len(examples[move_name]) >= limit:
                    continue
                if any(re.search(pattern, sentence, flags=re.IGNORECASE) for pattern in patterns):
                    examples[move_name].append({"section": section_name, "excerpt": short_excerpt(sentence)})
    return dict(examples)


def section_metrics(text: str) -> dict[str, Any]:
    section_sentences = sentences(text)
    lengths = [len(WORD_RE.findall(value)) for value in section_sentences]
    words = WORD_RE.findall(text)
    marker_counts = count_markers(text)
    denominator = max(len(words), 1)
    return {
        "words": len(words),
        "sentences": len(section_sentences),
        "sentence_words_mean": safe_mean([float(value) for value in lengths]),
        "sentence_words_sd": safe_stdev([float(value) for value in lengths]),
        "citations": len(re.findall(r"\[[0-9][0-9,\-\u2013\s]*\]|\([A-Z][A-Za-z-]+(?:\s+et\s+al\.)?,?\s+\d{4}[a-z]?\)", text)),
        "first_person": len(re.findall(r"\b(?:we|our|us)\b", text, flags=re.IGNORECASE)),
        "passive_candidates": len(
            re.findall(
                r"\b(?:is|are|was|were|be|been|being)\s+(?:\w+ly\s+)?\w+(?:ed|en)\b",
                text,
                flags=re.IGNORECASE,
            )
        ),
        "moves": marker_counts,
        "moves_per_1000_words": {name: round(count * 1000 / denominator, 2) for name, count in marker_counts.items()},
        "move_sequence": move_sequence(text),
    }


def classify_domains(metadata: dict[str, str], text: str) -> list[str]:
    haystack = " ".join((metadata.get("title", ""), metadata.get("keywords", ""), text[:5000]))
    return [name for name, pattern in DOMAIN_PATTERNS.items() if re.search(pattern, haystack, flags=re.IGNORECASE)]


def analyze_paper(text_path: Path, max_excerpts: int) -> dict[str, Any]:
    bib_path = text_path.with_suffix(".bib")
    metadata = parse_bibtex(bib_path) if bib_path.is_file() else {}
    text = clean_text(text_path.read_text(encoding="utf-8-sig", errors="replace"))
    section_map, headings = split_sections(text)
    subheadings = []
    for line in text.splitlines():
        compact = re.sub(r"\s+", " ", line).strip()
        if SUBHEADING_RE.match(compact) and len(compact.split()) <= 14:
            subheadings.append(compact)
    if not section_map:
        section_map = {"unsegmented": text}
    section_samples = {}
    for name, body in section_map.items():
        values = sentences(body)
        section_samples[name] = {
            "opening": [short_excerpt(value) for value in values[:2]],
            "closing": [short_excerpt(value) for value in values[-2:]],
        }
    return {
        "source_id": text_path.stem,
        "metadata": metadata,
        "domains": classify_domains(metadata, text),
        "detected_headings": headings,
        "detected_subheadings": subheadings,
        "sections": {name: section_metrics(body) for name, body in section_map.items()},
        "section_samples": section_samples,
        "move_examples": extract_move_examples(section_map, max_excerpts),
    }


def aggregate(papers: list[dict[str, Any]]) -> dict[str, Any]:
    section_presence: Counter[str] = Counter()
    section_sentence_means: dict[str, list[float]] = defaultdict(list)
    section_words: dict[str, list[float]] = defaultdict(list)
    move_totals: Counter[str] = Counter()
    move_sequences: dict[str, Counter[str]] = defaultdict(Counter)
    domain_totals: Counter[str] = Counter()
    journals: Counter[str] = Counter()
    title_lengths: list[float] = []
    colon_titles = 0

    for paper in papers:
        domain_totals.update(paper["domains"])
        journal = paper["metadata"].get("journal") or "Unknown"
        journals[journal] += 1
        title = paper["metadata"].get("title") or ""
        if title:
            title_lengths.append(float(len(WORD_RE.findall(title))))
            colon_titles += int(":" in title)
        for section_name, metrics in paper["sections"].items():
            section_presence[section_name] += 1
            section_sentence_means[section_name].append(metrics["sentence_words_mean"])
            section_words[section_name].append(metrics["words"])
            move_totals.update(metrics["moves"])
            sequence = ">".join(metrics["move_sequence"])
            if sequence:
                move_sequences[section_name][sequence] += 1

    section_summary = {}
    for section_name in sorted(section_presence):
        section_summary[section_name] = {
            "papers": section_presence[section_name],
            "mean_words": safe_mean(section_words[section_name]),
            "mean_sentence_words": safe_mean(section_sentence_means[section_name]),
        }
    return {
        "papers": len(papers),
        "titles": {
            "mean_words": safe_mean(title_lengths),
            "colon_ratio": round(colon_titles / max(len(title_lengths), 1), 3),
        },
        "journals": dict(journals.most_common()),
        "domains": dict(domain_totals.most_common()),
        "sections": section_summary,
        "rhetorical_moves": dict(move_totals.most_common()),
        "common_move_sequences": {
            section: [{"sequence": sequence, "papers": count} for sequence, count in counts.most_common(5)]
            for section, counts in sorted(move_sequences.items())
        },
    }


def markdown_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Writing Corpus Distillation Report",
        "",
        f"Generated: {report['generated_at']}",
        f"Papers analyzed: {summary['papers']}",
        "",
        "## Corpus",
        "",
        "| ID | Year | Journal | Title | Domains |",
        "|---|---:|---|---|---|",
    ]
    for paper in report["papers"]:
        meta = paper["metadata"]
        values = [
            paper["source_id"],
            meta.get("year", ""),
            (meta.get("journal") or "Unknown").replace("|", "/"),
            (meta.get("title") or "Untitled").replace("|", "/"),
            ", ".join(paper["domains"]),
        ]
        lines.append("| " + " | ".join(values) + " |")

    lines.extend(["", "## Section profile", "", "| Section | Papers | Mean words | Mean sentence length |", "|---|---:|---:|---:|"])
    for section, values in summary["sections"].items():
        lines.append(
            f"| {section} | {values['papers']} | {values['mean_words']:.1f} | {values['mean_sentence_words']:.1f} |"
        )

    lines.extend(["", "## Rhetorical moves", "", "| Move | Occurrences |", "|---|---:|"])
    for move, count in summary["rhetorical_moves"].items():
        lines.append(f"| {move} | {count} |")

    lines.extend(["", "## Common move sequences", ""])
    for section, rows in summary["common_move_sequences"].items():
        rendered = "; ".join(f"{row['sequence']} ({row['papers']})" for row in rows)
        lines.append(f"- **{section}:** {rendered}")

    lines.extend(["", "## Per-paper diagnostics", ""])
    for paper in report["papers"]:
        meta = paper["metadata"]
        lines.append(f"### {paper['source_id']} — {meta.get('title', 'Untitled')}")
        lines.append("")
        lines.append("Sections: " + ", ".join(paper["sections"].keys()))
        for move, examples in paper["move_examples"].items():
            rendered = "; ".join(f"{item['section']}: “{item['excerpt']}”" for item in examples)
            lines.append(f"- {move}: {rendered}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    corpus = args.corpus.resolve()
    if not corpus.is_dir():
        print(f"ERROR: Corpus directory not found: {corpus}", file=sys.stderr)
        return 1
    text_paths = sorted(corpus.glob("*.txt"))
    if not text_paths:
        print(f"ERROR: No .txt files found under {corpus}", file=sys.stderr)
        return 1

    try:
        papers = [analyze_paper(path, max(args.max_excerpts, 0)) for path in text_paths]
        report = {
            "schema_version": "1.0",
            "generated_at": utc_now(),
            "corpus": str(corpus),
            "summary": aggregate(papers),
            "papers": papers,
        }
        if args.out_json:
            write_json(args.out_json.resolve(), report)
        if args.out_md:
            output = args.out_md.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(markdown_report(report), encoding="utf-8", newline="\n")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "papers": len(papers),
                "out_json": str(args.out_json.resolve()) if args.out_json else None,
                "out_md": str(args.out_md.resolve()) if args.out_md else None,
                "domains": report["summary"]["domains"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
