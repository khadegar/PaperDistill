#!/usr/bin/env python3
"""Audit a biomechanics manuscript for recurrent structural and prose problems."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


FLAGGED_TERMS = {
    "delve": "use the specific research action",
    "pivotal": "state the measured importance",
    "crucial": "state why it is necessary",
    "showcase": "use show, demonstrate, or present",
    "leverage": "use employ or use",
    "multifaceted": "name the relevant dimensions",
    "nuanced": "state the exact qualification",
    "comprehensive": "define the covered scope",
    "robust": "name reliability, sensitivity, or strength evidence",
    "cutting-edge": "state the concrete technical advance",
    "groundbreaking": "state the novelty delta",
}

THROAT_OPENERS = (
    r"\bin (?:the )?realm of\b",
    r"\bit is (?:important|worth mentioning|noteworthy) (?:to note )?that\b",
    r"\bit should be noted that\b",
    r"\bin today'?s rapidly evolving\b",
    r"\bthis section (?:will|aims to)\b",
    r"\bwe now turn (?:our attention )?to\b",
)

WORD_RE = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)?")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manuscript", type=Path)
    parser.add_argument("--mode", choices=("original", "review", "section"), default="original")
    parser.add_argument("--submission-ready", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def latex_to_plain(text: str) -> str:
    text = re.sub(r"(?m)^\s*%.*$", "", text)
    text = re.sub(r"\\cite\w*(?:\[[^\]]*\]){0,2}\{[^}]+\}", " [CITATION] ", text)
    text = re.sub(r"\\(?:section|subsection|subsubsection)\*?\{([^}]*)\}", r"\n\1\n", text)
    text = re.sub(r"\\(?:textbf|textit|emph)\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?", " ", text)
    text = text.replace("{", " ").replace("}", " ").replace("~", " ")
    return re.sub(r"\s+", " ", text).strip()


def extract_sections(raw: str) -> list[str]:
    latex = re.findall(r"\\section\*?\{([^}]+)\}", raw, flags=re.IGNORECASE)
    markdown = re.findall(r"(?m)^#{1,4}\s+(.+?)\s*$", raw)
    return [re.sub(r"\s+", " ", value).strip().casefold() for value in latex + markdown]


def extract_abstract(raw: str) -> str:
    match = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", raw, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return latex_to_plain(match.group(1))
    match = re.search(
        r"(?ims)^#{1,4}\s+abstract\s*$\s*(.*?)(?=^#{1,4}\s+|\Z)",
        raw,
    )
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def add_issue(issues: list[dict[str, Any]], code: str, message: str, count: int | None = None) -> None:
    value: dict[str, Any] = {"severity": "WARN", "code": code, "message": message}
    if count is not None:
        value["count"] = count
    issues.append(value)


def has_section(sections: list[str], patterns: tuple[str, ...]) -> bool:
    return any(any(pattern in section for pattern in patterns) for section in sections)


def audit(raw: str, mode: str, submission_ready: bool) -> dict[str, Any]:
    prose_raw = re.sub(r"(?im)^.*\\textbf\{keywords?:\}.*$", "", raw)
    prose_raw = re.sub(r"(?im)^\s*keywords?\s*:.*$", "", prose_raw)
    plain = latex_to_plain(prose_raw)
    words = WORD_RE.findall(plain)
    sentence_values = [value.strip() for value in SENTENCE_RE.split(plain) if len(WORD_RE.findall(value)) >= 4]
    lengths = [len(WORD_RE.findall(value)) for value in sentence_values]
    sections = extract_sections(raw)
    issues: list[dict[str, Any]] = []

    for term, guidance in FLAGGED_TERMS.items():
        count = len(re.findall(rf"\b{re.escape(term)}\b", plain, flags=re.IGNORECASE))
        if count:
            add_issue(issues, "VAGUE_TERM", f"Review '{term}': {guidance}", count)

    for pattern in THROAT_OPENERS:
        count = len(re.findall(pattern, plain, flags=re.IGNORECASE))
        if count:
            add_issue(issues, "THROAT_OPENER", "Start with the substantive claim instead of meta-commentary", count)

    em_dashes = plain.count("—")
    if em_dashes > 3:
        add_issue(issues, "EM_DASH", "Use commas, parentheses, or separate sentences where clearer", em_dashes)
    semicolons = plain.count(";")
    allowed_semicolons = max(2, round(len(words) / 500))
    if semicolons > allowed_semicolons:
        add_issue(issues, "SEMICOLON_DENSITY", f"Review semicolon density; target no more than about {allowed_semicolons}", semicolons)

    long_sentences = sum(length > 50 for length in lengths)
    if long_sentences:
        add_issue(issues, "LONG_SENTENCE", "Sentences above 50 words should be checked for multiple independent claims", long_sentences)

    burst_runs = 0
    for index in range(max(len(lengths) - 4, 0)):
        window = lengths[index : index + 5]
        if max(window) - min(window) <= 5:
            burst_runs += 1
    if burst_runs:
        add_issue(issues, "UNIFORM_RHYTHM", "Five consecutive sentences have a narrow length range; review the paragraph rhythm", burst_runs)

    significance_without_context = 0
    for sentence in sentence_values:
        if re.search(r"\bsignificant(?:ly)?\b", sentence, flags=re.IGNORECASE) and not re.search(
            r"\bp\s*[<=>]|confidence interval|\bCI\b|effect size|statistical",
            sentence,
            flags=re.IGNORECASE,
        ):
            significance_without_context += 1
    if significance_without_context:
        add_issue(
            issues,
            "SIGNIFICANCE_CONTEXT",
            "Clarify whether 'significant' is statistical or replace it with the measured magnitude",
            significance_without_context,
        )

    abstract = extract_abstract(raw)
    abstract_words = len(WORD_RE.findall(abstract))
    if mode != "section" and not abstract:
        add_issue(issues, "MISSING_ABSTRACT", "Add an abstract with the complete evidence chain")
    elif abstract and not 120 <= abstract_words <= 350:
        add_issue(issues, "ABSTRACT_LENGTH", f"Abstract contains {abstract_words} words; confirm the target journal limit")

    if mode == "original":
        required = {
            "introduction": ("introduction",),
            "methods": ("method", "materials"),
            "results": ("result", "finding"),
            "discussion": ("discussion",),
            "conclusion": ("conclusion",),
        }
        for label, patterns in required.items():
            if not has_section(sections, patterns):
                add_issue(issues, "MISSING_SECTION", f"Expected original-study section: {label}")
    elif mode == "review":
        if not has_section(sections, ("method", "search", "review protocol")):
            add_issue(issues, "MISSING_REVIEW_METHOD", "Report search sources, dates, queries, screening, and inclusion criteria")

    if submission_ready:
        declarations = {
            "data availability": ("data availability",),
            "author contributions": ("author contribution", "credit"),
            "funding": ("funding",),
            "conflict of interest": ("conflict", "competing interest"),
            "ethics": ("ethics",),
        }
        for label, patterns in declarations.items():
            if not has_section(sections, patterns):
                add_issue(issues, "MISSING_DECLARATION", f"Submission-ready draft is missing: {label}")

    return {
        "words": len(words),
        "sentences": len(sentence_values),
        "abstract_words": abstract_words,
        "sections": sections,
        "warnings": len(issues),
        "issues": issues,
    }


def main() -> int:
    args = parse_args()
    path = args.manuscript.resolve()
    if not path.is_file():
        print(f"ERROR: Manuscript not found: {path}", file=sys.stderr)
        return 1
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    if not raw.strip():
        print(f"ERROR: Manuscript is empty: {path}", file=sys.stderr)
        return 1
    report = {"manuscript": str(path), **audit(raw, args.mode, args.submission_ready)}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"PROSE_AUDIT: {path}")
        print(json.dumps({key: report[key] for key in ("words", "sentences", "abstract_words", "warnings")}, ensure_ascii=False))
        for issue in report["issues"]:
            suffix = f" (count={issue['count']})" if "count" in issue else ""
            print(f"- {issue['severity']} {issue['code']}: {issue['message']}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
