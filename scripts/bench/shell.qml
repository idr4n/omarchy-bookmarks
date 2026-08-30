import Quickshell
import Quickshell.Io
import QtQuick

ShellRoot {
  id: root

  property bool completed: false
  property string resultPath: Quickshell.env("BENCHMARK_RESULT")
  property var model: null

  function representativeDocument(count) {
    var bookmarks = []
    for (var i = 0; i < count; i++) {
      bookmarks.push({
        id: "bench-" + String(i),
        title: "Reference item " + String(i) + " for local documentation",
        url: "https://docs-" + String(i % 200) + ".example/path/" + String(i) + "?section=" + String(i % 17),
        tags: ["tag-" + String(i % 31), "collection-" + String(i % 7)],
        createdAt: "2026-08-30T12:00:00.000Z"
      })
    }
    return JSON.stringify({ schemaVersion: 1, bookmarks: bookmarks })
  }

  function median(values) {
    values.sort(function(left, right) { return left - right })
    return values[Math.floor(values.length / 2)]
  }


  function measureParse(raw, iterations) {
    var parseTimings = []
    var indexTimings = []
    var totalTimings = []
    var parsed = null
    var index = []
    for (var i = 0; i < iterations; i++) {
      var started = Date.now()
      parsed = root.model.parseBookmarks(raw)
      var parsedAt = Date.now()
      if (parsed.state !== "valid") throw new Error("benchmark document did not parse")
      index = root.model.buildSearchIndex(parsed.document.bookmarks)
      var finished = Date.now()
      parseTimings.push(parsedAt - started)
      indexTimings.push(finished - parsedAt)
      totalTimings.push(finished - started)
    }
    return {
      milliseconds: root.median(totalTimings),
      parseMilliseconds: root.median(parseTimings),
      indexMilliseconds: root.median(indexTimings),
      index: index
    }
  }

  function measureSearch(index, query, iterations) {
    var timings = []
    var rows = []
    for (var i = 0; i < iterations; i++) {
      var started = Date.now()
      rows = root.model.searchBookmarks(index, query, [], 60)
      timings.push(Date.now() - started)
    }
    return { milliseconds: root.median(timings), rows: rows.length }
  }

  function runSize(count, parseIterations, searchIterations) {
    var raw = root.representativeDocument(count)
    var parsed = root.measureParse(raw, parseIterations)
    var noMatch = root.measureSearch(parsed.index, "definitely-absent-token", searchIterations)
    var matched = root.measureSearch(parsed.index, "reference tag-7", searchIterations)
    console.log(
      "bookmarks benchmark records=" + String(count)
      + " bytes=" + String(raw.length)
      + " parse_index_ms=" + String(parsed.milliseconds)
      + " parse_ms=" + String(parsed.parseMilliseconds)
      + " index_ms=" + String(parsed.indexMilliseconds)
      + " no_match_ms=" + String(noMatch.milliseconds)
      + " match_sort_ms=" + String(matched.milliseconds)
      + " matched_rows=" + String(matched.rows)
    )
    return {
      parseIndexMilliseconds: parsed.milliseconds,
      noMatchMilliseconds: noMatch.milliseconds,
      matchSortMilliseconds: matched.milliseconds
    }
  }

  function run() {
    if (root.completed || !root.model) return
    root.completed = true

    var small = root.runSize(350, 7, 11)
    var large = root.runSize(10000, 3, 5)
    var passed = large.parseIndexMilliseconds < 150 && large.matchSortMilliseconds < 16
    console.log(
      "bookmarks benchmark result=" + (passed ? "PASS" : "FAIL")
      + " small_parse_index_ms=" + String(small.parseIndexMilliseconds)
      + " large_parse_index_ms=" + String(large.parseIndexMilliseconds)
      + " large_match_sort_ms=" + String(large.matchSortMilliseconds)
    )

    if (!root.resultPath) {
      console.error("BENCHMARK_RESULT is required")
      return
    }
    if (passed) resultFile.setText("PASS\n")
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
        console.error("Could not load BookmarkModel.js: " + String(error))
      }
    }
  }

  FileView {
    id: resultFile
    path: root.resultPath
    atomicWrites: true
    printErrors: true
  }
}
