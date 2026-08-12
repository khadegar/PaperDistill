from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdfbench.selection import select_stratified  # noqa: E402


class SelectionTests(unittest.TestCase):
    def test_selection_is_unique_and_balanced(self) -> None:
        strata = ["layout_stress", "formula_fe_topology"]
        candidates = []
        for index in range(12):
            candidates.append(
                {
                    "doi": f"10.1/{index}",
                    "pdf_sha256": f"hash-{index}",
                    "scores": {"layout_stress": float(index), "formula_fe_topology": float(12 - index)},
                    "qualifies": {"layout_stress": index > 6, "formula_fe_topology": index < 6},
                }
            )
        selected, reserve = select_stratified(candidates, strata, per_stratum=4, reserve_per_stratum=1)
        self.assertEqual(len(selected), 8)
        self.assertEqual(len(reserve), 2)
        all_dois = [row["doi"] for row in [*selected, *reserve]]
        self.assertEqual(len(all_dois), len(set(all_dois)))
        self.assertEqual(sum(row["benchmark_stratum"] == strata[0] for row in selected), 4)
        self.assertEqual(sum(row["benchmark_stratum"] == strata[1] for row in selected), 4)
        for stratum in strata:
            observed = [
                (row["doi"], row["pdf_sha256"])
                for row in selected
                if row["benchmark_stratum"] == stratum
            ]
            self.assertEqual(observed, sorted(observed))


if __name__ == "__main__":
    unittest.main()
