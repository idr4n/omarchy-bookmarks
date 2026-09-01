import Quickshell
import Quickshell.Io
import QtQuick

ShellRoot {
  id: root

  property string inputPath: Quickshell.env("FILEVIEW_INPUT")
  property string resultPath: Quickshell.env("FILEVIEW_RESULT")
  property string payload: "first-é\n"
  property string cachedPayload: ""
  property bool fileEnabled: true
  property int saves: 0
  property bool completed: false
  property int attempts: 0

  function finish(passed, detail) {
    if (root.completed) return
    root.completed = true
    resultFile.setText((passed ? "PASS" : "FAIL") + "\n" + detail + "\n")
  }

  function writeCachedPayload() {
    if (root.payload !== root.cachedPayload) {
      dataFile.setText(root.payload)
      return
    }
    root.fileEnabled = false
    Qt.callLater(function() {
      root.fileEnabled = true
      Qt.callLater(function() { dataFile.setText(root.payload) })
    })
  }

  Component.onCompleted: Qt.callLater(function() { root.writeCachedPayload() })

  FileView {
    id: dataFile
    path: root.fileEnabled ? root.inputPath : ""
    preload: false
    watchChanges: true
    atomicWrites: true
    printErrors: true
    onSaved: {
      root.saves++
      root.cachedPayload = root.payload
      if (root.saves === 1) externalWrite.running = true
      else verifyRead.running = true
    }
    onSaveFailed: root.finish(false, "save-failed")
  }

  Process {
    id: externalWrite
    running: false
    command: [
      "python3",
      "-c",
      "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('external\\n', encoding='utf-8')",
      root.inputPath
    ]
    stderr: StdioCollector {
      id: externalError
      waitForEnd: true
    }
    onExited: function(code) {
      if (code !== 0) {
        root.finish(false, "external-write=" + String(code) + " " + externalError.text)
        return
      }
      root.writeCachedPayload()
    }
  }

  Process {
    id: verifyRead
    running: false
    command: ["cat", "--", root.inputPath]
    stdout: StdioCollector {
      id: verifyOutput
      waitForEnd: true
    }
    stderr: StdioCollector {
      id: verifyError
      waitForEnd: true
    }
    onExited: function(code) {
      root.finish(
        code === 0 && root.saves === 2 && verifyOutput.text === root.payload,
        "read=" + String(code)
            + " saves=" + String(root.saves)
            + " payload=" + JSON.stringify(verifyOutput.text)
            + " error=" + verifyError.text
      )
    }
  }

  Timer {
    interval: 50
    repeat: true
    running: !root.completed
    onTriggered: {
      root.attempts++
      if (root.attempts >= 100) root.finish(false, "timeout saves=" + String(root.saves))
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
