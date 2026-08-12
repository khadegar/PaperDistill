from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdfbench import runner  # noqa: E402
from pdfbench.runner import (  # noqa: E402
    _external_process_requires_pause,
    _format_value,
    _terminal_existing,
    _timing_phase,
    cleanup_observed_descendants,
    load_sample_set,
)


class RunnerTests(unittest.TestCase):
    def test_format_value_preserves_service_url(self) -> None:
        self.assertEqual(_format_value("http://127.0.0.1:18922", {}), "http://127.0.0.1:18922")

    def test_load_production_manifest(self) -> None:
        root = ROOT / "tests" / "fixtures" / "production_manifest"
        rows = load_sample_set({"_project_root": str(root)}, "corpus10000")
        self.assertEqual(rows[0]["sample_id"], "PMC1")
        self.assertEqual(rows[0]["pdf_sha256"], "a" * 64)

    def test_descendant_cleanup_requires_matching_creation_time(self) -> None:
        class FakeNoSuchProcess(Exception):
            pass

        class FakeAccessDenied(Exception):
            pass

        class FakeProcess:
            def __init__(self, pid: int) -> None:
                self.pid = pid

            def create_time(self) -> float:
                return {101: 10.0, 202: 99.0}[self.pid]

        class FakePsutil:
            NoSuchProcess = FakeNoSuchProcess
            AccessDenied = FakeAccessDenied
            Process = FakeProcess

        original_psutil = runner.psutil
        original_terminate = runner.terminate_process_tree
        terminated: list[int] = []
        runner.psutil = FakePsutil()
        runner.terminate_process_tree = terminated.append
        try:
            cleaned, issues = cleanup_observed_descendants({101: 10.0, 202: 20.0})
        finally:
            runner.psutil = original_psutil
            runner.terminate_process_tree = original_terminate
        self.assertEqual(cleaned, [])
        self.assertEqual(terminated, [101])
        self.assertTrue(any("PID 101: still alive" in issue for issue in issues))
        self.assertTrue(any("PID 202: skipped" in issue for issue in issues))

    def test_failed_signature_is_terminal_for_resume(self) -> None:
        failed_root = ROOT / "tests" / "fixtures" / "runner_failed"
        record = _terminal_existing(
            {"_project_root": str(failed_root)}, "mineru", "primary", "R001", "smoke", "fixture-signature"
        )
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "failed")

    def test_historical_external_process_does_not_block_resume(self) -> None:
        self.assertTrue(_external_process_requires_pause({"external_compute_process_detected": True}))
        self.assertFalse(
            _external_process_requires_pause(
                {"external_compute_process_detected": True, "resume_skipped": True}
            )
        )

    def test_failed_attempt_does_not_end_cold_start_phase(self) -> None:
        failed_root = ROOT / "tests" / "fixtures" / "runner_failed"
        success_root = ROOT / "tests" / "fixtures" / "runner_success"
        self.assertEqual(
            _timing_phase({"_project_root": str(failed_root)}, "mineru", "primary", "smoke"),
            "cold_start",
        )
        self.assertEqual(
            _timing_phase({"_project_root": str(success_root)}, "mineru", "primary", "smoke"),
            "steady_state",
        )


if __name__ == "__main__":
    unittest.main()
