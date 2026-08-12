#!/usr/bin/env python3
"""Regression tests for conservative MinerU-to-Luna packet preparation.

The in-memory fixture contains 10,000 lightweight identity rows because the
production migrator intentionally rejects partial manifests.  No production
corpus path is read or modified.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import prepare_mineru_semantic_packets as prepare  # noqa: E402
import manage_semantic_reading as manager  # noqa: E402


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _paragraph(label: str, count: int = 140) -> str:
    """Return a long, non-repeated paragraph with deterministic tokens."""

    return f"{label} " + " ".join(f"{label.casefold()}_{index:03d}" for index in range(count)) + "."


def _valid_markdown(*, middle: str = "", result_heading: str = "## Results") -> str:
    return (
        "# Fixture title\n\n"
        "## Introduction\n\n"
        f"{_paragraph('Introduction')}\n\n"
        f"{middle}\n\n"
        "## Methods\n\n"
        f"{_paragraph('Methods')}\n\n"
        f"{result_heading}\n\n"
        f"{_paragraph('Results')}\n\n"
        "## ■ REFERENCES\n\n"
        "1. Excluded reference alpha.\n"
        "2. Excluded reference beta.\n\n"
        "## Funding\n\n"
        f"{_paragraph('Funding', 80)}\n"
    )


class MarkdownParserTest(unittest.TestCase):
    def test_html_table_and_display_math_remain_atomic_chunks(self) -> None:
        source = r"""
<table>
<tr><th>region</th><th>stress</th></tr>
<tr><td>cortical bone</td><td>12.4 MPa</td></tr>
</table>

$$
\sigma_{eq} = E \epsilon + \alpha + \beta + \gamma
$$
""".strip()
        blocks, errors = prepare.lex_markdown(source)
        self.assertEqual(errors, [])
        self.assertEqual([block.kind for block in blocks], ["html_table", "display_math"])

        chunks = prepare.chunk_section(prepare.Section("Results", 2, blocks), token_limit=2)
        self.assertEqual(len(chunks), 2)
        self.assertTrue(all(len(chunk) == 1 for chunk in chunks))
        self.assertIs(chunks[0][0], blocks[0])
        self.assertIs(chunks[1][0], blocks[1])
        self.assertIn("</table>", chunks[0][0].text)
        self.assertTrue(chunks[1][0].text.startswith("$$\n"))
        self.assertTrue(chunks[1][0].text.endswith("\n$$"))
        self.assertTrue(all("oversize_atomic_block" in block.flags for block in blocks))

    def test_heatmap_details_is_image_ocr_not_a_table_and_marks_visual_unavailable(self) -> None:
        details = """<details>
