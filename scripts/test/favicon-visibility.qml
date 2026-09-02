import Quickshell
import Quickshell.Io
import QtQuick

// Regression harness for favicon visibility. Image.source is a QML url, which
// reaches JavaScript as an object without `length`, so visibility must derive
// from the normalized favicon path exactly as Bookmarks.qml binds it.
ShellRoot {
  id: root

  property string resultPath: Quickshell.env("FAVICON_RESULT")
  property string dataDir: Quickshell.env("FAVICON_DATA_DIR")
  property var model: null
  property string favicon: ""
  property bool completed: false
  property int attempts: 0
  readonly property string safeFavicon: "favicons-v2/" + Array(65).join("a") + ".png"
  readonly property string legacyFavicon: "favicons/" + Array(65).join("b") + ".png"

  function finish(passed, detail) {
    if (root.completed) return
    root.completed = true
    resultFile.setText((passed ? "PASS" : "FAIL") + "\n" + detail + "\n")
  }

  function faviconSource(favicon) {
    var path = root.model ? root.model.normalizeSafeFavicon(String(favicon || "")) : ""
    return path ? "file://" + encodeURI(root.dataDir + "/" + path) : ""
  }

  function observe(favicon) {
    root.favicon = favicon
    return icon.visible
  }

  function run() {
    var safe = root.observe(root.safeFavicon)
    var legacy = root.observe(root.legacyFavicon)
    var empty = root.observe("")
    var changed = root.observe(root.safeFavicon)
    root.finish(
      safe === true && legacy === false && empty === false && changed === true,
      "safe=" + String(safe)
          + " legacy=" + String(legacy)
          + " empty=" + String(empty)
          + " changed=" + String(changed)
          + " urlType=" + typeof icon.source
          + " urlLength=" + String(icon.source.length)
          + " sourceMatches=" + String(String(icon.source) === root.faviconSource(root.safeFavicon))
    )
  }

  // A parentless item never becomes effectively visible, so host the Image in an Item.
  Item {
    Image {
      id: icon
      source: root.faviconSource(root.favicon)
      visible: root.model !== null && root.model.hasRenderableFavicon(root.favicon)
      asynchronous: true
    }
  }

  FileView {
    id: modelFile
    path: Quickshell.shellDir + "/../../BookmarkModel.js"
    printErrors: true
    onLoaded: {
      try {
        var loader = new Function("module", text() + "\nreturn module.exports")
        root.model = loader({ exports: ({}) })
        Qt.callLater(root.run)
      } catch (error) {
        root.finish(false, "model=" + String(error))
      }
    }
  }

  Timer {
    interval: 50
    repeat: true
    running: !root.completed
    onTriggered: {
      root.attempts++
      if (root.attempts >= 100) root.finish(false, "timeout model=" + String(root.model !== null))
    }
  }

  FileView {
    id: resultFile
    path: root.resultPath
    atomicWrites: true
    printErrors: true
    onSaved: Qt.quit()
  }
}
