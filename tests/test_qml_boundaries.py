from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "scripts" / "test" / "storage-boundary.qml"
FILEVIEW_HARNESS = ROOT / "scripts" / "test" / "fileview-cache.qml"


@unittest.skipUnless(shutil.which("qs"), "Quickshell is not installed")
class QmlBoundaryTests(unittest.TestCase):
    def test_streaming_read_and_helper_output_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / "bookmarks.json"
            result = work / "result"
            source.write_bytes(b" " * (1024 * 1024 + 1))
            result.touch()
            environment = os.environ.copy()
            environment.pop("WAYLAND_DISPLAY", None)
            environment.update(
                {
                    "QT_QPA_PLATFORM": "offscreen",
                    "BOUNDARY_INPUT": str(source),
                    "BOUNDARY_RESULT": str(result),
                }
            )

            completed = subprocess.run(
                ["qs", "-p", str(HARNESS), "--no-color"],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = result.read_text(encoding="utf-8")
            self.assertTrue(output.startswith("PASS\n"), output)
            self.assertIn("readExit=23 readBytes=0", output)
            self.assertIn("unicode=true", output)
            self.assertIn("helperOverflow=true helperOut=65536", output)
            self.assertIn("errorOverflow=true helperErr=65536", output)

    def test_fileview_rewrites_cached_payload_after_external_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / "state.json"
            result = work / "result"
            source.write_text("initial\n", encoding="utf-8")
            result.touch()
            environment = os.environ.copy()
            environment.pop("WAYLAND_DISPLAY", None)
            environment.update(
                {
                    "QT_QPA_PLATFORM": "offscreen",
                    "FILEVIEW_INPUT": str(source),
                    "FILEVIEW_RESULT": str(result),
                }
            )

            completed = subprocess.run(
                ["qs", "-p", str(FILEVIEW_HARNESS), "--no-color"],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = result.read_text(encoding="utf-8")
            self.assertTrue(output.startswith("PASS\n"), output)
            self.assertIn("saves=2", output)
            self.assertEqual(source.read_text(encoding="utf-8"), "first-é\n")


if __name__ == "__main__":
    unittest.main()
