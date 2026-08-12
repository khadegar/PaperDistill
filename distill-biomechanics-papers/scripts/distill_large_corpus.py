#!/usr/bin/env python3
"""Aggregate writing patterns from the external parsed full-text corpus.

The input is the ``records/*.json.gz`` directory produced by
``manage_fulltext_corpus.py fetch``.  The report stores corpus-level metrics and
stable paper identifiers only; it never copies source paragraphs into the Skill.
This makes the command suitable for a 10,000-paper corpus without loading the
whole collection into an LLM context window.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from _common import utc_now, write_json
from distill_writing_patterns import MOVE_PATTERNS, WORD_RE, count_markers, move_sequence, sentences


SECTION_ORDER = (
    "abstract",
    "introduction",
    "methods",
    "results",
    "results_discussion",
    "discussion",
    "limitations",
    "conclusion",
    "other",
)

REPORTING_MARKERS = {
    "finite_element_reporting": (
        r"\bfinite element\b",
        r"\bmesh (?:convergence|independence|sensitivity)\b",
        r"\bboundary conditions?\b",
        r"\bmaterial propert(?:y|ies)\b",
        r"\bcontact (?:condition|interaction|definition)s?\b",
    ),
    "topology_optimization_reporting": (
        r"\btopology optimi[sz]ation\b",
        r"\bobjective function\b",
        r"\bvolume fraction\b",
        r"\b(?:simp|level[- ]set)\b",
        r"\bmanufactur(?:ing|ability) constraint\b",
    ),
    "porous_scaffold_reporting": (
        r"\b(?:porosity|pore size|strut diameter)\b",
        r"\b(?:tpms|gyroid|diamond lattice|trabecular)\b",
        r"\bpermeability\b",
        r"\bfatigue (?:life|strength|test)\b",
        r"\bbone ingrowth\b",
    ),
    "additive_manufacturing_reporting": (
        r"\badditive manufactur(?:ing|ed)\b",
        r"\b(?:laser|beam) power\b",
        r"\blayer thickness\b",
        r"\bscan(?:ning)? speed\b",
        r"\bas[- ]built\b",
    ),
    "validation_reporting": (
        r"\b(?:model|numerical|experimental) validation\b",
        r"\bvalidated against\b",
        r"\b(?:bench|cadaveric|in vitro|in vivo) test\b",
        r"\b(?:rmse|root mean square error|coefficient of determination)\b",
        r"\b(?:agreement|repeatability|reproducibility)\b",
    ),
    "uncertainty_reporting": (
        r"\buncertainty\b",
        r"\bsensitivity analysis\b",
        r"\bconfidence interval\b",
        r"\bstandard deviation\b",
        r"\bp\s*[<=>]\s*0?\.\d+\b",
    ),
    "translation_caution": (
        r"\bclinical translation\b",
        r"\bfurther (?:validation|investigation|studies)\b",
        r"\bshould be interpreted with caution\b",
        r"\b(?:may|might|could) (?:improve|support|provide|enable)\b",
        r"\b(?:limitation|limitations)\b",
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="External corpus root")
    parser.add_argument("--out-json", type=Path, help="Defaults to ROOT/reports/writing-patterns.json")
    parser.add_argument("--out-md", type=Path, help="Defaults to ROOT/reports/writing-patterns.md")
    parser.add_argument("--limit", type=int, help="Analyze at most N records (diagnostic use)")
    parser.add_argument(
        "--audit-sample-size",
        type=int,
        default=400,
        help="Deterministic stratified paper-ID sample for later qualitative audit",
    )
    return parser.parse_args()


def percentile(values: list[float], proportion: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * proportion
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(float(ordered[lower]), 2)
    weight = position - lower
    return round(float(ordered[lower] * (1 - weight) + ordered[upper] * weight), 2)


def describe(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"n": 0, "mean": 0.0, "median": 0.0, "p25": 0.0, "p75": 0.0}
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 2),
        "median": round(statistics.median(values), 2),
        "p25": percentile(values, 0.25),
        "p75": percentile(values, 0.75),
    }


def record_paths(root: Path, limit: int | None) -> list[Path]:
    paths = sorted((root / "records").glob("*.json.gz"))
    if limit is not None:
        if limit <= 0:
            raise ValueError("--limit must be positive")
        return paths[:limit]
    return paths


def load_record(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Expected object in {path}")
    return value


def canonical_sections(record: dict[str, Any]) -> tuple[dict[str, str], list[str], list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    order: list[str] = []
    headings: list[str] = []
    for section in record.get("sections") or []:
        if not isinstance(section, dict):
            continue
        name = str(section.get("section_type") or "other")
        text = str(section.get("text") or "").strip()
        heading = re.sub(r"\s+", " ", str(section.get("heading") or "")).strip()
        if not text:
            continue
        grouped[name].append(text)
        if not order or order[-1] != name:
            order.append(name)
        if heading:
            headings.append(heading.casefold())
    return {name: " ".join(parts) for name, parts in grouped.items()}, order, headings


def marker_presence(text: str) -> dict[str, int]:
    return {
        family: int(any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns))
        for family, patterns in REPORTING_MARKERS.items()
    }


def deterministic_audit_sample(rows: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
    if size <= 0:
        return []
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        strata = row.get("strata") or ["unclassified"]
        primary = str(strata[0])
        buckets[primary].append(row)
    for bucket in buckets.values():
        bucket.sort(key=lambda item: hashlib.sha256(str(item["paper_id"]).encode("utf-8")).hexdigest())

    sample: list[dict[str, Any]] = []
    names = sorted(buckets)
    cursor = 0
    while len(sample) < min(size, len(rows)) and names:
        name = names[cursor % len(names)]
        bucket = buckets[name]
        if bucket:
            sample.append(bucket.pop(0))
        if not bucket:
            names.remove(name)
            cursor = 0
        else:
            cursor += 1
    return sample


def analyze(paths: Iterable[Path], corpus_root: Path, audit_sample_size: int) -> dict[str, Any]:
    paper_count = 0
    total_words = 0
    title_words: list[float] = []
    abstract_words: list[float] = []
    section_words: dict[str, list[float]] = defaultdict(list)
    sentence_lengths: dict[str, list[float]] = defaultdict(list)
    section_presence: Counter[str] = Counter()
    heading_counts: Counter[str] = Counter()
    order_counts: Counter[str] = Counter()
    move_counts: dict[str, Counter[str]] = defaultdict(Counter)
    move_denominators: Counter[str] = Counter()
    move_sequences: dict[str, Counter[str]] = defaultdict(Counter)
    reporting_papers: Counter[str] = Counter()
    strata: Counter[str] = Counter()
    journals: Counter[str] = Counter()
    years: Counter[str] = Counter()
    licenses: Counter[str] = Counter()
    sample_candidates: list[dict[str, Any]] = []

    for path in paths:
        record = load_record(path)
        paper_count += 1
        title = str(record.get("title") or "")
        title_words.append(float(len(WORD_RE.findall(title))))
        journal = str(record.get("journal") or "Unknown")
        journals[journal] += 1
        years[str(record.get("year") or "Unknown")] += 1
        license_label = str(record.get("license_url") or record.get("license") or "Unspecified")
        licenses[license_label] += 1
        record_strata = [str(value) for value in (record.get("discovery_strata") or ["unclassified"])]
        strata.update(record_strata)

        grouped, order, headings = canonical_sections(record)
        heading_counts.update(headings)
        if order:
            order_counts[">".join(order)] += 1
        abstract = grouped.get("abstract") or str(record.get("abstract") or "")
        abstract_words.append(float(len(WORD_RE.findall(abstract))))

        whole_text_parts: list[str] = []
        for section_name, text in grouped.items():
            words = WORD_RE.findall(text)
            word_count = len(words)
            total_words += word_count
            whole_text_parts.append(text)
            section_presence[section_name] += 1
            section_words[section_name].append(float(word_count))
            section_sentences = sentences(text)
            lengths = [len(WORD_RE.findall(sentence)) for sentence in section_sentences]
            if lengths:
                sentence_lengths[section_name].append(float(statistics.mean(lengths)))
            markers = count_markers(text)
            move_counts[section_name].update(markers)
            move_denominators[section_name] += word_count
            sequence = ">".join(move_sequence(text))
            if sequence:
                move_sequences[section_name][sequence] += 1

        for family, present in marker_presence(" ".join(whole_text_parts)).items():
            reporting_papers[family] += present
        sample_candidates.append(
            {
                "paper_id": str(record.get("paper_id") or path.stem.removesuffix(".json")),
                "pmcid": str(record.get("pmcid") or ""),
                "year": record.get("year"),
                "journal": journal,
                "strata": record_strata,
            }
        )

    section_summary: dict[str, Any] = {}
    for name in sorted(section_presence, key=lambda value: (SECTION_ORDER.index(value) if value in SECTION_ORDER else 99, value)):
        denominator = max(move_denominators[name], 1)
        section_summary[name] = {
            "paper_presence": section_presence[name],
            "presence_ratio": round(section_presence[name] / max(paper_count, 1), 4),
            "words": describe(section_words[name]),
            "mean_sentence_words_per_paper": describe(sentence_lengths[name]),
            "rhetorical_moves": dict(move_counts[name].most_common()),
            "moves_per_1000_words": {
                move: round(count * 1000 / denominator, 3) for move, count in move_counts[name].most_common()
            },
            "common_move_sequences": [
                {"sequence": sequence, "papers": count}
                for sequence, count in move_sequences[name].most_common(10)
            ],
        }

    audit_sample = deterministic_audit_sample(sample_candidates, audit_sample_size)
    return {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "corpus_root": str(corpus_root),
        "method": {
            "unit": "parsed JATS full text",
            "aggregation": "deterministic corpus statistics plus a stratified ID-only audit sample",
            "source_text_embedded": False,
        },
        "summary": {
            "papers": paper_count,
            "section_words_total": total_words,
            "title_words": describe(title_words),
            "abstract_words": describe(abstract_words),
            "journals": dict(journals.most_common()),
            "years": dict(sorted(years.items(), reverse=True)),
            "discovery_strata": dict(strata.most_common()),
            "licenses": dict(licenses.most_common()),
            "section_orders": [
                {"order": order, "papers": count} for order, count in order_counts.most_common(20)
            ],
            "sections": section_summary,
            "reporting_marker_prevalence": {
                family: {
                    "papers": reporting_papers[family],
                    "ratio": round(reporting_papers[family] / max(paper_count, 1), 4),
                }
                for family in REPORTING_MARKERS
            },
            "top_headings": [
                {"heading": heading, "occurrences": count} for heading, count in heading_counts.most_common(100)
            ],
        },
        "qualitative_audit_sample": {
            "requested_size": audit_sample_size,
            "actual_size": len(audit_sample),
            "selection": "round-robin across primary discovery strata, hash-stable within stratum",
            "papers": audit_sample,
        },
    }


def markdown_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Large-corpus writing-pattern report",
        "",
        f"Generated: {report['generated_at']}",
        f"Parsed full texts analyzed: {summary['papers']:,}",
        f"Section words analyzed: {summary['section_words_total']:,}",
        "",
        "This report contains aggregate metrics and stable identifiers only; no source paragraphs are embedded.",
        "",
        "## Section profile",
        "",
        "| Section | Papers | Presence | Median words | IQR words | Mean sentence words |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for section, values in summary["sections"].items():
        words = values["words"]
        sentence = values["mean_sentence_words_per_paper"]
        lines.append(
            f"| {section} | {values['paper_presence']:,} | {values['presence_ratio']:.1%} | "
            f"{words['median']:.1f} | {words['p25']:.1f}–{words['p75']:.1f} | {sentence['mean']:.1f} |"
        )

    lines.extend(
        [
            "",
            "## Reporting-marker prevalence",
            "",
            "| Marker family | Papers | Prevalence |",
            "|---|---:|---:|",
        ]
    )
    for family, values in summary["reporting_marker_prevalence"].items():
        lines.append(f"| {family} | {values['papers']:,} | {values['ratio']:.1%} |")

    lines.extend(["", "## Discovery strata", ""])
    for name, count in summary["discovery_strata"].items():
        lines.append(f"- {name}: {count:,}")

    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            "- Corpus frequencies calibrate structure, reporting completeness, and terminology; they do not establish scientific truth.",
            "- Claim support still requires task-specific retrieval and verification against the cited full text.",
            "- The ID-only stratified audit sample is intended for human/LLM deep reading and qualitative calibration.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    paths = record_paths(root, args.limit)
    if not paths:
        print(f"ERROR: No parsed records found under {root / 'records'}", file=sys.stderr)
        return 1
    try:
        report = analyze(paths, root, max(args.audit_sample_size, 0))
        out_json = (args.out_json or root / "reports" / "writing-patterns.json").resolve()
        out_md = (args.out_md or root / "reports" / "writing-patterns.md").resolve()
        write_json(out_json, report)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(markdown_report(report), encoding="utf-8", newline="\n")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "papers": report["summary"]["papers"],
                "section_words": report["summary"]["section_words_total"],
                "out_json": str(out_json),
                "out_md": str(out_md),
                "audit_sample": report["qualitative_audit_sample"]["actual_size"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
