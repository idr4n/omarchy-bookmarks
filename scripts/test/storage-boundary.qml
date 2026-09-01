import Quickshell
import Quickshell.Io
import QtQuick

ShellRoot {
  id: root

  property string resultPath: Quickshell.env("BOUNDARY_RESULT")
  property string inputPath: Quickshell.env("BOUNDARY_INPUT")
  property var model: null
  property bool readChecked: false
  property bool helperChecked: false
  property bool errorHelperChecked: false
  property bool unicodeChecked: false
  property bool unicodeValid: false
  property bool completed: false
  property int attempts: 0
  readonly property int helperOutputLimit: 64 * 1024
  readonly property string boundedReadScript: "path=$1; limit=$2; "
      + "if [ -L \"$path\" ] || [ ! -f \"$path\" ]; then exit 22; fi; "
      + "size=$(wc -c < \"$path\") || exit 22; "
      + "if [ \"$size\" -gt \"$limit\" ]; then exit 23; fi; "
      + "head -c \"$((limit + 1))\" -- \"$path\""

  function finish(passed, detail) {
    if (root.completed) return
    root.completed = true
    resultFile.setText((passed ? "PASS" : "FAIL") + "\n" + detail + "\n")
  }

  function checkFinished() {
    if (!root.readChecked
        || !root.helperChecked
        || !root.errorHelperChecked
        || !root.unicodeChecked) return
    root.finish(
      boundedRead.exitCode === 23
          && boundedReadOutput.data.byteLength === 0
          && root.unicodeValid
          && noisyHelper.overflow
          && noisyHelper.outputBytes <= root.helperOutputLimit
          && noisyError.overflow
          && noisyError.errorBytes <= root.helperOutputLimit,
      "readExit=" + String(boundedRead.exitCode)
          + " readBytes=" + String(boundedReadOutput.data.byteLength)
          + " unicode=" + String(root.unicodeValid)
          + " helperOverflow=" + String(noisyHelper.overflow)
          + " helperOut=" + String(noisyHelper.outputBytes)
          + " errorOverflow=" + String(noisyError.overflow)
          + " helperErr=" + String(noisyError.errorBytes)
    )
  }

  FileView {
    id: modelFile
    path: Quickshell.shellDir + "/../../BookmarkModel.js"
    printErrors: true
    onLoaded: {
      try {
        var loader = new Function("module", text() + "\nreturn module.exports")
        root.model = loader({ exports: ({}) })
        boundedRead.command = [
          "sh",
          "-c",
          root.boundedReadScript,
          "boundary-read",
          root.inputPath,
          String(root.model.BOOKMARK_FILE_LIMIT)
        ]
        boundedRead.running = true
        noisyHelper.running = true
        noisyError.running = true
        utf8Helper.running = true
      } catch (error) {
        root.finish(false, "model=" + String(error))
      }
    }
  }

  Process {
    id: boundedRead
    running: false
    command: []
    property int exitCode: -1

    stdout: StdioCollector {
      id: boundedReadOutput
      waitForEnd: true
    }
    stderr: StdioCollector { waitForEnd: true }
    onExited: function(code) {
      boundedRead.exitCode = code
      Qt.callLater(function() {
        root.readChecked = true
        root.checkFinished()
      })
    }
  }
  Process {
    id: utf8Helper
    running: false
    command: [
      "python3",
      "-c",
      "import os,time; os.write(1,b'A\\xc3'); time.sleep(0.05); os.write(1,b'\\xa9B')"
    ]

    stdout: StdioCollector {
      id: utf8Output
      waitForEnd: true
    }
    onExited: function(_code) {
      Qt.callLater(function() {
        root.unicodeValid = utf8Output.text === "AéB"
        root.unicodeChecked = true
        root.checkFinished()
      })
    }
  }



  Process {
    id: noisyHelper
    running: false
    command: [
      "python3",
      "-c",
      "import sys; sys.stdout.write('o' * 65537)"
    ]
    property int outputBytes: 0
    property int errorBytes: 0
    property bool overflow: false

    function collect(data, stdoutStream) {
      if (overflow) return
      var bytes = root.model.utf8ByteLength(String(data || ""))
      if ((stdoutStream ? outputBytes : errorBytes) + bytes > root.helperOutputLimit) {
        overflow = true
        if (running) running = false
        return
      }
      if (stdoutStream) outputBytes += bytes
      else errorBytes += bytes
    }

    stdout: SplitParser {
      splitMarker: ""
      onRead: function(data) { noisyHelper.collect(data, true) }
    }
    stderr: SplitParser {
      splitMarker: ""
      onRead: function(data) { noisyHelper.collect(data, false) }
    }
    onExited: function(_code) {
      Qt.callLater(function() {
        root.helperChecked = true
        root.checkFinished()
      })
    }
  }
  Process {
    id: noisyError
    running: false
    command: [
      "python3",
      "-c",
      "import sys; sys.stderr.write('e' * 65537)"
    ]
    property int errorBytes: 0
    property bool overflow: false

    stderr: SplitParser {
      splitMarker: ""
      onRead: function(data) {
        if (noisyError.overflow) return
        var bytes = root.model.utf8ByteLength(String(data || ""))
        if (noisyError.errorBytes + bytes > root.helperOutputLimit) {
          noisyError.overflow = true
          if (noisyError.running) noisyError.running = false
          return
        }
        noisyError.errorBytes += bytes
      }
    }
    onExited: function(_code) {
      Qt.callLater(function() {
        root.errorHelperChecked = true
        root.checkFinished()
      })
    }
  }



  Timer {
    interval: 50
    repeat: true
    running: !root.completed
    onTriggered: {
      root.attempts++
      if (root.attempts >= 100) {
        root.finish(
          false,
          "timeout read=" + String(root.readChecked)
              + " unicode=" + String(root.unicodeChecked)
              + " helper=" + String(root.helperChecked)
              + " errorHelper=" + String(root.errorHelperChecked)
        )
      }
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
