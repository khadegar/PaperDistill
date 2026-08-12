from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdfbench.deployment import BUNDLE_INCLUDE_ROOTS, _is_reproducible_bundle_file  # noqa: E402


class DeploymentTests(unittest.TestCase):
    def test_volatile_model_cache_logs_are_excluded(self) -> None:
        self.assertFalse(
            _is_reproducible_bundle_file(
                Path("offline/models/docling/huggingface/xet/logs/session.log")
            )
        )
        self.assertTrue(
            _is_reproducible_bundle_file(
                Path("offline/models/docling/model/model.safetensors")
            )
        )

    def test_python_bytecode_is_excluded_from_bundle(self) -> None:
        self.assertFalse(_is_reproducible_bundle_file(Path("src/pdfbench/__pycache__/cli.cpython-311.pyc")))
        self.assertFalse(_is_reproducible_bundle_file(Path("src/pdfbench/cli.pyc")))
        self.assertTrue(_is_reproducible_bundle_file(Path("src/pdfbench/cli.py")))

    def test_runtime_and_tests_are_included_in_bundle_manifest(self) -> None:
        self.assertIn("offline/python", BUNDLE_INCLUDE_ROOTS)
        self.assertIn("tests", BUNDLE_INCLUDE_ROOTS)


if __name__ == "__main__":
    unittest.main()
