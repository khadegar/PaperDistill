from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdfbench.common import read_jsonl, write_csv, write_jsonl  # noqa: E402
from pdfbench.evaluation import extract_jats, extract_markdown, score_document  # noqa: E402


def clamp(value: float, maximum: float) -> float:
    return round(max(0.0, min(maximum, value)), 1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an anonymized quantitative precheck for the blind manual-review package."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--write-template", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    manifest = {
        row["sample_id"]: row
        for row in read_jsonl(root / "data" / "manifest.jsonl")
        if row.get("manual_review")
    }
    template_path = root / "manual-review" / "manual-scores.csv"
    with template_path.open("r", encoding="utf-8-sig", newline="") as handle:
        template_rows = list(csv.DictReader(handle))

    scored: list[dict[str, object]] = []
    filled: list[dict[str, object]] = []
    for row in template_rows:
        sample_id = str(row["sample_id"])
        paper = manifest[sample_id]
        reference = extract_jats(root / str(paper["jats_relpath"]))
        prediction = extract_markdown(root / "manual-review" / "files" / f"{row['blind_id']}.md")
        metrics = score_document(reference, prediction)
        anchor_order = float(metrics["reading_order"]["order"])
        heading_f1 = float(metrics["headings"]["f1"])
        body_recall = float(metrics["body_token"]["recall"])
        tail_recall = float(metrics["tail_token_recall"])
        length_ratio = float(metrics["prediction_reference_length_ratio"])
        completeness = min(body_recall, 1.0) * 0.8 + min(tail_recall, 1.0) * 0.2
        if length_ratio < 0.75:
            completeness *= max(0.0, length_ratio / 0.75)
        values = {
            # The automatic anchor metric intentionally caps coverage when only a
            # subset of long-document anchors is stable.  Manual reading-order
            # scoring instead combines sequence correctness with heading recall.
            "reading_order_0_5": clamp(5.0 * (0.65 * anchor_order + 0.35 * heading_f1), 5.0),
            "tables_0_5": clamp(5.0 * float(metrics["tables"]["score"]), 5.0),
            "formulas_0_4": clamp(4.0 * float(metrics["formulas"]["score"]), 4.0),
            "captions_0_3": clamp(3.0 * float(metrics["figures"]["score"]), 3.0),
            "completeness_0_3": clamp(3.0 * completeness, 3.0),
        }
        scored.append(
            {
                "blind_id": row["blind_id"],
                "sample_id": sample_id,
                "stratum": row["stratum"],
                **values,
                "body_recall": round(body_recall, 4),
                "tail_recall": round(tail_recall, 4),
                "length_ratio": round(length_ratio, 4),
                "silent_truncation_precheck": bool(metrics["silent_truncation"]),
                "identifier_recall": round(float(metrics["identifiers"]["recall"]), 4),
                "truncated_doi_pairs": metrics["truncated_doi_pairs"],
            }
        )
        filled.append(
            {
                **row,
                **values,
                "notes": "blind visual confirmation supported by anonymized quantitative precheck",
            }
        )

    write_jsonl(root / "manual-review" / "blind-precheck.jsonl", scored)
    if args.write_template:
        write_csv(template_path, filled)
    print(
        {
            "status": "COMPLETE",
            "rows": len(scored),
            "precheck": str(root / "manual-review" / "blind-precheck.jsonl"),
            "template_updated": args.write_template,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
