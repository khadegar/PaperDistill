from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdfbench import common  # noqa: E402
from pdfbench.common import write_json_once  # noqa: E402


class CommonTests(unittest.TestCase):
    def test_concurrent_sentinel_writers_only_create_once(self) -> None:
        temp_root = ROOT / "tmp"
        temp_root.mkdir(exist_ok=True)
        target = temp_root / f"successful-run-{uuid.uuid4().hex}.json"
        try:
            with patch("pdfbench.common.os.fsync"):
                with ThreadPoolExecutor(max_workers=8) as executor:
                    created = list(executor.map(lambda index: write_json_once(target, {"index": index}), range(16)))

            self.assertEqual(created.count(True), 1)
            self.assertEqual(created.count(False), 15)
            self.assertIn(json.loads(target.read_text(encoding="utf-8"))["index"], range(16))
        finally:
            target.unlink(missing_ok=True)

    def test_atomic_replace_retries_transient_permission_error(self) -> None:
        temp_root = ROOT / "tmp"
        temp_root.mkdir(exist_ok=True)
        target = temp_root / f"atomic-retry-{uuid.uuid4().hex}.json"
        real_replace = common.os.replace
        attempts = 0

        def flaky_replace(source: str, destination: Path) -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise PermissionError("simulated Windows sharing violation")
            real_replace(source, destination)

        try:
            with patch("pdfbench.common.os.fsync"), patch("pdfbench.common.time.sleep"), patch(
                "pdfbench.common.os.replace", side_effect=flaky_replace
            ):
                common._atomic_replace(target, b'{"ok":true}\n')
            self.assertEqual(attempts, 3)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"ok": True})
        finally:
            target.unlink(missing_ok=True)

    def test_atomic_replace_cleans_temp_after_retry_exhaustion(self) -> None:
        temp_root = ROOT / "tmp"
        temp_root.mkdir(exist_ok=True)
        target = temp_root / f"atomic-fail-{uuid.uuid4().hex}.json"
        pattern = f".{target.name}.*.tmp"
        before = set(temp_root.glob(pattern))
        with patch("pdfbench.common.os.fsync"), patch("pdfbench.common.time.sleep"), patch(
            "pdfbench.common.os.replace", side_effect=PermissionError("persistent sharing violation")
        ):
            with self.assertRaises(PermissionError):
                common._atomic_replace(target, b"payload")
        self.assertEqual(set(temp_root.glob(pattern)), before)


if __name__ == "__main__":
    unittest.main()
