#!/usr/bin/env python3
"""Build a flat evidence matrix from per-paper JSON records."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from _common import read_json


FIELDS = (
    "paper_id",
    "title",
    "year",
    "source_title",
    "doi",
    "citekey",
    "access_level",
    "study_type",
    "domains",
    "primary_outcomes",
    "credibility",
    "relevance",
    "summary_zh",
    "reported_limitations",
    "analyst_limitations",
    "source_hash",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--out", type=Path, help="Defaults to <project>/evidence/matrix.csv")
    return parser.parse_args()


def compact(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def to_row(record: dict[str, Any]) -> dict[str, str]:
    bibliography = record.get("bibliography") or {}
    provenance = record.get("provenance") or {}
    study = record.get("study") or {}
    quality = record.get("quality") or {}
    limitations = record.get("limitations") or {}
    outcomes = record.get("outcomes") or []
    primary = [outcome for outcome in outcomes if not isinstance(outcome, dict) or outcome.get("primary", True)]
    return {
        "paper_id": compact(record.get("paper_id")),
        "title": compact(bibliography.get("title")),
        "year": compact(bibliography.get("year")),
        "source_title": compact(bibliography.get("source_title")),
        "doi": compact(bibliography.get("doi")),
        "citekey": compact(bibliography.get("citekey")),
        "access_level": compact(provenance.get("access_level")),
        "study_type": compact(study.get("study_type")),
        "domains": compact(record.get("domains") or {}),
        "primary_outcomes": compact(primary),
        "credibility": compact(quality.get("credibility")),
        "relevance": compact(quality.get("relevance")),
        "summary_zh": compact(record.get("summary_zh")),
        "reported_limitations": compact(limitations.get("reported") or []),
        "analyst_limitations": compact(limitations.get("analyst_identified") or []),
        "source_hash": compact(provenance.get("source_hash")),
    }


def main() -> int:
    args = parse_args()
    project = args.project.resolve()
    records_dir = project / "corpus/records"
    output = (args.out or project / "evidence/matrix.csv").resolve()
    if not (project / "project.json").is_file():
        print(f"ERROR: project.json not found under {project}", file=sys.stderr)
        return 1
    if not records_dir.is_dir():
        print(f"ERROR: records directory not found: {records_dir}", file=sys.stderr)
        return 1

    rows = []
    errors = []
    for path in sorted(records_dir.glob("*.json")):
        try:
            rows.append(to_row(read_json(path)))
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
    if errors:
        print("ERROR: invalid record files:\n- " + "\n- ".join(errors), file=sys.stderr)
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(output)
    print(json.dumps({"project": str(project), "output": str(output), "records": len(rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
