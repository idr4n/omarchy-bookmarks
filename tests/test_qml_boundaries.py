from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "scripts" / "test" / "storage-boundary.qml"
FILEVIEW_HARNESS = ROOT / "scripts" / "test" / "fileview-cache.qml"
FAVICON_HARNESS = ROOT / "scripts" / "test" / "favicon-visibility.qml"
BOOKMARKS_QML = ROOT / "Bookmarks.qml"
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


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

    def test_favicon_visibility_follows_normalized_path_not_image_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            data_dir = work / "data"
            cache = data_dir / "favicons-v2"
            cache.mkdir(parents=True)
            (cache / f"{'a' * 64}.png").write_bytes(PNG_1X1)
            result = work / "result"
            result.touch()
            environment = os.environ.copy()
            environment.pop("WAYLAND_DISPLAY", None)
            environment.update(
                {
                    "QT_QPA_PLATFORM": "offscreen",
                    "FAVICON_DATA_DIR": str(data_dir),
                    "FAVICON_RESULT": str(result),
                }
            )

            completed = subprocess.run(
                ["qs", "-p", str(FAVICON_HARNESS), "--no-color"],
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
            self.assertIn("safe=true legacy=false empty=false changed=true", output)
            self.assertIn("sourceMatches=true", output)


class FaviconBindingTests(unittest.TestCase):
    def test_favicon_images_bind_visibility_to_the_model_predicate(self) -> None:
        lines = BOOKMARKS_QML.read_text(encoding="utf-8").splitlines()
        text = "\n".join(lines)
        self.assertEqual(
            text.count("source.length"),
            0,
            "Image.source is a url; its length is undefined",
        )
        checked = 0
        for index, line in enumerate(lines):
            match = re.search(
                r"^\s*source: root\.faviconSource\((?P<favicon>[^()]+)\)$", line
            )
            if not match:
                continue
            checked += 1
            neighbourhood = lines[max(0, index - 8) : index + 9]
            visible = [
                candidate.strip()
                for candidate in neighbourhood
                if candidate.strip().startswith("visible:")
            ]
            self.assertEqual(
                visible,
                [
                    f"visible: BookmarkModel.hasRenderableFavicon({match.group('favicon')})"
                ],
                f"favicon Image near line {index + 1} must derive visibility from the normalized path",
            )
        self.assertEqual(checked, 2, "expected the search-row and form favicon Images")


if __name__ == "__main__":
    unittest.main()
