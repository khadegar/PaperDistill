from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdfbench.evaluation import (  # noqa: E402
    extract_identifier_groups,
    heading_metrics,
    score_document,
    summarize_repeatability_rows,
)


def reference() -> dict:
    return {
        "body_text": (
            "Introduction Porous titanium reduced stiffness by 25% at 800 N. "
            "Methods Ten specimens were tested at 5 mm/min. "
            "Results The elastic modulus was 3.2 GPa and fatigue survival reached 10^6 cycles. "
            "Discussion These measurements support a bounded mechanical interpretation."
        ),
        "headings": ["Introduction", "Methods", "Results", "Discussion"],
        "tables": ["Group Modulus GPa Dense 8.0 Porous 3.2"],
        "formulas": ["E = sigma / epsilon"],
        "captions": ["Figure 1. As-built porous titanium architecture"],
    }


def prediction(**overrides: object) -> dict:
    base = {
        "body_text": reference()["body_text"],
        "headings": list(reference()["headings"]),
        "tables": list(reference()["tables"]),
        "formulas": list(reference()["formulas"]),
        "captions": list(reference()["captions"]),
    }
    base.update(overrides)
    return base


class EvaluationTests(unittest.TestCase):
    def test_exact_content_scores_near_maximum(self) -> None:
        result = score_document(reference(), prediction())
        self.assertGreater(result["automatic_score"], 78)
        self.assertFalse(result["silent_truncation"])
        self.assertFalse(result["identifier_integrity_risk"])

    def test_swapped_headings_reduce_order(self) -> None:
        metrics = heading_metrics(reference()["headings"], ["Results", "Methods", "Introduction", "Discussion"])
        self.assertLess(metrics["order"], 1.0)

    def test_missing_table_reduces_table_score(self) -> None:
        result = score_document(reference(), prediction(tables=[]))
        self.assertEqual(result["tables"]["score"], 0.0)
        self.assertEqual(result["component_points"]["tables"], 0.0)

    def test_broken_formula_reduces_formula_score(self) -> None:
        result = score_document(reference(), prediction(formulas=["x + y = 100"]))
        self.assertLess(result["formulas"]["score"], 0.75)

    def test_numeric_unit_change_is_detected(self) -> None:
        altered = reference()["body_text"].replace("25% at 800 N", "52% at 80 N").replace("3.2 GPa", "32 GPa")
        result = score_document(reference(), prediction(body_text=altered))
        self.assertLess(result["identifiers"]["recall"], 1.0)
        self.assertTrue(result["identifier_integrity_risk"])

    def test_unicode_scientific_units_are_normalized(self) -> None:
        groups = extract_identifier_groups("Pore size was 12 ± 0.2 μm at 37 °C under 5 N/mm.")
        self.assertIn("12±0.2µm", groups["number_unit"])
        self.assertIn("37°c", groups["number_unit"])
        self.assertIn("5n/mm", groups["number_unit"])

    def test_truncated_doi_is_an_integrity_risk(self) -> None:
        source = reference()
        source["body_text"] += " Reference DOI 10.1016/j.clinbiomech.2008.12.007."
        altered = source["body_text"].replace("10.1016/j.clinbiomech.2008.12.007", "10.1016/j")
        result = score_document(source, prediction(body_text=altered))
        self.assertTrue(result["truncated_doi_pairs"])
        self.assertTrue(result["identifier_integrity_risk"])

    def test_truncated_document_is_flagged(self) -> None:
        result = score_document(reference(), prediction(body_text="Introduction Porous titanium reduced stiffness."))
        self.assertTrue(result["silent_truncation"])

    def test_repeatability_summary_detects_score_drift(self) -> None:
        rows = [
            {
                "tool": "parser",
                "both_successful": True,
                "exact_markdown_match": index < 4,
                "normalized_token_f1": 1.0 if index < 4 else 0.95,
                "automatic_score_delta": 0.0 if index < 4 else 1.25,
            }
            for index in range(5)
        ]
        summary = summarize_repeatability_rows(rows)[0]
        self.assertEqual(summary["exact_markdown_matches"], 4)
        self.assertEqual(summary["maximum_automatic_score_delta"], 1.25)
        self.assertFalse(summary["stable"])


if __name__ == "__main__":
    unittest.main()
