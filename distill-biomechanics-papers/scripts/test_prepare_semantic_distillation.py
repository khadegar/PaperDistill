#!/usr/bin/env python3
"""Small regression test for the resumable full-manifest queue.

The fixture exercises only queue mechanics.  It does not create semantic card
content and does not touch the production corpus.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = SCRIPT_DIR.parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import prepare_semantic_distillation as prepare  # noqa: E402


class ManifestQueueTest(unittest.TestCase):
    def _fixture(self, root: Path) -> None:
        (root / "records").mkdir(parents=True)
        manifest_rows = []
        for index in range(3):
            pmcid = f"PMC{index + 1:04d}"
            record = {
                "schema_version": "1.0",
                "paper_id": f"doi:10.1000/example-{index + 1}",
                "pmcid": pmcid,
                "title": f"Fixture paper {index + 1}",
                "authors": "Fixture A",
                "journal": "Fixture Journal",
                "year": 2024,
                "doi": f"10.1000/example-{index + 1}",
                "publication_type": "research-article; Journal Article",
                "discovery_strata": ["biomechanics_fe_bone"],
                "abstract": "Fixture abstract.",
                "sections": [
                    {
                        "heading": "Introduction",
                        "section_type": "introduction",
                        "text": "One two three four five six seven eight nine ten.",
                    },
                    {
                        "heading": "References",
                        "section_type": "references",
                        "text": "Excluded reference text.",
                    },
                ],
            }
            with gzip.open(root / "records" / f"{pmcid}.json.gz", "wt", encoding="utf-8") as stream:
                json.dump(record, stream)
            manifest_rows.append(
                {
                    "paper_id": record["paper_id"],
                    "title": record["title"],
                    "year": 2024,
                    "doi": record["doi"],
                    "pmcid": pmcid,
                    "authors": record["authors"],
                    "publication_type": record["publication_type"],
                    "discovery_strata": record["discovery_strata"],
                    "selection_rank": index + 1,
                    "selected": True,
                }
            )
        (root / "manifest.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in manifest_rows), encoding="utf-8"
        )

    @staticmethod
    def _args(root: Path, *extra: str) -> object:
        return prepare.parse_args(
            [
                "--root",
                str(root),
                "--all-manifest",
                "--chunk-words",
                "3",
                "--card-stubs",
                *extra,
            ]
        )

    def test_batch_rerun_is_idempotent_and_cards_are_preserved(self) -> None:
        # The managed workspace sandbox may deny Python-created subdirectories
        # even though the production corpus is writable.  Keep the regression
        # test runnable in normal checkouts and report that environment-only
        # limitation as a skip rather than a false implementation failure.
        try:
            temporary = tempfile.mkdtemp(dir=WORKSPACE_DIR)
        except PermissionError as exc:  # pragma: no cover - sandbox-specific
            self.skipTest(f"temporary fixture directory is not writable: {exc}")
        root = Path(temporary)
        try:
            try:
                self._fixture(root)
            except PermissionError as exc:  # pragma: no cover - sandbox-specific
                self.skipTest(f"fixture subdirectory is not writable: {exc}")

            first = prepare.prepare(self._args(root, "--limit", "1"))
            self.assertEqual(first["counts"]["new_selected"], 1)
            selection_path = root / "semantic-distillation" / "selection.jsonl"
            card_path = root / "semantic-distillation" / "cards" / "PMC0001.json"
            before_selection = selection_path.read_bytes()
            before_card = card_path.read_bytes()

            # Mark the fixture card as completed; queue preparation must never
            # replace it, even when card stubs are requested again.
            card = json.loads(before_card)
            card["reading"]["status"] = "completed"
            card_path.write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
            completed_card = card_path.read_bytes()

            rerun = prepare.prepare(self._args(root, "--limit", "1"))
            self.assertEqual(rerun["counts"]["new_selected"], 0)
            self.assertEqual(selection_path.read_bytes(), before_selection)
            self.assertEqual(card_path.read_bytes(), completed_card)

            second = prepare.prepare(self._args(root, "--offset", "1", "--limit", "1"))
            self.assertEqual(second["counts"]["new_selected"], 1)
            self.assertEqual(len(selection_path.read_text(encoding="utf-8").splitlines()), 2)

            second_rerun = prepare.prepare(self._args(root, "--offset", "1", "--limit", "1"))
            self.assertEqual(second_rerun["counts"]["new_selected"], 0)
            self.assertEqual(card_path.read_bytes(), completed_card)

            dry = prepare.prepare(self._args(root, "--offset", "2", "--limit", "1", "--dry-run"))
            self.assertEqual(dry["counts"]["new_selected"], 1)
            self.assertEqual(len(selection_path.read_text(encoding="utf-8").splitlines()), 2)

            packet = root / "semantic-distillation" / "packets" / "PMC0001.md"
            self.assertTrue(packet.exists())
            self.assertIn("S001:C01", packet.read_text(encoding="utf-8"))
            self.assertEqual(
                hashlib.sha256(card_path.read_bytes()).hexdigest(),
                hashlib.sha256(completed_card).hexdigest(),
            )
        finally:
            # ``TemporaryDirectory`` cannot always clean up in a restricted
            # Windows sandbox; best-effort cleanup is sufficient for a test
            # fixture and avoids touching any production path.
            import shutil

            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
