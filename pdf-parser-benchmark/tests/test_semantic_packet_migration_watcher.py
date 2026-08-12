from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import sys
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "watch_semantic_packet_migration.py"
SPEC = importlib.util.spec_from_file_location("semantic_packet_migration_watcher", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
watcher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = watcher
SPEC.loader.exec_module(watcher)


class SemanticPacketMigrationWatcherTests(unittest.TestCase):
    def test_candidate_pmcids_skip_unchanged_stable_blockers(self) -> None:
        selection = [
            {"pmcid": "PMC1"},
            {"pmcid": "PMC2"},
            {"pmcid": "PMC3", "source_format": "mineru_markdown"},
        ]
        index = {
            "PMC1": {"preferred_markdown_sha256": "a" * 64},
            "PMC2": {"preferred_markdown_sha256": "b" * 64},
            "PMC3": {"preferred_markdown_sha256": "c" * 64},
        }
        pdf = {pmcid: {"pdf_sha256": "d" * 64} for pmcid in index}
        blocked = {
            "PMC1": {
                "fingerprint": "stable-1",
                "reasons": ["references_boundary_unknown"],
            }
        }
        fingerprints = {"PMC1": "stable-1", "PMC2": "new-2", "PMC3": "new-3"}

        self.assertEqual(
            watcher.candidate_pmcids(selection, index, pdf, blocked, fingerprints),
            ["PMC2"],
        )

    def test_dynamic_blocker_is_retried_without_hash_change(self) -> None:
        selection = [{"pmcid": "PMC1"}]
        index = {"PMC1": {"preferred_markdown_sha256": "a" * 64}}
        pdf = {"PMC1": {"pdf_sha256": "b" * 64}}
        blocked = {
            "PMC1": {
                "fingerprint": "same",
                "reasons": ["active_lease"],
            }
        }
        self.assertEqual(
            watcher.candidate_pmcids(
                selection,
                index,
                pdf,
                blocked,
                {"PMC1": "same"},
            ),
            ["PMC1"],
        )

    def test_completed_or_migrated_rows_without_fingerprints_are_not_candidates(self) -> None:
        selection = [
            {"pmcid": "PMC1"},
            {"pmcid": "PMC2", "source_format": "mineru_markdown"},
        ]
        index = {
            "PMC1": {"preferred_markdown_sha256": "a" * 64},
            "PMC2": {"preferred_markdown_sha256": "b" * 64},
        }
        pdf = {pmcid: {"pdf_sha256": "c" * 64} for pmcid in index}
        self.assertEqual(watcher.candidate_pmcids(selection, index, pdf, {}, {}), [])

    def test_all_canonical_completed_statuses_are_frozen(self) -> None:
        temp_root = ROOT / "tmp" / f"watcher-status-{uuid.uuid4().hex}"
        temp_root.mkdir(parents=True, exist_ok=True)
        try:
            for status in ("completed", "complete", "done", "read"):
                path = temp_root / f"{status}.json"
                path.write_text(json.dumps({"reading": {"status": status}}), encoding="utf-8")
                self.assertTrue(watcher.is_completed_card(path), status)
        finally:
            for path in temp_root.glob("*"):
                path.unlink(missing_ok=True)
            temp_root.rmdir()

    def test_fingerprint_changes_when_markdown_or_overlay_changes(self) -> None:
        temp_root = ROOT / "tmp" / f"watcher-{uuid.uuid4().hex}"
        semantic = temp_root / "corpus" / "semantic-distillation"
        export = temp_root / "export"
        pdf_root = temp_root / "pdf-root"
        migrator = temp_root / "migrator.py"
        card = semantic / "cards" / "PMC1.json"
        markdown = export / "preferred" / "PMC1.md"
        pdf = pdf_root / "pdfs" / "PMC1.pdf"
        overlay = semantic / "overlays" / "PMC1.json"
        for path in (card, markdown, pdf, migrator):
            path.parent.mkdir(parents=True, exist_ok=True)
        card.write_text(json.dumps({"reading": {"status": "pending"}}), encoding="utf-8")
        markdown.write_text("first", encoding="utf-8")
        pdf.write_bytes(b"pdf")
        migrator.write_text("# migrator", encoding="utf-8")
        paths = watcher.Paths(
            project_root=temp_root,
            corpus_root=temp_root / "corpus",
            semantic_root=semantic,
            mineru_export=export,
            pdf_manifest=temp_root / "manifest.jsonl",
            pdf_root=pdf_root,
            migrator=migrator,
            skill_scripts=temp_root,
            state_path=temp_root / "state.json",
            log_path=temp_root / "log.jsonl",
            lock_path=temp_root / "lock",
        )
        row = {"source_record_sha256": "old", "packet_sha256": "packet"}
        idx = {
            "preferred_markdown_relpath": "preferred/PMC1.md",
            "preferred_markdown_sha256": watcher.sha256_file(markdown),
            "input_sha256": watcher.sha256_file(pdf),
            "input_kind": "publisher_pdf",
        }
        pdf_row = {"pdf_sha256": watcher.sha256_file(pdf), "input_kind": "publisher_pdf"}
        try:
            first = watcher.material_fingerprint(
                "PMC1", row, idx, pdf_row, paths, watcher.sha256_file(migrator), set()
            )
            overlay.parent.mkdir(parents=True, exist_ok=True)
            overlay.write_text("{}", encoding="utf-8")
            second = watcher.material_fingerprint(
                "PMC1", row, idx, pdf_row, paths, watcher.sha256_file(migrator), set()
            )
            markdown.write_text("second", encoding="utf-8")
            idx["preferred_markdown_sha256"] = watcher.sha256_file(markdown)
            third = watcher.material_fingerprint(
                "PMC1", row, idx, pdf_row, paths, watcher.sha256_file(migrator), set()
            )
            self.assertNotEqual(first, second)
            self.assertNotEqual(second, third)
        finally:
            for path in sorted(temp_root.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    try:
                        path.rmdir()
                    except OSError:
                        pass
            try:
                temp_root.rmdir()
            except OSError:
                pass

    def test_batch_size_above_safety_cap_is_rejected(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                watcher.parse_args(["--batch-size", "101", "--once"])


if __name__ == "__main__":
    unittest.main()