<summary>heatmap</summary>
| apparent region | apparent value |
| --- | --- |
| red pixels | 12.4 MPa |
</details>"""
        source = _valid_markdown(middle=details)
        blocks, errors = prepare.lex_markdown(source)
        self.assertEqual(errors, [])
        heatmap_blocks = [block for block in blocks if "heatmap" in block.text.casefold()]
        self.assertEqual(len(heatmap_blocks), 1)
        self.assertEqual(heatmap_blocks[0].kind, "image_ocr")
        self.assertNotIn(heatmap_blocks[0].kind, {"html_table", "markdown_table"})
        self.assertIn("visual_unavailable", heatmap_blocks[0].flags)

        parsed = prepare.parse_markdown(source, page_count=2)
        self.assertTrue(parsed.visual_unavailable)
        self.assertTrue(
            any(block.kind == "image_ocr" for section in parsed.sections for block in section.blocks)
        )

    def test_figure_heading_is_caption_content_not_a_section(self) -> None:
        caption = "## Fig. 10. Stress distribution around the implant"
        source = _valid_markdown(middle=f"{caption}\n\nPeak stress remained localized.")
        blocks, errors = prepare.lex_markdown(source)
        self.assertEqual(errors, [])
        figure = [block for block in blocks if "Fig. 10" in block.text]
        self.assertEqual(len(figure), 1)
        self.assertEqual(figure[0].kind, "caption")

        parsed = prepare.parse_markdown(source, page_count=2)
        self.assertFalse(any(section.heading.casefold().startswith("fig") for section in parsed.sections))
        body = "\n".join(block.text for section in parsed.sections for block in section.blocks)
        self.assertIn(caption, body)

    def test_reference_like_fe_heading_is_preserved_real_references_end_at_funding(self) -> None:
        false_heading = "## Reference finite element model"
        source = _valid_markdown(
            middle=f"{false_heading}\n\nThe reference finite element mesh remained in the body."
        )
        parsed = prepare.parse_markdown(source, page_count=2)

        self.assertTrue(parsed.references_detected)
        self.assertFalse(parsed.references_inferred)
        headings = [section.heading for section in parsed.sections]
        self.assertNotIn("Reference finite element model", headings)
        self.assertIn("Funding", headings)
        body = "\n".join(block.text for section in parsed.sections for block in section.blocks)
        self.assertIn(false_heading, body)
        self.assertIn("funding_079", body)
        self.assertNotIn("Excluded reference alpha", body)

    def test_replacement_character_in_heading_blocks_semantic_eligibility(self) -> None:
        source = _valid_markdown(result_heading="## Res\ufffdults")
        parsed = prepare.parse_markdown(source, page_count=2)
        self.assertIn("replacement_character_in_heading", parsed.blocking_reasons)
        self.assertIn("replacement_characters:1", parsed.quality_flags)
        self.assertFalse(parsed.semantic_eligible)

    def test_rendered_chunk_locators_are_unique_and_contiguous(self) -> None:
        sections = [
            prepare.Section(
                "Introduction",
                2,
                [prepare.Block("text", word, number, number) for number, word in enumerate(("alpha", "beta", "gamma"), 1)],
                "introduction",
            ),
            prepare.Section(
                "Methods",
                2,
                [prepare.Block("text", word, number + 3, number + 3) for number, word in enumerate(("delta", "epsilon"), 1)],
                "methods",
            ),
        ]
        parsed = prepare.ParseResult(sections, [], [], True, False, False, 5, 26)
        packet, section_rows = prepare.render_packet(
            {"pmcid": "PMC1", "paper_id": "fixture"},
            {"preferred_mode": "primary", "page_count": 1},
            "source.md",
            "a" * 64,
            "source.pdf",
            "b" * 64,
            "old.xml.gz",
            "c" * 64,
            parsed,
            token_limit=1,
        )
        locators = re.findall(r"^### (S\d{3}:C\d{2})$", packet.decode("utf-8"), re.MULTILINE)
        expected = [
            "S001:C01",
            "S001:C02",
            "S001:C03",
            "S002:C01",
            "S002:C02",
        ]
        self.assertEqual(locators, expected)
        self.assertEqual(len(locators), len(set(locators)))
        self.assertTrue(all(prepare.LOCATOR_RE.fullmatch(locator) for locator in locators))
        self.assertEqual([row["chunk_count"] for row in section_rows], [3, 2])


class MigrationGateTest(unittest.TestCase):
    ELIGIBLE = "PMC00000001"
    MARKDOWN_MISMATCH = "PMC00000002"
    PDF_MISMATCH = "PMC00000003"
    INPUT_MISMATCH = "PMC00000004"
    COMPLETED = "PMC00000005"

    def test_only_untouched_pending_skeleton_is_migratable(self) -> None:
        base = {
            "reading": {
                "status": "pending",
                "packet_sha256": "a" * 64,
                "section_locators_read": [],
                "omissions": [],
            },
            "study": {"article_kind": "original_research", "design_features": []},
            "status": "blank",
        }
        self.assertEqual(prepare.pending_skeleton_reasons(base), [])

        cases = {
            "completed": {**base, "reading": {**base["reading"], "status": "completed"}},
            "partial": {**base, "reading": {**base["reading"], "section_locators_read": ["S001:C01"]}},
            "provenance": {**base, "reading": {**base["reading"], "reader_role": "luna_primary"}},
            "semantic": {**base, "argument_map": {"central_claim": "already interpreted"}},
        }
        for name, card in cases.items():
            with self.subTest(name=name):
                self.assertTrue(prepare.pending_skeleton_reasons(card))

    def test_recovery_preserves_only_matching_completed_card_evolution(self) -> None:
        pending = {
            "source_record_sha256": "a" * 64,
            "reading": {"status": "pending", "packet_sha256": "b" * 64},
        }
        completed = {
            **pending,
            "reading": {
                "status": "completed",
                "packet_sha256": "b" * 64,
                "reader_role": "luna_primary",
            },
            "argument_map": {"central_claim": "semantic result"},
        }
        encode = lambda value: json.dumps(value).encode("utf-8")
        self.assertTrue(prepare.completed_card_evolution(encode(completed), encode(pending)))

        wrong_packet = {**completed, "reading": {**completed["reading"], "packet_sha256": "c" * 64}}
        wrong_source = {**completed, "source_record_sha256": "d" * 64}
        still_pending = {**completed, "reading": {**completed["reading"], "status": "pending"}}
        self.assertFalse(prepare.completed_card_evolution(encode(wrong_packet), encode(pending)))
        self.assertFalse(prepare.completed_card_evolution(encode(wrong_source), encode(pending)))
        self.assertFalse(prepare.completed_card_evolution(encode(still_pending), encode(pending)))

    def test_active_reading_material_unions_lease_overlay_draft_and_checkpoint(self) -> None:
        paths = [
            Path("fixture/overlays/PMC2.json"),
            Path("fixture/overlays/PMC3.json.draft"),
            Path("fixture/luna-state/PMC4.progress"),
        ]

        def fake_glob(path: Path, pattern: str):
            if path.name == "overlays" and pattern == "*.json":
                return iter(paths[:1])
            if path.name == "overlays" and pattern == "*.json.draft":
                return iter(paths[1:2])
            return iter(())

        def fake_rglob(path: Path, pattern: str):
            return iter(paths[2:]) if path.name == "luna-state" else iter(())

        with (
            mock.patch.object(prepare, "active_lease_ids", return_value={"PMC1"}),
            mock.patch.object(prepare.Path, "glob", autospec=True, side_effect=fake_glob),
            mock.patch.object(prepare.Path, "rglob", autospec=True, side_effect=fake_rglob),
        ):
            self.assertEqual(
                prepare.active_reading_material_ids(Path("fixture")),
                {"PMC1", "PMC2", "PMC3", "PMC4"},
            )

    def test_job_manager_reserves_uncommitted_migration_pmcids(self) -> None:
        manifests = [Path("fixture/transactions/txn-a/transaction.json"), Path("fixture/transactions/txn-b/transaction.json")]
        values = {
            str(manifests[0]): {"status": "prepared", "pmcids": ["PMC1", "PMC2"]},
            str(manifests[1]): {"status": "committed", "pmcids": ["PMC3"]},
        }

        def fake_glob(path: Path, pattern: str):
            return iter(manifests) if pattern == "*/transaction.json" else iter(())

        with (
            mock.patch.object(manager.Path, "exists", autospec=True, return_value=True),
            mock.patch.object(manager.Path, "glob", autospec=True, side_effect=fake_glob),
            mock.patch.object(manager, "read_json_object", side_effect=lambda path: values[str(path)]),
        ):
            ids, unreadable = manager.prepared_migration_ids(Path("fixture"))
        self.assertEqual(ids, {"PMC1", "PMC2"})
        self.assertFalse(unreadable)

    def _run_fixture(
        self,
        pmcid: str,
        *,
        status: str = "pending",
        expected_markdown_hash: str | None = None,
        manifest_pdf_hash: str | None = None,
        mineru_pdf_hash: str | None = None,
        write: bool = False,
    ) -> tuple[dict[str, object], mock.Mock]:
        markdown = _valid_markdown().encode("utf-8")
        pdf = f"fixture-pdf:{pmcid}".encode()
        actual_markdown_hash = _sha256(markdown)
        actual_pdf_hash = _sha256(pdf)
        expected_markdown_hash = expected_markdown_hash or actual_markdown_hash
        manifest_pdf_hash = manifest_pdf_hash or actual_pdf_hash
        mineru_pdf_hash = mineru_pdf_hash or manifest_pdf_hash
        old_source_hash = _sha256(f"old-source:{pmcid}".encode())
        old_packet_hash = _sha256(f"old-packet:{pmcid}".encode())

        selection: list[dict[str, object]] = []
        index_rows: list[dict[str, object]] = []
        pdf_rows: list[dict[str, object]] = []
        for number in range(1, 10001):
            identity = f"PMC{number:08d}"
            row: dict[str, object] = {"pmcid": identity, "reading_order": number}
            index_row: dict[str, object] = {"pmcid": identity}
            pdf_row: dict[str, object] = {"pmcid": identity}
            if identity == pmcid:
                row.update(
                    {
                        "paper_id": f"fixture:{number}",
                        "title": f"Fixture paper {number}",
                        "record_path": f"records/{pmcid}.json.gz",
                        "source_record_sha256": old_source_hash,
                        "source_hash": old_source_hash,
                        "packet_path": f"semantic-distillation/packets/{pmcid}.md",
                        "packet_sha256": old_packet_hash,
                    }
                )
                index_row.update(
                    {
                        "preferred_markdown_relpath": "source.md",
                        "preferred_markdown_sha256": expected_markdown_hash,
                        "input_sha256": mineru_pdf_hash,
                        "preferred_mode": "primary",
                        "page_count": 2,
                    }
                )
                pdf_row.update({"pdf_relpath": "source.pdf", "pdf_sha256": manifest_pdf_hash})
            selection.append(row)
            index_rows.append(index_row)
            pdf_rows.append(pdf_row)

        card = {
            "pmcid": pmcid,
            "source_record_sha256": old_source_hash,
            "reading": {"status": status, "packet_sha256": old_packet_hash},
        }
        root = Path("D:/CodeX/PaperDistill/.nonexistent-mineru-packet-test/corpus")
        export = Path("D:/CodeX/PaperDistill/.nonexistent-mineru-packet-test/export")
        manifest = Path("D:/CodeX/PaperDistill/.nonexistent-mineru-packet-test/manifest.jsonl")
        args = prepare.parse_args(
            [
                "--root",
                str(root),
                "--mineru-export",
                str(export),
                "--pdf-manifest",
                str(manifest),
                "--pmcid",
                pmcid,
                *(["--write"] if write else []),
            ]
        )

        def fake_jsonl(path: Path) -> list[dict[str, object]]:
            if path.name == "selection.jsonl":
                return selection
            if path.name == "index.jsonl":
                return index_rows
            if path.name == "manifest.jsonl":
                return pdf_rows
            raise AssertionError(f"unexpected JSONL read: {path}")

        def fake_read_bytes(path: Path, *_args: object, **_kwargs: object) -> bytes:
            if path.name == f"{pmcid}.json":
                return json.dumps(card).encode("utf-8")
            if path.name == "source.md":
                return markdown
            raise AssertionError(f"unexpected bytes read: {path}")

        def fake_sha256_file(path: Path) -> str:
            if path.name == "source.md":
                return actual_markdown_hash
            if path.name == "source.pdf":
                return actual_pdf_hash
            raise AssertionError(f"unexpected hash read: {path}")

        with (
            mock.patch.object(prepare, "jsonl", side_effect=fake_jsonl),
            mock.patch.object(prepare, "active_lease_ids", return_value=set()),
            mock.patch.object(prepare.Path, "read_bytes", autospec=True, side_effect=fake_read_bytes),
            mock.patch.object(prepare.Path, "exists", autospec=True, return_value=False),
            mock.patch.object(prepare.Path, "is_file", autospec=True, return_value=True),
            mock.patch.object(prepare.Path, "rglob", autospec=True, return_value=[]),
            mock.patch.object(prepare, "sha256_file", side_effect=fake_sha256_file) as hash_reader,
            mock.patch.object(prepare, "atomic_write") as atomic_write,
            mock.patch.object(prepare, "atomic_json") as atomic_json,
            mock.patch.object(prepare, "atomic_jsonl") as atomic_jsonl,
            mock.patch.object(prepare, "StateLock") as state_lock,
        ):
            result = prepare.run(args)
        atomic_write.assert_not_called()
        atomic_json.assert_not_called()
        atomic_jsonl.assert_not_called()
        if write:
            state_lock.assert_called_once()
        else:
            state_lock.assert_not_called()
        return result, hash_reader

    def test_completed_card_is_frozen_even_when_write_is_requested(self) -> None:
        result, hash_reader = self._run_fixture(
            self.COMPLETED,
            status="completed",
            expected_markdown_hash="3" * 64,
            manifest_pdf_hash="4" * 64,
            mineru_pdf_hash="5" * 64,
            write=True,
        )
        self.assertEqual(result["counts"]["completed_frozen"], 1)
        self.assertEqual(result["counts"]["written"], 0)
        self.assertEqual(result["items"][0]["status"], "completed_frozen")
        hash_reader.assert_not_called()

    def test_markdown_pdf_and_mineru_input_hash_mismatches_are_blocking(self) -> None:
        cases = (
            (self.MARKDOWN_MISMATCH, "preferred_markdown_hash_mismatch"),
            (self.PDF_MISMATCH, "pdf_manifest_hash_mismatch"),
            (self.INPUT_MISMATCH, "mineru_input_pdf_hash_mismatch"),
        )
        for pmcid, reason in cases:
            with self.subTest(pmcid=pmcid):
                kwargs: dict[str, object] = {"write": True}
                if pmcid == self.MARKDOWN_MISMATCH:
                    kwargs["expected_markdown_hash"] = "0" * 64
                elif pmcid == self.PDF_MISMATCH:
                    kwargs["manifest_pdf_hash"] = "1" * 64
                    kwargs["mineru_pdf_hash"] = "1" * 64
                else:
                    kwargs["mineru_pdf_hash"] = "2" * 64
                result, _ = self._run_fixture(pmcid, **kwargs)
                self.assertIn(reason, result["items"][0]["reasons"])
                self.assertEqual(result["counts"]["eligible"], 0)
                self.assertEqual(result["counts"]["written"], 0)

    def test_dry_run_reports_eligible_but_never_writes(self) -> None:
        result, _ = self._run_fixture(self.ELIGIBLE)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["counts"]["eligible"], 1)
        self.assertEqual(result["counts"]["blocked"], 0)
        self.assertEqual(result["counts"]["written"], 0)
        self.assertEqual(result["items"][0]["status"], "eligible")

    def test_active_leases_are_read_from_state_store_mapping(self) -> None:
        payload = json.dumps(
            {
                "schema_version": "1.0",
                "leases": {
                    "lease-active": {
                        "status": "active",
                        "items": [{"pmcid": self.ELIGIBLE}],
                    },
                    "lease-closed": {
                        "status": "completed",
                        "items": [{"pmcid": self.COMPLETED}],
                    },
                },
            }
        )
        with (
            mock.patch.object(prepare.Path, "exists", autospec=True, return_value=True),
            mock.patch.object(prepare.Path, "read_text", autospec=True, return_value=payload),
        ):
            self.assertEqual(prepare.active_lease_ids(Path("fixture")), {self.ELIGIBLE})


if __name__ == "__main__":
    unittest.main()
