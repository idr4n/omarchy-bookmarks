import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import QtQuick
import qs.Commons
import qs.Ui
import "BookmarkModel.js" as BookmarkModel

Item {
  id: root

  property bool opened: false
  property var payload: ({})
  property string mode: "search"
  property string query: ""
  property int selectedIndex: 0
  property bool cursorActive: true
  property string addError: ""
  property string addNotice: ""
  property string recentWarning: ""
  property bool bookmarkSavePending: false
  property var pendingBookmarkParse: null
  property string pendingBookmarkId: ""
  property string pendingMutation: ""
  property string pendingCleanupFavicon: ""
  property string editingBookmarkId: ""
  property string editingOriginalUrl: ""
  property string editingOriginalFavicon: ""
  property string formFavicon: ""
  property string titleOrigin: "empty"
  property bool settingFormFields: false
  property bool deleteConfirmationVisible: false
  property string metadataStatus: ""
  property string metadataWantedUrl: ""
  property bool metadataPending: false

  property string home: Quickshell.env("HOME")
  property string dataHome: root.absoluteXdgHome(Quickshell.env("XDG_DATA_HOME"), root.home + "/.local/share")
  property string stateHome: root.absoluteXdgHome(Quickshell.env("XDG_STATE_HOME"), root.home + "/.local/state")
  property string dataDir: root.dataHome + "/io.github.idr4n.bookmarks"
  property string stateDir: root.stateHome + "/io.github.idr4n.bookmarks"
  property string bookmarksPath: root.dataDir + "/bookmarks.json"
  property string recentPath: root.stateDir + "/recent.json"
  property string metadataHelperPath: root.localFilePath(Qt.resolvedUrl("bookmark_metadata.py"))

  property bool bookmarksReady: false
  property bool recentReady: false
  property bool bookmarksLoaded: false
  property bool recentLoaded: false
  property string storageError: ""
  property string bookmarkError: ""
  property var bookmarkParse: BookmarkModel.parseBookmarks(null)
  property var recentParse: BookmarkModel.parseRecent(null)
  property var bookmarks: []
  property var searchIndex: []
  property var recentIds: []
  property string lastBookmarksPayload: ""
  property string lastRecentPayload: ""

  readonly property bool loading: !root.bookmarksLoaded || !root.recentLoaded
  readonly property string visibleError: root.storageError || root.bookmarkError
  readonly property bool readOnly: root.bookmarkError.length > 0
  readonly property bool formVisible: root.mode === "add" || root.mode === "edit"
  readonly property bool formBusy: root.bookmarkSavePending

  property color background: Color.menu.background
  property color foreground: Color.menu.text
  property color border: Color.menu.border
  property var borderSpec: Border.surfaceSpec("menu", "border", border, Math.max(1, Style.space(2)))
  property color scrim: Color.menu.scrim
  property color selectedBackground: Color.menu.selectedBackground
  property color selectedText: Color.menu.selectedText
  readonly property int cornerRadius: Style.cornerRadius
  property string fontFamily: Style.font.menuFamily
  property int contentMargin: Style.spacing.panelPadding
  property int contentSpacing: Style.spacing.md
  property int headerHeight: Math.max(Style.space(38), Style.font.title + Style.spacing.controlPaddingY * 2)
  property int footerHeight: Math.max(Style.space(28), Style.font.caption + Style.spacing.controlPaddingY)
  property int cardWidth: Math.min(Style.space(root.formVisible ? 760 : 900), panel.width - Style.gapsOut * 2)
  property int cardHeight: Math.min(root.formVisible
      ? formView.implicitHeight + card.contentTopInset + card.contentBottomInset
      : Style.space(620), panel.height - Style.gapsOut * 2)
  property int rowHeight: Math.max(Style.space(64), Style.font.body + Style.font.caption * 2 + Style.spacing.rowPaddingX * 2)

  property string bootstrapScript: "set -eu\nif [ -L \"$1\" ]; then exit 20; fi\ninstall -d -m 0700 \"$1\"\nif [ -L \"$2\" ] || { [ -e \"$2\" ] && [ ! -f \"$2\" ]; }; then exit 21; fi\nif [ ! -e \"$2\" ]; then\n  umask 077\n  printf '%s' \"$3\" > \"$2\"\nfi\nchmod 0700 \"$1\"\nchmod 0600 \"$2\""

  function absoluteXdgHome(value, fallback) {
    var candidate = String(value || "")
    if (candidate.charAt(0) !== "/") candidate = fallback
    while (candidate.length > 1 && candidate.charAt(candidate.length - 1) === "/") {
      candidate = candidate.substring(0, candidate.length - 1)
    }
    return candidate
  }
  function localFilePath(value) {
    var path = String(value || "")
    if (path.indexOf("file://") === 0) path = path.substring(7)
    try {
      return decodeURIComponent(path)
    } catch (error) {
      return path
    }
  }
  function faviconSource(favicon) {
    var path = BookmarkModel.normalizeFavicon(String(favicon || ""))
    return path ? "file://" + encodeURI(root.dataDir + "/" + path) : ""
  }

  function parsePayload(payloadJson) {
    if (payloadJson === undefined || payloadJson === null || payloadJson === "") return ({})

    try {
      var parsed = typeof payloadJson === "string" ? JSON.parse(payloadJson) : payloadJson
      if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return ({})
      return parsed
    } catch (error) {
      return ({})
    }
  }

  function enterSearch(resetQuery) {
    root.mode = "search"
    root.deleteConfirmationVisible = false
    if (resetQuery) root.query = ""
    root.selectedIndex = 0
    root.cursorActive = true
    root.disarmPointer()
    root.rebuildDisplay(false)
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function setFormFields(url, title, tags, favicon, origin) {
    root.settingFormFields = true
    urlField.text = String(url || "")
    titleField.text = String(title || "")
    tagsField.text = Array.isArray(tags) ? tags.join(", ") : String(tags || "")
    root.formFavicon = BookmarkModel.normalizeFavicon(String(favicon || ""))
    root.titleOrigin = origin || "empty"
    root.settingFormFields = false
  }

  function enterAdd(resetForm) {
    root.discardUnreferencedFormFavicon()
    root.mode = "add"
    root.editingBookmarkId = ""
    root.editingOriginalUrl = ""
    root.editingOriginalFavicon = ""
    root.deleteConfirmationVisible = false
    root.metadataStatus = ""
    root.metadataWantedUrl = ""
    root.metadataPending = false
    metadataDebounce.stop()
    urlInputDebounce.stop()
    root.addError = ""
    if (resetForm) root.setFormFields("", "", [], "", "empty")
    Qt.callLater(function() { urlField.forceActiveFocus() })
  }

  function bookmarkById(bookmarkId) {
    for (var i = 0; i < root.bookmarks.length; i++) {
      if (root.bookmarks[i].id === bookmarkId) return root.bookmarks[i]
    }
    return null
  }
  function editingBookmarkTitle() {
    var bookmark = root.bookmarkById(root.editingBookmarkId)
    if (bookmark) return bookmark.title
    return titleField.text || BookmarkModel.urlHost(urlField.text)
  }

  function enterEditSelected() {
    var bookmark = root.bookmarkById(root.selectedId())
    if (!bookmark) return
    root.discardUnreferencedFormFavicon()
    root.mode = "edit"
    root.editingBookmarkId = bookmark.id
    root.editingOriginalUrl = bookmark.url
    root.editingOriginalFavicon = BookmarkModel.normalizeFavicon(String(bookmark.favicon || ""))
    root.deleteConfirmationVisible = false
    root.metadataStatus = ""
    root.metadataWantedUrl = ""
    root.metadataPending = false
    metadataDebounce.stop()
    urlInputDebounce.stop()
    root.addError = ""
    root.setFormFields(bookmark.url, bookmark.title, bookmark.tags, bookmark.favicon || "", "existing")
    Qt.callLater(function() { urlField.forceActiveFocus() })
  }

  function leaveForm() {
    if (root.deleteConfirmationVisible) {
      root.deleteConfirmationVisible = false
      Qt.callLater(function() { deleteButton.forceActiveFocus() })
      return
    }
    root.discardUnreferencedFormFavicon()
    root.enterSearch(false)
  }

  function handleFormShortcut(event) {
    var ctrl = (event.modifiers & Qt.ControlModifier) !== 0
    if (!root.formVisible || !ctrl
        || (event.key !== Qt.Key_Return && event.key !== Qt.Key_Enter)) return
    root.submitForm()
    event.accepted = true
  }

  function open(payloadJson) {
    if (root.formVisible) root.discardUnreferencedFormFavicon()
    root.payload = root.parsePayload(payloadJson)
    root.addNotice = ""
    root.opened = true
    if (root.payload.mode === "add") root.enterAdd(true)
    else root.enterSearch(true)
  }

  function close() {
    if (root.formVisible) root.discardUnreferencedFormFavicon()
    root.deleteConfirmationVisible = false
    root.opened = false
  }

  function toggle() {
    if (root.opened) root.close()
    else root.open("{}")
  }

  function describeBookmarkError(result) {
    if (!result || !result.error) return "Bookmark data could not be loaded"
    if (result.state === "unsupported") return "Unsupported bookmark data version"
    if (result.error.code === "invalid-json") return "Bookmarks contain invalid JSON"
    if (result.error.code === "duplicate-id") return "Bookmarks contain a duplicate ID"
    if (result.error.code === "duplicate-url") return "Bookmarks contain a duplicate URL"
    if (result.error.index !== undefined) return "Invalid bookmark at row " + String(result.error.index + 1)
    return "Bookmark data has an invalid structure"
  }

  function loadBookmarks(rawText) {
    var raw = String(rawText === undefined || rawText === null ? "" : rawText)
    root.bookmarksLoaded = true
    if (raw === root.lastBookmarksPayload && root.lastBookmarksPayload) {
      root.lastBookmarksPayload = ""
      return
    }

    var priorBookmarkError = root.bookmarkError
    var parsed = BookmarkModel.parseBookmarks(raw)
    root.bookmarkParse = parsed
    if (parsed.state !== "valid" && parsed.state !== "missing") {
      root.bookmarkError = root.describeBookmarkError(parsed)
      displayModel.clear()
      return
    }

    if (root.addError === priorBookmarkError) root.addError = ""
    root.bookmarkError = ""
    root.bookmarks = parsed.document.bookmarks
    root.searchIndex = BookmarkModel.buildSearchIndex(root.bookmarks)
    root.rebuildDisplay(true)
  }

  function failBookmarksLoad() {
    root.bookmarksLoaded = true
    root.bookmarkError = "Bookmark data could not be read"
    displayModel.clear()
  }

  function loadRecent(rawText) {
    var raw = String(rawText === undefined || rawText === null ? "" : rawText)
    root.recentLoaded = true
    if (raw === root.lastRecentPayload && root.lastRecentPayload) {
      root.lastRecentPayload = ""
      return
    }

    root.recentParse = BookmarkModel.parseRecent(raw)
    root.recentIds = root.recentParse.document.recentIds
    root.rebuildDisplay(true)
  }

  function failRecentLoad() {
    root.recentLoaded = true
    root.recentParse = BookmarkModel.parseRecent(null)
    root.recentIds = []
    root.rebuildDisplay(true)
  }

  function selectedId() {
    if (displayModel.count === 0 || root.selectedIndex < 0 || root.selectedIndex >= displayModel.count) return ""
    return displayModel.get(root.selectedIndex).bookmarkId
  }

  function rebuildDisplay(preserveSelection) {
    var priorId = preserveSelection ? root.selectedId() : ""
    var priorIndex = root.selectedIndex
    var rows = root.visibleError ? [] : BookmarkModel.searchBookmarks(root.searchIndex, root.query, root.recentIds, BookmarkModel.DISPLAY_LIMIT)

    displayModel.clear()
    for (var i = 0; i < rows.length; i++) {
      displayModel.append({
        bookmarkId: rows[i].id,
        title: rows[i].title,
        url: rows[i].url,
        domain: BookmarkModel.urlHost(rows[i].url),
        tagsText: rows[i].tags.length ? "#" + rows[i].tags.join("  #") : "",
        favicon: rows[i].favicon || ""
      })
    }

    if (displayModel.count === 0) {
      root.selectedIndex = 0
      return
    }

    var restored = -1
    if (priorId) {
      for (var j = 0; j < displayModel.count; j++) {
        if (displayModel.get(j).bookmarkId === priorId) {
          restored = j
          break
        }
      }
    }
    root.selectedIndex = restored >= 0
        ? restored
        : Math.max(0, Math.min(priorIndex, displayModel.count - 1))

    Qt.callLater(function() {
      if (displayModel.count > 0) resultList.positionViewAtIndex(root.selectedIndex, ListView.Contain)
    })
  }

  function setQuery(nextQuery) {
    root.query = nextQuery
    root.selectedIndex = 0
    root.cursorActive = true
    root.disarmPointer()
    root.rebuildDisplay(false)
  }

  function select(delta) {
    if (displayModel.count === 0) return
    root.disarmPointer()
    root.cursorActive = true
    root.selectedIndex = (root.selectedIndex + delta + displayModel.count) % displayModel.count
    resultList.positionViewAtIndex(root.selectedIndex, ListView.Contain)
  }

  function disarmPointer() {
    pointerGate.reset()
  }

  function selectFromPointer(index, item, mouse) {
    if (!pointerGate.moved(item, mouse)) return
    root.cursorActive = true
    root.selectedIndex = index
  }

  function openIndex(index) {
    if (index < 0 || index >= displayModel.count) return
    var row = displayModel.get(index)
    if (!BookmarkModel.isValidHttpUrl(row.url)) {
      root.bookmarkError = "Selected bookmark URL is invalid"
      root.rebuildDisplay(false)
      return
    }

    root.recordRecent(row.bookmarkId)
    root.close()
    Quickshell.execDetached(["xdg-open", row.url])
  }

  function persistRecent(nextIds) {
    var document = ({})
    var current = root.recentParse && root.recentParse.document ? root.recentParse.document : BookmarkModel.emptyRecentDocument()
    for (var key in current) document[key] = current[key]
    document.schemaVersion = 1
    document.recentIds = nextIds

    var staged = {
      state: "valid",
      writable: true,
      document: document,
      error: null
    }
    var payload
    try {
      payload = BookmarkModel.serializeRecent(staged)
    } catch (error) {
      root.recentWarning = "Recent state could not be prepared"
      return
    }

    root.recentParse = staged
    root.recentIds = nextIds
    root.recentWarning = ""
    if (root.recentReady) {
      root.lastRecentPayload = payload
      recentFile.setText(payload)
    }
    if (root.mode === "search") root.rebuildDisplay(true)
  }

  function recordRecent(bookmarkId) {
    root.persistRecent(BookmarkModel.touchRecent(root.recentIds, bookmarkId))
  }

  function removeRecentBookmark(bookmarkId) {
    root.persistRecent(BookmarkModel.removeRecent(root.recentIds, bookmarkId))
  }

  function selectBookmark(bookmarkId) {
    for (var i = 0; i < displayModel.count; i++) {
      if (displayModel.get(i).bookmarkId === bookmarkId) {
        root.selectedIndex = i
        root.cursorActive = true
        resultList.positionViewAtIndex(i, ListView.Contain)
        return
      }
    }
  }
  function faviconReferenced(favicon) {
    if (!favicon) return false
    for (var i = 0; i < root.bookmarks.length; i++) {
      if (String(root.bookmarks[i].favicon || "") === favicon) return true
    }
    var pending = root.pendingBookmarkParse
        && root.pendingBookmarkParse.document
        && Array.isArray(root.pendingBookmarkParse.document.bookmarks)
        ? root.pendingBookmarkParse.document.bookmarks
        : []
    for (var j = 0; j < pending.length; j++) {
      if (String(pending[j].favicon || "") === favicon) return true
    }
    return root.bookmarkSavePending
        && (root.pendingCleanupFavicon === favicon || root.formFavicon === favicon)
  }
  function duplicateBookmarkForUrl(url) {
    var key = BookmarkModel.duplicateKey(url)
    if (!key) return null
    for (var i = 0; i < root.bookmarks.length; i++) {
      var bookmark = root.bookmarks[i]
      if (bookmark.id !== root.editingBookmarkId
          && BookmarkModel.duplicateKey(bookmark.url) === key) return bookmark
    }
    return null
  }

  function cancelMetadataRequest(clearStatus) {
    root.metadataWantedUrl = ""
    root.metadataPending = false
    metadataDebounce.stop()
    metadataWatchdog.stop()
    if (metadataProcess.running) metadataProcess.running = false
    if (clearStatus !== false) root.metadataStatus = ""
  }


  function cleanupFaviconIfUnused(favicon) {
    var path = BookmarkModel.normalizeFavicon(String(favicon || ""))
    if (!path || root.faviconReferenced(path)) return
    Quickshell.execDetached([
      "python3",
      root.metadataHelperPath,
      "remove",
      "--favicon",
      path,
      "--data-dir",
      root.dataDir
    ])
  }

  function discardUnreferencedFormFavicon() {
    var discarded = root.formFavicon
    root.formFavicon = ""
    root.cancelMetadataRequest(true)
    urlInputDebounce.stop()
    root.cleanupFaviconIfUnused(discarded)
  }

  function replaceFormFavicon(favicon) {
    var next = BookmarkModel.normalizeFavicon(String(favicon || ""))
    var previous = root.formFavicon
    root.formFavicon = next
    if (previous && previous !== next) root.cleanupFaviconIfUnused(previous)
  }

  function handleUrlChanged() {
    if (root.settingFormFields || !root.formVisible) return
    root.addError = ""
    root.cancelMetadataRequest(true)
    urlInputDebounce.restart()
  }

  function processUrlInput() {
    if (!root.formVisible) return
    var raw = String(urlField.text || "")
    var trimmed = raw.trim()
    var directUrl = BookmarkModel.normalizeUrl(trimmed)
    var extracted = BookmarkModel.parsePastedInput(raw, "")
    var url = directUrl

    if (!directUrl && extracted.url !== trimmed && BookmarkModel.isValidHttpUrl(extracted.url)) {
      root.settingFormFields = true
      urlField.text = extracted.url
      if (extracted.title) {
        titleField.text = extracted.title
        root.titleOrigin = "pasted"
      }
      root.settingFormFields = false
      url = extracted.url
    }

    if (root.mode === "edit" && url === root.editingOriginalUrl) {
      root.replaceFormFavicon(root.editingOriginalFavicon)
      root.cancelMetadataRequest(true)
      return
    }

    root.replaceFormFavicon("")
    if (!url) {
      root.cancelMetadataRequest(true)
      return
    }

    var duplicate = root.duplicateBookmarkForUrl(url)
    if (duplicate) {
      root.cancelMetadataRequest(false)
      root.metadataStatus = "Another bookmark already uses this URL"
      return
    }

    root.metadataWantedUrl = url
    root.metadataPending = true
    root.metadataStatus = "Fetching page title and favicon…"
    metadataDebounce.restart()
  }

  function handleTitleChanged() {
    root.addError = ""
    if (!root.settingFormFields && root.formVisible) root.titleOrigin = "manual"
  }

  function startWantedMetadata() {
    var url = root.metadataWantedUrl
    if (!root.formVisible || metadataProcess.running
        || !url || BookmarkModel.normalizeUrl(urlField.text) !== url) return
    metadataProcess.requestedUrl = url
    metadataProcess.receivedOutput = false
    metadataProcess.command = [
      "python3",
      root.metadataHelperPath,
      "fetch",
      "--url",
      url,
      "--data-dir",
      root.dataDir
    ]
    metadataProcess.running = true
  }

  function applyMetadataOutput(requestedUrl, rawText) {
    var result
    try {
      result = JSON.parse(String(rawText || ""))
    } catch (error) {
      result = ({ ok: false, error: "invalid-output" })
    }

    var favicon = BookmarkModel.normalizeFavicon(String(result.favicon || ""))
    var currentUrl = root.formVisible ? BookmarkModel.normalizeUrl(urlField.text) : ""
    if (!root.formVisible || requestedUrl !== currentUrl || requestedUrl !== root.metadataWantedUrl) {
      root.cleanupFaviconIfUnused(favicon)
      return
    }

    var enteredTitleKept = root.titleOrigin === "pasted" || root.titleOrigin === "manual"
    root.replaceFormFavicon(favicon)
    if (result.title && (root.titleOrigin === "empty" || root.titleOrigin === "metadata" || root.titleOrigin === "existing")) {
      root.settingFormFields = true
      titleField.text = String(result.title)
      root.titleOrigin = "metadata"
      root.settingFormFields = false
    }
    root.metadataPending = false

    if (!result.ok) {
      root.metadataStatus = "Page details unavailable — the URL host will be used if title is empty"
    } else if (result.warning === "favicon-unavailable") {
      root.metadataStatus = enteredTitleKept ? "Entered title kept; favicon unavailable" : "Title loaded; favicon unavailable"
    } else if (result.warning === "title-unavailable") {
      root.metadataStatus = "Favicon loaded; the URL host will be used if title is empty"
    } else {
      root.metadataStatus = enteredTitleKept ? "Favicon loaded; entered title kept" : "Title and favicon loaded"
    }
  }

  function finishMetadataProcess(requestedUrl) {
    if (root.formVisible && root.metadataWantedUrl
        && root.metadataWantedUrl !== requestedUrl) {
      Qt.callLater(function() { root.startWantedMetadata() })
    }
  }

  function mutationError(error) {
    if (!error) return "Bookmark could not be changed"
    if (error.code === "duplicate-url") return "Another bookmark already uses this URL"
    if (error.code === "invalid-url") return "Enter an absolute HTTP(S) URL"
    if (error.code === "not-found") return "Bookmark no longer exists"
    if (error.code === "invalid-favicon") return "Cached favicon path is invalid"
    return "Bookmark data is read-only"
  }

  function stageBookmarkMutation(parseResult, bookmarkId, mutation, cleanupFavicon) {
    var payload
    var currentPayload
    try {
      payload = BookmarkModel.serializeBookmarks(parseResult)
      currentPayload = BookmarkModel.serializeBookmarks(root.bookmarkParse)
    } catch (error) {
      root.addError = "Bookmark data could not be serialized"
      return
    }
    root.cancelMetadataRequest(true)

    root.pendingBookmarkParse = parseResult
    root.pendingBookmarkId = bookmarkId
    root.pendingMutation = mutation
    root.pendingCleanupFavicon = cleanupFavicon || ""
    root.bookmarkSavePending = true
    if (payload === currentPayload) {
      root.lastBookmarksPayload = ""
      root.completeBookmarkSave()
      return
    }
    root.lastBookmarksPayload = payload
    bookmarksFile.setText(payload)
  }

  function submitForm() {
    root.addError = ""
    if (!root.formVisible || root.formBusy) return
    if (root.loading || !root.bookmarksReady) {
      root.addError = "Bookmark storage is still loading"
      return
    }
    if (root.storageError || root.bookmarkError || !root.bookmarkParse.writable) {
      root.addError = root.visibleError || "Bookmark data is read-only"
      return
    }

    var input = BookmarkModel.parsePastedInput(urlField.text, titleField.text)
    var values = {
      url: input.url,
      title: input.title,
      tags: tagsField.text,
      favicon: root.formFavicon
    }

    if (root.mode === "add") {
      var created = BookmarkModel.createBookmark(values, root.bookmarks)
      if (!created.ok) {
        if (created.error.code === "duplicate-url") {
          root.discardUnreferencedFormFavicon()
          root.recordRecent(created.existing.id)
          root.mode = "search"
          root.query = created.existing.title
          root.rebuildDisplay(false)
          root.selectBookmark(created.existing.id)
          root.addNotice = "Bookmark already exists — moved to recent"
          Qt.callLater(function() { keyCatcher.forceActiveFocus() })
        } else {
          root.addError = root.mutationError(created.error)
          urlField.forceActiveFocus()
        }
        return
      }

      var appended = BookmarkModel.appendBookmark(root.bookmarkParse, created.bookmark)
      if (!appended.ok) {
        root.addError = root.mutationError(appended.error)
        return
      }
      root.stageBookmarkMutation(appended.parseResult, created.bookmark.id, "add", "")
      return
    }

    var updated = BookmarkModel.updateBookmark(root.bookmarkParse, root.editingBookmarkId, values)
    if (!updated.ok) {
      root.addError = root.mutationError(updated.error)
      if (updated.error.code === "invalid-url" || updated.error.code === "duplicate-url") {
        urlField.forceActiveFocus()
      }
      return
    }
    var oldFavicon = root.editingOriginalFavicon
    var cleanup = oldFavicon && oldFavicon !== root.formFavicon ? oldFavicon : ""
    root.stageBookmarkMutation(updated.parseResult, updated.bookmark.id, "edit", cleanup)
  }

  function submitDelete() {
    if (root.mode !== "edit" || root.formBusy) return
    root.addError = ""
    var deleted = BookmarkModel.deleteBookmark(root.bookmarkParse, root.editingBookmarkId)
    if (!deleted.ok) {
      root.deleteConfirmationVisible = false
      root.addError = root.mutationError(deleted.error)
      return
    }
    var removedFavicon = BookmarkModel.normalizeFavicon(String(deleted.removed.favicon || ""))
    root.replaceFormFavicon(removedFavicon)
    root.stageBookmarkMutation(
      deleted.parseResult,
      deleted.removed.id,
      "delete",
      removedFavicon
    )
  }

  function completeBookmarkSave() {
    bookmarksMode.running = true
    if (!root.bookmarkSavePending || !root.pendingBookmarkParse) return

    var completedId = root.pendingBookmarkId
    var mutation = root.pendingMutation
    var cleanupFavicon = root.pendingCleanupFavicon
    root.bookmarkParse = root.pendingBookmarkParse
    root.bookmarks = root.bookmarkParse.document.bookmarks
    root.searchIndex = BookmarkModel.buildSearchIndex(root.bookmarks)
    root.pendingBookmarkParse = null
    root.pendingBookmarkId = ""
    root.pendingMutation = ""
    root.pendingCleanupFavicon = ""
    root.bookmarkSavePending = false
    root.deleteConfirmationVisible = false
    root.setFormFields("", "", [], "", "empty")
    root.editingBookmarkId = ""
    root.editingOriginalUrl = ""
    root.editingOriginalFavicon = ""
    root.mode = "search"
    root.query = ""
    root.addNotice = mutation === "add"
        ? "Bookmark added"
        : mutation === "edit" ? "Bookmark updated" : "Bookmark deleted"
    if (mutation === "delete") root.removeRecentBookmark(completedId)
    else root.rebuildDisplay(false)
    if (mutation !== "delete") root.selectBookmark(completedId)
    root.cleanupFaviconIfUnused(cleanupFavicon)
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function failBookmarkSave() {
    root.lastBookmarksPayload = ""
    root.pendingBookmarkParse = null
    root.pendingBookmarkId = ""
    root.pendingMutation = ""
    root.pendingCleanupFavicon = ""
    root.bookmarkSavePending = false
    root.deleteConfirmationVisible = false
    root.addError = "Could not save bookmark data"
    if (root.formVisible) Qt.callLater(function() { urlField.forceActiveFocus() })
  }

  function stateMessage() {
    if (root.visibleError) return root.visibleError
    if (root.loading) return "Loading bookmarks…"
    if (root.query && displayModel.count === 0) return "No matching bookmarks"
    if (displayModel.count === 0) return "No bookmarks yet"
    return ""
  }

  Component.onCompleted: {
    bookmarksBootstrap.running = true
    recentBootstrap.running = true
  }

  ListModel { id: displayModel }

  PointerMoveGate {
    id: pointerGate
    referenceItem: card
  }
  Timer {
    id: urlInputDebounce
    interval: 300
    repeat: false
    onTriggered: root.processUrlInput()
  }

  Timer {
    id: metadataDebounce
    interval: 350
    repeat: false
    onTriggered: root.startWantedMetadata()
  }

  Timer {
    id: metadataExitFallback
    interval: 50
    repeat: false
    onTriggered: metadataProcess.finalize()
  }

  Timer {
    id: metadataWatchdog
    interval: 7000
    repeat: false
    onTriggered: {
      if (metadataProcess.running) metadataProcess.running = false
      metadataProcess.finalize()
    }
  }

  Process {
    id: metadataProcess
    running: false
    command: []

    property string requestedUrl: ""
    property bool receivedOutput: false
    property bool hasExited: false
    property bool finalized: false

    function finalize() {
      if (finalized) return
      finalized = true
      metadataExitFallback.stop()
      metadataWatchdog.stop()
      if (!receivedOutput && root.formVisible
          && root.metadataWantedUrl === requestedUrl) {
        root.metadataPending = false
        root.metadataStatus = "Page details unavailable — the URL host will be used if title is empty"
      }
      root.finishMetadataProcess(requestedUrl)
    }

    onRunningChanged: {
      if (running) {
        receivedOutput = false
        hasExited = false
        finalized = false
        metadataWatchdog.restart()
      } else {
        metadataWatchdog.stop()
        if (requestedUrl && !finalized && !hasExited) metadataExitFallback.restart()
      }
    }

    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        metadataProcess.receivedOutput = true
        root.applyMetadataOutput(metadataProcess.requestedUrl, text)
        if (metadataProcess.hasExited) metadataProcess.finalize()
      }
    }
    stderr: StdioCollector { waitForEnd: true }

    onExited: function(exitCode) {
      hasExited = true
      if (receivedOutput || exitCode !== 0) finalize()
      else metadataExitFallback.restart()
    }
  }

  Process {
    id: bookmarksBootstrap
    command: ["sh", "-c", root.bootstrapScript, "bookmarks-bootstrap", root.dataDir, root.bookmarksPath, BookmarkModel.serializeBookmarks(BookmarkModel.parseBookmarks(null))]
    onExited: function(exitCode) {
      if (exitCode === 0) root.bookmarksReady = true
      else root.storageError = "Could not initialize private bookmark storage"
    }
  }

  Process {
    id: recentBootstrap
    command: ["sh", "-c", root.bootstrapScript, "recent-bootstrap", root.stateDir, root.recentPath, BookmarkModel.serializeRecent(BookmarkModel.parseRecent(null))]
    onExited: function(exitCode) {
      if (exitCode === 0) {
        root.recentReady = true
      } else {
        root.recentWarning = "Recent state is unavailable — bookmarks still work"
        root.failRecentLoad()
      }
    }
  }

  Process {
    id: bookmarksMode
    command: ["chmod", "0600", root.bookmarksPath]
    onExited: function(exitCode) {
      if (exitCode !== 0) root.storageError = "Could not preserve private bookmark permissions"
    }
  }

  Process {
    id: recentMode
    command: ["chmod", "0600", root.recentPath]
    onExited: function(exitCode) {
      if (exitCode !== 0) {
        root.recentReady = false
        root.recentWarning = "Recent state permissions could not be preserved"
      }
    }
  }

  FileView {
    id: bookmarksFile
    path: root.bookmarksReady ? root.bookmarksPath : ""
    watchChanges: true
    atomicWrites: true
    printErrors: false
    onLoaded: root.loadBookmarks(text())
    onLoadFailed: root.failBookmarksLoad()
    onFileChanged: reload()
    onSaved: {
      root.lastBookmarksPayload = ""
      root.completeBookmarkSave()
    }
    onSaveFailed: root.failBookmarkSave()
  }

  FileView {
    id: recentFile
    path: root.recentReady ? root.recentPath : ""
    watchChanges: true
    atomicWrites: true
    printErrors: false
    onLoaded: root.loadRecent(text())
    onLoadFailed: root.failRecentLoad()
    onFileChanged: reload()
    onSaved: {
      root.lastRecentPayload = ""
      recentMode.running = true
    }
    onSaveFailed: {
      root.lastRecentPayload = ""
      root.recentWarning = "Recent state could not be saved"
    }
  }

  PanelWindow {
    id: panel
    visible: root.opened
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "idr4n-bookmarks"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive

    Rectangle {
      anchors.fill: parent
      color: root.scrim
    }

    MouseArea {
      anchors.fill: parent
      onClicked: root.close()
    }

    BorderSurface {
      id: card
      width: root.cardWidth
      height: root.cardHeight
      anchors.centerIn: parent
      radius: root.cornerRadius
      color: root.background
      borderSpec: root.borderSpec
      padding: root.contentMargin

      MouseArea { anchors.fill: parent; onClicked: {} }

      Item {
        id: keyCatcher
        anchors.fill: parent
        focus: true
        Keys.priority: Keys.BeforeItem
        Keys.onPressed: function(event) {
          if (root.mode !== "search") return

          var ctrl = (event.modifiers & Qt.ControlModifier) !== 0

          if (event.key === Qt.Key_Escape) {
            if (root.query) root.setQuery("")
            else root.close()
            event.accepted = true
          } else if (event.key === Qt.Key_Up || (ctrl && event.key === Qt.Key_K)) {
            root.select(-1)
            event.accepted = true
          } else if (event.key === Qt.Key_Down || (ctrl && event.key === Qt.Key_J)) {
            root.select(1)
            event.accepted = true
          } else if (ctrl && event.key === Qt.Key_E) {
            root.enterEditSelected()
            event.accepted = true
          } else if (ctrl && (event.key === Qt.Key_Return || event.key === Qt.Key_Enter)) {
            root.enterAdd(true)
            event.accepted = true
          } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            root.openIndex(root.selectedIndex)
            event.accepted = true
          } else if (Util.editsFilter(event, root.query)) {
            root.setQuery(Util.editedFilter(event, root.query))
            event.accepted = true
          } else if (event.text && event.text.length === 1 && event.text.charCodeAt(0) >= 32 && event.text.charCodeAt(0) !== 127) {
            root.setQuery(root.query + event.text)
            event.accepted = true
          }
        }
      }

      Column {
        visible: root.mode === "search"
        anchors.fill: parent
        anchors.topMargin: card.contentTopInset
        anchors.rightMargin: card.contentRightInset
        anchors.bottomMargin: card.contentBottomInset
        anchors.leftMargin: card.contentLeftInset
        spacing: root.contentSpacing

        Row {
          width: parent.width
          height: root.headerHeight
          spacing: Style.space(12)

          Text {
            width: parent.width - resultCount.width - parent.spacing
            anchors.verticalCenter: parent.verticalCenter
            text: root.query || "Search bookmarks…"
            color: root.foreground
            opacity: root.query ? 1 : 0.58
            font.family: root.fontFamily
            font.pixelSize: Style.font.heading
            elide: Text.ElideRight
          }

          Text {
            id: resultCount
            anchors.verticalCenter: parent.verticalCenter
            text: root.loading || root.visibleError ? "" : String(displayModel.count)
            color: root.foreground
            opacity: 0.52
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }

        Item {
          width: parent.width
          height: parent.height - root.headerHeight - root.footerHeight - root.contentSpacing * 2

          Text {
            anchors.centerIn: parent
            width: parent.width - Style.space(48)
            visible: root.stateMessage().length > 0
            text: root.stateMessage()
            color: root.visibleError ? Color.urgent : root.foreground
            opacity: root.visibleError ? 1 : 0.62
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          ListView {
            id: resultList
            anchors.fill: parent
            visible: root.stateMessage().length === 0
            model: displayModel
            clip: true
            spacing: Style.space(4)
            boundsBehavior: Flickable.StopAtBounds

            delegate: Rectangle {
              id: row
              required property int index
              required property string bookmarkId
              required property string title
              required property string url
              required property string domain
              required property string tagsText
              required property string favicon

              readonly property bool selected: root.cursorActive && index === root.selectedIndex

              width: ListView.view.width
              height: root.rowHeight
              radius: root.cornerRadius
              color: selected ? root.selectedBackground : "transparent"

              Image {
                id: faviconImage
                width: Math.max(Style.space(32), root.rowHeight - Style.space(24))
                height: width
                anchors.left: parent.left
                anchors.leftMargin: Style.space(14)
                anchors.verticalCenter: parent.verticalCenter
                source: root.faviconSource(row.favicon)
                visible: row.favicon.length > 0
                asynchronous: true
                cache: true
                fillMode: Image.PreserveAspectFit
                smooth: true
                mipmap: true
                sourceSize.width: width
                sourceSize.height: height
              }

              Column {
                anchors.top: parent.top
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.left: faviconImage.visible ? faviconImage.right : parent.left
                anchors.leftMargin: faviconImage.visible ? Style.space(10) : Style.space(14)
                anchors.rightMargin: Style.space(14)
                anchors.topMargin: Style.space(8)
                anchors.bottomMargin: Style.space(8)
                spacing: Style.space(2)

                Text {
                  width: parent.width
                  text: row.title
                  textFormat: Text.PlainText
                  color: row.selected ? root.selectedText : root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                  elide: Text.ElideRight
                }

                Text {
                  width: parent.width
                  text: row.domain + (row.tagsText ? "    " + row.tagsText : "")
                  textFormat: Text.PlainText
                  color: row.selected ? root.selectedText : root.foreground
                  opacity: 0.66
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }

                Text {
                  width: parent.width
                  text: row.url
                  textFormat: Text.PlainText
                  color: row.selected ? root.selectedText : root.foreground
                  opacity: 0.48
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideMiddle
                }
              }

              MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onPositionChanged: function(mouse) { root.selectFromPointer(row.index, row, mouse) }
                onClicked: {
                  root.cursorActive = true
                  root.selectedIndex = row.index
                  root.openIndex(row.index)
                }
              }
            }
          }
        }

        Text {
          width: parent.width
          height: root.footerHeight
          text: root.recentWarning || root.addNotice || "↑/↓ or Ctrl+J/K navigate    Enter open    Ctrl+E edit    Ctrl+Enter add    Esc clear/close"
          color: root.foreground
          opacity: 0.46
          horizontalAlignment: Text.AlignHCenter
          verticalAlignment: Text.AlignVCenter
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      Column {
        id: formView
        visible: root.formVisible
        anchors.fill: parent
        anchors.topMargin: card.contentTopInset
        anchors.rightMargin: card.contentRightInset
        anchors.bottomMargin: card.contentBottomInset
        anchors.leftMargin: card.contentLeftInset
        spacing: Style.space(10)

        Text {
          width: parent.width
          text: root.mode === "edit" ? "Edit bookmark" : "Add bookmark"
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.heading
          font.bold: true
        }

        Text {
          width: parent.width
          text: root.mode === "edit"
              ? "Change title, URL, or tags. Changing the URL refreshes page details."
              : "Paste text containing an HTTP(S) URL. Surrounding text becomes the title; URL-only input fetches page details."
          color: root.foreground
          opacity: 0.58
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.Wrap
        }

        Text {
          text: "URL"
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }

        TextField {
          id: urlField
          width: parent.width
          foreground: root.foreground
          placeholderText: "https://example.com/"
          enabled: !root.bookmarkSavePending
          KeyNavigation.tab: titleField
          KeyNavigation.backtab: saveButton
          Keys.onEscapePressed: root.leaveForm()
          Keys.onPressed: function(event) { root.handleFormShortcut(event) }
          onTextChanged: root.handleUrlChanged()
          onAccepted: titleField.forceActiveFocus()
        }

        Text {
          text: "Title (optional)"
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }

        TextField {
          id: titleField
          width: parent.width
          foreground: root.foreground
          placeholderText: "Falls back to the URL host"
          enabled: !root.bookmarkSavePending
          KeyNavigation.tab: tagsField
          KeyNavigation.backtab: urlField
          Keys.onEscapePressed: root.leaveForm()
          Keys.onPressed: function(event) { root.handleFormShortcut(event) }
          onTextChanged: root.handleTitleChanged()
          onAccepted: tagsField.forceActiveFocus()
        }

        Text {
          text: "Tags (optional, comma-separated)"
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }

        TextField {
          id: tagsField
          width: parent.width
          foreground: root.foreground
          placeholderText: "reference, linux"
          enabled: !root.bookmarkSavePending
          KeyNavigation.tab: root.mode === "edit" ? deleteButton : cancelButton
          KeyNavigation.backtab: titleField
          Keys.onEscapePressed: root.leaveForm()
          Keys.onPressed: function(event) { root.handleFormShortcut(event) }
          onTextChanged: root.addError = ""
          onAccepted: root.submitForm()
        }

        Row {
          width: parent.width
          height: Style.space(42)
          spacing: Style.space(10)

          Image {
            width: Style.space(32)
            height: width
            anchors.verticalCenter: parent.verticalCenter
            source: root.faviconSource(root.formFavicon)
            visible: root.formFavicon.length > 0
            asynchronous: true
            fillMode: Image.PreserveAspectFit
            smooth: true
            mipmap: true
            sourceSize.width: width
            sourceSize.height: height
          }

          Text {
            width: parent.width - (root.formFavicon ? Style.space(42) : 0)
            anchors.verticalCenter: parent.verticalCenter
            text: root.addError || root.metadataStatus
            color: root.addError ? Color.urgent : root.foreground
            opacity: root.addError ? 1 : 0.66
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.Wrap
          }
        }

        Row {
          width: parent.width
          spacing: Style.space(10)

          Item {
            width: parent.width - saveButton.width - cancelButton.width
                - (deleteButton.visible ? deleteButton.width : 0)
                - parent.spacing * (deleteButton.visible ? 3 : 2)
            height: 1
          }

          Button {
            id: deleteButton
            visible: root.mode === "edit"
            text: "Delete…"
            foreground: Color.urgent
            focusable: true
            bordered: true
            enabled: !root.formBusy
            KeyNavigation.tab: cancelButton
            KeyNavigation.backtab: tagsField
            Keys.onEscapePressed: root.leaveForm()
            Keys.onPressed: function(event) { root.handleFormShortcut(event) }
            onClicked: {
              root.deleteConfirmationVisible = true
              Qt.callLater(function() { keepButton.forceActiveFocus() })
            }
          }

          Button {
            id: cancelButton
            text: "Cancel"
            foreground: root.foreground
            focusable: true
            bordered: true
            enabled: !root.bookmarkSavePending
            KeyNavigation.tab: saveButton
            KeyNavigation.backtab: root.mode === "edit" ? deleteButton : tagsField
            Keys.onEscapePressed: root.leaveForm()
            Keys.onPressed: function(event) { root.handleFormShortcut(event) }
            onClicked: root.leaveForm()
          }

          Button {
            id: saveButton
            text: root.bookmarkSavePending ? "Saving…" : "Save"
            foreground: root.foreground
            focusable: true
            bordered: true
            enabled: !root.formBusy
            KeyNavigation.tab: urlField
            KeyNavigation.backtab: cancelButton
            Keys.onEscapePressed: root.leaveForm()
            Keys.onPressed: function(event) { root.handleFormShortcut(event) }
            onClicked: root.submitForm()
          }
        }

        Item { width: 1; height: Style.space(8) }

        Text {
          width: parent.width
          text: root.mode === "edit"
              ? "Enter advances fields    Ctrl+Enter saves    Delete asks for confirmation    Esc returns"
              : "Enter advances fields    Ctrl+Enter saves    Esc returns to search"
          color: root.foreground
          opacity: 0.46
          horizontalAlignment: Text.AlignHCenter
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      Rectangle {
        anchors.fill: parent
        visible: root.deleteConfirmationVisible
        z: 20
        color: root.background
        radius: root.cornerRadius

        MouseArea { anchors.fill: parent; onClicked: {} }

        Column {
          width: Math.min(parent.width - Style.space(64), Style.space(560))
          anchors.centerIn: parent
          spacing: Style.space(18)

          Text {
            width: parent.width
            text: "Delete “" + root.editingBookmarkTitle() + "”?"
            textFormat: Text.PlainText
            color: root.foreground
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            font.family: root.fontFamily
            font.pixelSize: Style.font.heading
            font.bold: true
          }

          Text {
            width: parent.width
            text: "This removes the bookmark from this device. This action cannot be undone."
            color: root.foreground
            opacity: 0.66
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          Row {
            width: parent.width
            spacing: Style.space(10)

            Item { width: parent.width - keepButton.width - confirmDeleteButton.width - parent.spacing; height: 1 }

            Button {
              id: keepButton
              text: "Keep bookmark"
              foreground: root.foreground
              focusable: true
              bordered: true
              enabled: !root.bookmarkSavePending
              KeyNavigation.tab: confirmDeleteButton
              KeyNavigation.backtab: confirmDeleteButton
              Keys.onEscapePressed: root.leaveForm()
              onClicked: {
                root.deleteConfirmationVisible = false
                Qt.callLater(function() { deleteButton.forceActiveFocus() })
              }
            }

            Button {
              id: confirmDeleteButton
              text: root.bookmarkSavePending ? "Deleting…" : "Delete"
              foreground: Color.urgent
              focusable: true
              bordered: true
              enabled: !root.bookmarkSavePending
              KeyNavigation.tab: keepButton
              KeyNavigation.backtab: keepButton
              Keys.onEscapePressed: root.leaveForm()
              onClicked: root.submitDelete()
            }
          }
        }
      }
    }
  }
}
