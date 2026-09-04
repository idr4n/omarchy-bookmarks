from __future__ import annotations

import base64
import json
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

    def test_query_and_error_labels_render_markup_as_literal_text(self) -> None:
        source = BOOKMARKS_QML.read_text(encoding="utf-8")
        labels = [
            match.group(0)
            for match in re.finditer(r"(?ms)^( *)Text \{\n.*?^\1\}", source)
        ]
        bindings = (
            "root.query",
            "root.stateMessage()",
            "root.addError",
            "root.recentWarning",
        )
        for binding in bindings:
            with self.subTest(binding=binding), tempfile.TemporaryDirectory() as temporary:
                matches = [
                    block
                    for block in labels
                    if re.search(
                        rf"(?m)^\s*text:\s*{re.escape(binding)}(?:\s*\|\||\s*$)", block
                    )
                ]
                self.assertEqual(len(matches), 1, f"Expected one label for {binding}")
                label = matches[0]
                # Run the actual Text element without the desktop's Wayland panel.
                # Only theme dependencies are replaced with fixed test values.
                label = label.replace("Style.", "testStyle.").replace("Color.", "testPalette.")
                harness = Path(temporary) / "literal-text.qml"
                harness.write_text(
                    """
import Quickshell
import QtQuick

ShellRoot {
  id: root
  property string query: '<b>Bookmark &amp; text</b><br><a href="https://example.invalid/">link</a>'
  property string visibleError: query
  property string addError: query
  property string recentWarning: query
  property string addNotice: ""
  property int footerHeight: 32
  property string metadataStatus: ""
  property string formFavicon: ""
  property string fontFamily: "monospace"
  property color foreground: "white"
  function stateMessage() { return visibleError }

  QtObject {
    id: testStyle
    property var font: ({ heading: 16, body: 16, caption: 16 })
    function space(value) { return value }
  }
  QtObject { id: testPalette; property color urgent: "red" }
  Item {
    id: host
    width: 4000
    height: 500
    property real spacing: 0
    Item { id: resultCount; width: 0 }
    Text {
      id: expected
      text: root.query
      textFormat: Text.PlainText
      font.family: root.fontFamily
      font.pixelSize: 16
    }
  }
  Component.onCompleted: Qt.callLater(function() {
    var label = Qt.createQmlObject(LABEL_SOURCE, host)
    label.forceLayout()
    expected.forceLayout()
    if (expected.contentWidth > 0
        && Math.abs(label.contentWidth - expected.contentWidth) < 0.01
        && Math.abs(label.contentHeight - expected.contentHeight) < 0.01) {
      console.log("literal text PASS")
    } else {
      console.error("literal text FAIL: actual=" + label.contentWidth + "x" + label.contentHeight
          + " expected=" + expected.contentWidth + "x" + expected.contentHeight)
    }
    Qt.quit()
  })
}
""".replace(
                        "LABEL_SOURCE",
                        json.dumps(
                            "import QtQuick\nimport "
                            + json.dumps((ROOT / "BookmarkModel.js").as_uri())
                            + " as BookmarkModel\n"
                            + label
                        ),
                    ),
                    encoding="utf-8",
                )
                environment = os.environ.copy()
                environment.pop("WAYLAND_DISPLAY", None)
                environment["QT_QPA_PLATFORM"] = "offscreen"
                completed = subprocess.run(
                    ["qs", "-p", str(harness), "--no-color"],
                    cwd=ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                output = completed.stdout + completed.stderr
                self.assertEqual(completed.returncode, 0, output)
                self.assertIn("literal text PASS", output)


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
