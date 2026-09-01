var BOOKMARK_SCHEMA_VERSION = 1
var RECENT_LIMIT = 10
var DISPLAY_LIMIT = 60
var BOOKMARK_FILE_LIMIT = 1024 * 1024
var RECENT_FILE_LIMIT = 64 * 1024
var BOOKMARK_LIMIT = 5000
var ID_LIMIT = 128
var TITLE_LIMIT = 512
var URL_LIMIT = 2048
var TAG_LIMIT = 32
var TAG_LENGTH_LIMIT = 64
var CREATED_AT_LIMIT = 64
var JSON_DEPTH_LIMIT = 32

function unicodeLength(value) {
  var text = String(value || "")
  var length = 0
  for (var i = 0; i < text.length; i++) {
    var code = text.charCodeAt(i)
    if (code >= 0xd800 && code <= 0xdbff && i + 1 < text.length) {
      var next = text.charCodeAt(i + 1)
      if (next >= 0xdc00 && next <= 0xdfff) i++
    }
    length++
  }
  return length
}

function utf8ByteLength(value, stopAfter) {
  var text = String(value || "")
  if (!/[^\x00-\x7f]/.test(text)) return text.length

  var bytes = 0
  var cap = typeof stopAfter === "number" ? stopAfter : 9007199254740991
  for (var i = 0; i < text.length; i++) {
    var code = text.charCodeAt(i)
    if (code <= 0x7f) bytes++
    else if (code <= 0x7ff) bytes += 2
    else if (code >= 0xd800 && code <= 0xdbff && i + 1 < text.length) {
      var next = text.charCodeAt(i + 1)
      if (next >= 0xdc00 && next <= 0xdfff) {
        bytes += 4
        i++
      } else {
        bytes += 3
      }
    } else {
      bytes += 3
    }
    if (bytes > cap) return bytes
  }
  return bytes
}

function valueDepthWithinLimit(value, limit, depth) {
  if (value === null || typeof value !== "object") return true
  var currentDepth = typeof depth === "number" ? depth : 1
  if (currentDepth > limit) return false

  if (Array.isArray(value)) {
    for (var index = 0; index < value.length; index++) {
      if (!valueDepthWithinLimit(value[index], limit, currentDepth + 1)) return false
    }
    return true
  }

  for (var key in value) {
    if (hasOwn(value, key)
        && !valueDepthWithinLimit(value[key], limit, currentDepth + 1)) {
      return false
    }
  }
  return true
}

function hasUrlControl(value) {
  var text = String(value || "")
  for (var i = 0; i < text.length; i++) {
    var code = text.charCodeAt(i)
    if (code <= 31 || (code >= 127 && code <= 159) || code === 0xfeff) return true
  }
  return false
}

function hasTextControl(value) {
  var text = String(value || "")
  for (var i = 0; i < text.length; i++) {
    var code = text.charCodeAt(i)
    if (code <= 31 || (code >= 127 && code <= 159)) return true
  }
  return false
}

function hasOwn(value, key) {
  return Object.prototype.hasOwnProperty.call(value, key)
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

function emptyDocument() {
  return {
    schemaVersion: BOOKMARK_SCHEMA_VERSION,
    bookmarks: []
  }
}

function emptyRecentDocument() {
  return {
    schemaVersion: BOOKMARK_SCHEMA_VERSION,
    recentIds: []
  }
}

function parseFailure(state, code, index, firstIndex) {
  var error = { code: code }
  if (typeof index === "number") error.index = index
  if (typeof firstIndex === "number") error.firstIndex = firstIndex

  return {
    state: state,
    writable: false,
    document: null,
    error: error
  }
}

function parseSuccess(state, document) {
  return {
    state: state,
    writable: true,
    document: document,
    error: null
  }
}

function parseHttpUrl(value) {
  if (typeof value !== "string" || hasUrlControl(value)) return null

  var url = value.trim()
  if (!url || unicodeLength(url) > URL_LIMIT) return null

  var match = /^(https?):\/\/([^\/?#\s\u0000-\u001f\u007f\ufeff]+)([^\s\u0000-\u001f\u007f\ufeff]*)$/i.exec(url)
  if (!match) return null

  var authority = match[2]
  if (!authority || authority.indexOf("\\") !== -1) return null

  var hostPort = authority
  var at = authority.lastIndexOf("@")
  if (at !== -1) {
    if (at === authority.length - 1) return null
    hostPort = authority.substring(at + 1)
  }

  var host = ""
  var port = ""
  if (hostPort.charAt(0) === "[") {
    var closingBracket = hostPort.indexOf("]")
    if (closingBracket <= 1) return null

    host = hostPort.substring(1, closingBracket)
    var bracketRemainder = hostPort.substring(closingBracket + 1)
    if (bracketRemainder) {
      if (!/^:\d+$/.test(bracketRemainder)) return null
      port = bracketRemainder.substring(1)
    }
  } else {
    if (hostPort.indexOf("[") !== -1 || hostPort.indexOf("]") !== -1) return null

    var lastColon = hostPort.lastIndexOf(":")
    if (lastColon !== -1) {
      if (hostPort.indexOf(":") !== lastColon) return null
      host = hostPort.substring(0, lastColon)
      port = hostPort.substring(lastColon + 1)
      if (!/^\d+$/.test(port)) return null
    } else {
      host = hostPort
    }
  }

  if (!host || /[\/?#@]/.test(host)) return null
  if (port && Number(port) > 65535) return null

  return {
    url: url,
    scheme: match[1],
    authority: authority,
    suffix: match[3],
    host: host
  }
}

function isValidHttpUrl(value) {
  return parseHttpUrl(value) !== null
}

function normalizeUrl(value) {
  var parsed = parseHttpUrl(value)
  return parsed ? parsed.url : ""
}

function duplicateKey(value) {
  var parsed = parseHttpUrl(value)
  if (!parsed) return null

  return parsed.scheme.toLowerCase() + "://" + parsed.authority.toLowerCase() + parsed.suffix
}

function urlHost(value) {
  var parsed = parseHttpUrl(value)
  return parsed ? parsed.host.toLowerCase() : ""
}

function normalizeTitle(value, url) {
  var title = typeof value === "string" ? value.trim() : ""
  return title || urlHost(url)
}

function isCanonicalTag(value) {
  return typeof value === "string"
      && value.length > 0
      && value.charAt(0) !== "#"
      && value === value.trim()
      && value === value.toLowerCase()
      && unicodeLength(value) <= TAG_LENGTH_LIMIT
      && !hasTextControl(value)
}

function normalizeTags(value, strict) {
  var values
  if (Array.isArray(value)) values = value
  else if (typeof value === "string") values = value.split(",")
  else values = []

  if (Array.isArray(value) && value.length <= TAG_LIMIT) {
    var canonical = true
    for (var canonicalIndex = 0; canonicalIndex < value.length && canonical; canonicalIndex++) {
      var canonicalTag = value[canonicalIndex]
      if (!isCanonicalTag(canonicalTag)) {
        canonical = false
        break
      }
      for (var earlierIndex = 0; earlierIndex < canonicalIndex; earlierIndex++) {
        if (value[earlierIndex] === canonicalTag) {
          canonical = false
          break
        }
      }
    }
    if (canonical) return value
  }

  var normalized = []
  for (var i = 0; i < values.length; i++) {
    if (typeof values[i] !== "string") {
      if (strict) return null
      continue
    }
    if (hasTextControl(values[i])) return null

    var tag = values[i].trim()
    if (tag.charAt(0) === "#") tag = tag.substring(1).trim()
    tag = tag.toLowerCase()
    if (!tag) continue
    if (hasTextControl(tag) || unicodeLength(tag) > TAG_LENGTH_LIMIT) return null

    var duplicate = false
    for (var j = 0; j < normalized.length; j++) {
      if (normalized[j] === tag) {
        duplicate = true
        break
      }
    }
    if (!duplicate) normalized.push(tag)
    if (normalized.length > TAG_LIMIT) return null
  }

  return normalized
}
function normalizeFavicon(value) {
  if (typeof value !== "string") return ""
  var path = value.trim()
  if (path !== value) return ""
  if (/^favicons-v2\/[0-9a-f]{64}\.png$/.test(path)) return path
  return /^favicons\/[0-9a-f]{64}\.(?:gif|ico|jpg|png|webp)$/.test(path) ? path : ""
}

function normalizeSafeFavicon(value) {
  var path = normalizeFavicon(value)
  return /^favicons-v2\/[0-9a-f]{64}\.png$/.test(path) ? path : ""
}

function isValidCreatedAt(value) {
  return typeof value === "string"
      && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
}

function normalizeStoredBookmark(value, index) {
  if (!isObject(value)) return { error: parseFailure("malformed", "invalid-bookmark", index).error }

  if (typeof value.id !== "string") {
    return { error: parseFailure("malformed", "invalid-id", index).error }
  }
  var id = value.id.trim()
  if (!id
      || id !== value.id
      || unicodeLength(id) > ID_LIMIT
      || hasTextControl(id)) {
    return { error: parseFailure("malformed", "invalid-id", index).error }
  }
  if (typeof value.title !== "string"
      || unicodeLength(value.title) > TITLE_LIMIT
      || hasTextControl(value.title)) {
    return { error: parseFailure("malformed", "invalid-title", index).error }
  }

  var parsedUrl = typeof value.url === "string" ? parseHttpUrl(value.url) : null
  if (!parsedUrl || parsedUrl.url !== value.url) {
    return { error: parseFailure("malformed", "invalid-url", index).error }
  }
  if (!Array.isArray(value.tags) || value.tags.length > TAG_LIMIT) {
    return { error: parseFailure("malformed", "invalid-tags", index).error }
  }
  var tags = normalizeTags(value.tags, true)
  if (tags === null) {
    return { error: parseFailure("malformed", "invalid-tags", index).error }
  }
  if (hasOwn(value, "favicon")) {
    if (value.favicon === null) delete value.favicon
    else if (!normalizeFavicon(value.favicon)) {
      return { error: parseFailure("malformed", "invalid-favicon", index).error }
    }
  }
  if (typeof value.createdAt !== "string"
      || unicodeLength(value.createdAt) > CREATED_AT_LIMIT
      || hasTextControl(value.createdAt)
      || !isValidCreatedAt(value.createdAt)) {
    return { error: parseFailure("malformed", "invalid-created-at", index).error }
  }

  value.title = value.title.trim() || parsedUrl.host.toLowerCase()
  if (unicodeLength(value.title) > TITLE_LIMIT) {
    return { error: parseFailure("malformed", "invalid-title", index).error }
  }
  value.tags = tags
  return parsedUrl.scheme.toLowerCase() + "://" + parsedUrl.authority.toLowerCase() + parsedUrl.suffix
}

function validateDocument(document) {
  if (!isObject(document)) return parseFailure("malformed", "invalid-top-level")
  if (!hasOwn(document, "schemaVersion")) return parseFailure("malformed", "missing-schema-version")
  if (document.schemaVersion !== BOOKMARK_SCHEMA_VERSION) {
    return parseFailure("unsupported", "unsupported-schema")
  }
  if (!Array.isArray(document.bookmarks)) return parseFailure("malformed", "invalid-bookmarks")
  if (document.bookmarks.length > BOOKMARK_LIMIT) {
    return parseFailure("malformed", "too-many-bookmarks")
  }

  var seenIds = {}
  var seenUrls = {}
  for (var i = 0; i < document.bookmarks.length; i++) {
    var bookmark = document.bookmarks[i]
    var normalized = normalizeStoredBookmark(bookmark, i)
    if (typeof normalized !== "string") {
      return parseFailure("malformed", normalized.error.code, normalized.error.index)
    }

    var idKey = ":" + bookmark.id
    var previousId = seenIds[idKey]
    if (previousId !== undefined) {
      return parseFailure("malformed", "duplicate-id", i, previousId)
    }
    seenIds[idKey] = i

    var urlKey = ":" + normalized
    var previousUrl = seenUrls[urlKey]
    if (previousUrl !== undefined) {
      return parseFailure("malformed", "duplicate-url", i, previousUrl)
    }
    seenUrls[urlKey] = i
  }

  return parseSuccess("valid", document)
}

function parseBookmarks(rawText, byteLengthKnownBounded) {
  if (rawText === undefined || rawText === null) {
    return parseSuccess("missing", emptyDocument())
  }
  if (typeof rawText !== "string") return parseFailure("malformed", "invalid-json")
  if (rawText.length > BOOKMARK_FILE_LIMIT
      || (!byteLengthKnownBounded
          && utf8ByteLength(rawText, BOOKMARK_FILE_LIMIT) > BOOKMARK_FILE_LIMIT)) {
    return parseFailure("malformed", "file-too-large")
  }

  var document
  try {
    document = JSON.parse(rawText)
  } catch (error) {
    return parseFailure("malformed", "invalid-json")
  }
  if (!valueDepthWithinLimit(document, JSON_DEPTH_LIMIT)) {
    return parseFailure("malformed", "too-deep")
  }

  return validateDocument(document)
}

function serializeBookmarks(parseResult) {
  if (!parseResult
      || parseResult.writable !== true
      || (parseResult.state !== "missing" && parseResult.state !== "valid")) {
    throw new Error("bookmark document is not writable")
  }

  var raw
  try {
    raw = JSON.stringify(parseResult.document)
  } catch (error) {
    throw new Error("bookmark document is not serializable")
  }

  var validated = parseBookmarks(raw, true)
  if (validated.state !== "valid") {
    throw new Error("bookmark document is invalid: " + validated.error.code)
  }

  var payload = JSON.stringify(validated.document, null, 2) + "\n"
  if (utf8ByteLength(payload, BOOKMARK_FILE_LIMIT) > BOOKMARK_FILE_LIMIT) {
    throw new Error("bookmark document is invalid: file-too-large")
  }
  return payload
}

function existingIdMap(values) {
  var seen = {}
  var entries = Array.isArray(values) ? values : []
  for (var i = 0; i < entries.length; i++) {
    var id = typeof entries[i] === "string"
        ? entries[i]
        : isObject(entries[i]) && typeof entries[i].id === "string" ? entries[i].id : ""
    if (id) seen[":" + id] = true
  }
  return seen
}

function generateId(existingIds, nowMilliseconds, randomFunction) {
  var seen = existingIdMap(existingIds)
  var now = typeof nowMilliseconds === "number" && isFinite(nowMilliseconds)
      ? Math.floor(nowMilliseconds)
      : Date.now()
  var random = typeof randomFunction === "function" ? randomFunction() : Math.random()
  if (typeof random !== "number" || !isFinite(random) || random < 0 || random >= 1) {
    random = Math.random()
  }

  var randomPart = Math.floor(random * 4294967296).toString(36)
  var base = "bkm_" + now.toString(36) + "_" + randomPart
  var candidate = base
  var suffix = 1
  while (seen[":" + candidate]) {
    candidate = base + "_" + suffix.toString(36)
    suffix++
  }
  return candidate
}

function createBookmark(input, existingBookmarks, nowMilliseconds, randomFunction) {
  if (!isObject(input)) return { ok: false, error: { code: "invalid-input" } }

  var url = normalizeUrl(input.url)
  if (!url) return { ok: false, error: { code: "invalid-url" } }

  var existing = Array.isArray(existingBookmarks) ? existingBookmarks : []
  if (existing.length >= BOOKMARK_LIMIT) {
    return { ok: false, error: { code: "too-many-bookmarks" } }
  }
  var key = duplicateKey(url)
  for (var i = 0; i < existing.length; i++) {
    if (duplicateKey(existing[i].url) === key) {
      return {
        ok: false,
        error: { code: "duplicate-url", index: i },
        existing: existing[i]
      }
    }
  }

  var title = normalizeTitle(input.title, url)
  if (unicodeLength(title) > TITLE_LIMIT || hasTextControl(title)) {
    return { ok: false, error: { code: "invalid-title" } }
  }
  var tags = normalizeTags(input.tags)
  if (tags === null) return { ok: false, error: { code: "invalid-tags" } }

  var now = typeof nowMilliseconds === "number" && isFinite(nowMilliseconds)
      ? nowMilliseconds
      : Date.now()
  var bookmark = {
    id: generateId(existing, now, randomFunction),
    title: title,
    url: url,
    tags: tags,
    createdAt: new Date(now).toISOString()
  }
  if (hasOwn(input, "favicon")) {
    var favicon = normalizeFavicon(input.favicon)
    if (input.favicon && !favicon) return { ok: false, error: { code: "invalid-favicon" } }
    if (favicon) bookmark.favicon = favicon
  }

  return { ok: true, bookmark: bookmark }
}

function trimPastedUrl(value) {
  var result = value
  while (result) {
    var trailing = result.charAt(result.length - 1)
    if (trailing === "." || trailing === ",") {
      result = result.substring(0, result.length - 1)
      continue
    }

    var opening = trailing === ")" ? "(" : trailing === "]" ? "[" : trailing === "}" ? "{" : ""
    if (!opening) break
    var openingCount = result.split(opening).length - 1
    var closingCount = result.split(trailing).length - 1
    if (closingCount <= openingCount) break
    result = result.substring(0, result.length - 1)
  }
  return result
}

function cleanPastedTitlePart(value) {
  var result = String(value || "").replace(/\s+/g, " ").trim()
  result = result.replace(/^[\s|:;,.\-–—()[\]{}]+/, "")
  return result.replace(/[\s|:;,.\-–—()[\]{}]+$/, "")
}

function parsePastedInput(urlValue, titleValue) {
  var raw = typeof urlValue === "string" ? urlValue : ""
  var explicitTitle = typeof titleValue === "string" ? titleValue.trim() : ""
  if (hasUrlControl(raw)) return { url: raw, title: explicitTitle }
  var directUrl = normalizeUrl(raw)
  if (directUrl) return { url: directUrl, title: explicitTitle }
  var matcher = /https?:\/\/[^\s|<>"']+/ig
  var match
  while ((match = matcher.exec(raw)) !== null) {
    var candidate = trimPastedUrl(match[0])
    var url = normalizeUrl(candidate)
    if (!url) continue

    var title = explicitTitle
    if (!title) {
      var before = cleanPastedTitlePart(raw.substring(0, match.index))
      var after = cleanPastedTitlePart(raw.substring(match.index + match[0].length))
      title = before && after ? before + " " + after : before || after
    }
    return { url: url, title: title }
  }
  return { url: raw, title: explicitTitle }
}

function parseLegacyInput(urlValue, titleValue) {
  return parsePastedInput(urlValue, titleValue)
}

function snapshotActivation(value) {
  var row = value || {}
  return {
    bookmarkId: String(row.bookmarkId || ""),
    url: String(row.url || "")
  }
}


function appendBookmark(parseResult, bookmark) {
  if (!parseResult
      || parseResult.writable !== true
      || (parseResult.state !== "missing" && parseResult.state !== "valid")) {
    return { ok: false, error: { code: "read-only" } }
  }

  var document
  try {
    document = JSON.parse(JSON.stringify(parseResult.document))
  } catch (error) {
    return { ok: false, error: { code: "not-serializable" } }
  }
  document.bookmarks.push(bookmark)

  var validated = validateDocument(document)
  if (validated.state !== "valid") {
    return { ok: false, error: validated.error }
  }
  return { ok: true, parseResult: validated }
}
function updateBookmark(parseResult, bookmarkId, input) {
  if (!parseResult
      || parseResult.writable !== true
      || (parseResult.state !== "missing" && parseResult.state !== "valid")) {
    return { ok: false, error: { code: "read-only" } }
  }

  var document
  try {
    document = JSON.parse(JSON.stringify(parseResult.document))
  } catch (error) {
    return { ok: false, error: { code: "not-serializable" } }
  }

  var index = -1
  for (var i = 0; i < document.bookmarks.length; i++) {
    if (document.bookmarks[i].id === bookmarkId) {
      index = i
      break
    }
  }
  if (index === -1) return { ok: false, error: { code: "not-found" } }
  if (!isObject(input)) return { ok: false, error: { code: "invalid-input" } }

  var url = normalizeUrl(input.url)
  if (!url) return { ok: false, error: { code: "invalid-url" } }
  var key = duplicateKey(url)
  for (var duplicateIndex = 0; duplicateIndex < document.bookmarks.length; duplicateIndex++) {
    if (duplicateIndex !== index && duplicateKey(document.bookmarks[duplicateIndex].url) === key) {
      return {
        ok: false,
        error: { code: "duplicate-url", index: duplicateIndex },
        existing: document.bookmarks[duplicateIndex]
      }
    }
  }

  var updated = document.bookmarks[index]
  var title = normalizeTitle(input.title, url)
  if (unicodeLength(title) > TITLE_LIMIT || hasTextControl(title)) {
    return { ok: false, error: { code: "invalid-title" } }
  }
  var tags = normalizeTags(input.tags)
  if (tags === null) return { ok: false, error: { code: "invalid-tags" } }
  updated.title = title
  updated.url = url
  updated.tags = tags
  if (hasOwn(input, "favicon")) {
    var favicon = normalizeFavicon(input.favicon)
    if (input.favicon && !favicon) return { ok: false, error: { code: "invalid-favicon" } }
    if (favicon) updated.favicon = favicon
    else delete updated.favicon
  }

  var validated = validateDocument(document)
  if (validated.state !== "valid") return { ok: false, error: validated.error }
  return { ok: true, bookmark: validated.document.bookmarks[index], parseResult: validated }
}

function deleteBookmark(parseResult, bookmarkId) {
  if (!parseResult
      || parseResult.writable !== true
      || (parseResult.state !== "missing" && parseResult.state !== "valid")) {
    return { ok: false, error: { code: "read-only" } }
  }

  var document
  try {
    document = JSON.parse(JSON.stringify(parseResult.document))
  } catch (error) {
    return { ok: false, error: { code: "not-serializable" } }
  }

  var index = -1
  for (var i = 0; i < document.bookmarks.length; i++) {
    if (document.bookmarks[i].id === bookmarkId) {
      index = i
      break
    }
  }
  if (index === -1) return { ok: false, error: { code: "not-found" } }

  var removed = document.bookmarks.splice(index, 1)[0]
  var validated = validateDocument(document)
  if (validated.state !== "valid") return { ok: false, error: validated.error }
  return { ok: true, removed: removed, parseResult: validated }
}

function touchRecent(recentIds, id) {
  var values = [String(id || "")]
  var existing = Array.isArray(recentIds) ? recentIds : []
  for (var i = 0; i < existing.length; i++) values.push(existing[i])
  return normalizeRecentIds(values)
}
function removeRecent(recentIds, id) {
  var removedId = String(id || "")
  var values = []
  var existing = Array.isArray(recentIds) ? recentIds : []
  for (var i = 0; i < existing.length; i++) {
    if (String(existing[i] || "").trim() !== removedId) values.push(existing[i])
  }
  return normalizeRecentIds(values)
}

function normalizeRecentIds(value, limit) {
  var cap = RECENT_LIMIT
  if (typeof limit === "number" && isFinite(limit)) {
    cap = Math.max(0, Math.min(RECENT_LIMIT, Math.floor(limit)))
  }

  var values = Array.isArray(value) ? value : []
  var normalized = []
  var seen = {}
  for (var i = 0; i < values.length && normalized.length < cap; i++) {
    if (typeof values[i] !== "string") continue
    if (hasTextControl(values[i])) continue

    var id = values[i].trim()
    if (!id || unicodeLength(id) > ID_LIMIT || hasTextControl(id)) continue
    var key = ":" + id
    if (seen[key]) continue

    seen[key] = true
    normalized.push(id)
  }

  return normalized
}

function resetRecent(code) {
  return {
    state: "reset",
    writable: true,
    document: emptyRecentDocument(),
    error: { code: code }
  }
}

function parseRecent(rawText, byteLengthKnownBounded) {
  if (rawText === undefined || rawText === null) {
    return parseSuccess("missing", emptyRecentDocument())
  }
  if (typeof rawText !== "string") return resetRecent("invalid-json")
  if (rawText.length > RECENT_FILE_LIMIT
      || (!byteLengthKnownBounded
          && utf8ByteLength(rawText, RECENT_FILE_LIMIT) > RECENT_FILE_LIMIT)) {
    return resetRecent("file-too-large")
  }

  var document
  try {
    document = JSON.parse(rawText)
  } catch (error) {
    return resetRecent("invalid-json")
  }
  if (!valueDepthWithinLimit(document, JSON_DEPTH_LIMIT)) return resetRecent("too-deep")

  if (!isObject(document)) return resetRecent("invalid-top-level")
  if (document.schemaVersion !== BOOKMARK_SCHEMA_VERSION) return resetRecent("unsupported-schema")
  if (!Array.isArray(document.recentIds)) return resetRecent("invalid-recent-ids")

  document.recentIds = normalizeRecentIds(document.recentIds)
  return parseSuccess("valid", document)
}

function serializeRecent(parseResult) {
  if (!parseResult || parseResult.writable !== true) {
    throw new Error("recent document is not writable")
  }

  var raw
  try {
    raw = JSON.stringify(parseResult.document)
  } catch (error) {
    throw new Error("recent document is not serializable")
  }

  var validated = parseRecent(raw, true)
  if (validated.state !== "valid") {
    throw new Error("recent document is invalid: " + validated.error.code)
  }

  var payload = JSON.stringify(validated.document, null, 2) + "\n"
  if (utf8ByteLength(payload, RECENT_FILE_LIMIT) > RECENT_FILE_LIMIT) {
    throw new Error("recent document is invalid: file-too-large")
  }
  return payload
}

function searchTerms(value) {
  var raw = String(value || "").toLowerCase().split(/[^0-9a-z_\-\u0080-\uffff]+/)
  var terms = []
  for (var i = 0; i < raw.length; i++) {
    if (raw[i]) terms.push(raw[i])
  }
  return terms
}

function indexedHost(value) {
  var url = String(value || "")
  var start = url.indexOf("://")
  if (start === -1) return ""
  start += 3

  var end = url.length
  for (var i = start; i < url.length; i++) {
    var character = url.charAt(i)
    if (character === "/" || character === "?" || character === "#") {
      end = i
      break
    }
  }

  var authority = url.substring(start, end)
  var at = authority.lastIndexOf("@")
  var hostPort = at === -1 ? authority : authority.substring(at + 1)
  if (hostPort.charAt(0) === "[") {
    var bracket = hostPort.indexOf("]")
    return bracket > 0 ? hostPort.substring(1, bracket).toLowerCase() : ""
  }
  var colon = hostPort.lastIndexOf(":")
  return (colon === -1 ? hostPort : hostPort.substring(0, colon)).toLowerCase()
}

function buildSearchIndex(bookmarks) {
  var values = Array.isArray(bookmarks) ? bookmarks : []
  var searchText = new Array(values.length)
  for (var i = 0; i < values.length; i++) {
    var bookmark = values[i]
    var tags = Array.isArray(bookmark.tags) ? bookmark.tags.join("\n").toLowerCase() : ""
    searchText[i] = bookmark.title.toLowerCase() + "\n"
        + bookmark.url.toLowerCase() + "\n" + tags
  }
  return {
    bookmarks: values,
    searchText: searchText,
    details: new Array(values.length)
  }
}

function queryTokens(query) {
  var raw = String(query || "").toLowerCase().trim().split(/\s+/)
  var tokens = []
  var seen = {}
  for (var i = 0; i < raw.length; i++) {
    if (!raw[i]) continue
    var key = ":" + raw[i]
    if (seen[key]) continue
    seen[key] = true
    tokens.push(raw[i])
  }
  return tokens
}

function rowDetails(index, rowIndex) {
  if (index.details[rowIndex]) return index.details[rowIndex]
  var bookmark = index.bookmarks[rowIndex]
  index.details[rowIndex] = {
    title: String(bookmark.title || "").toLowerCase(),
    tags: Array.isArray(bookmark.tags) ? bookmark.tags.join("\n").toLowerCase() : "",
    domain: indexedHost(bookmark.url),
    url: String(bookmark.url || "").toLowerCase()
  }
  return index.details[rowIndex]
}

function valueScore(text, token, weight, splitTerms) {
  if (text.indexOf(token) === -1) return 0
  if (text === token) return 300 + weight

  if (splitTerms) {
    var terms = searchTerms(text)
    for (var i = 0; i < terms.length; i++) {
      if (terms[i] === token) return 300 + weight
    }
    for (var j = 0; j < terms.length; j++) {
      if (terms[j].indexOf(token) === 0) return 200 + weight
    }
  }
  if (text.indexOf(token) === 0) return 200 + weight
  return 100 + weight
}

function tokenScore(index, rowIndex, token) {
  var details = rowDetails(index, rowIndex)
  return Math.max(
    valueScore(details.title, token, 40, true),
    valueScore(details.tags, token, 35, true),
    valueScore(details.domain, token, 30, true),
    valueScore(details.url, token, 10, false)
  )
}

function scoreRow(index, rowIndex, tokens) {
  for (var i = 0; i < tokens.length; i++) {
    if (index.searchText[rowIndex].indexOf(tokens[i]) === -1) return null
  }

  var total = 0
  var weakest = 3
  for (var j = 0; j < tokens.length; j++) {
    var score = tokenScore(index, rowIndex, tokens[j])
    if (!score) return null
    weakest = Math.min(weakest, Math.floor(score / 100))
    total += score
  }
  return { weakest: weakest, total: total }
}

function resultLimit(value) {
  if (typeof value !== "number" || !isFinite(value)) return DISPLAY_LIMIT
  return Math.max(0, Math.min(DISPLAY_LIMIT, Math.floor(value)))
}

function recencyMap(recentIds) {
  var normalized = normalizeRecentIds(recentIds)
  var ranks = {}
  for (var i = 0; i < normalized.length; i++) ranks[":" + normalized[i]] = i
  return ranks
}

function searchBookmarks(index, query, recentIds, limit) {
  var searchIndex = isObject(index)
      && Array.isArray(index.bookmarks)
      && Array.isArray(index.searchText)
      && Array.isArray(index.details)
      ? index
      : buildSearchIndex([])
  var rows = searchIndex.bookmarks
  var cap = resultLimit(limit)
  if (cap === 0) return []

  var tokens = queryTokens(query)
  var recent = normalizeRecentIds(recentIds)
  if (tokens.length === 0) {
    var byId = {}
    for (var i = 0; i < rows.length; i++) byId[":" + rows[i].id] = i

    var blankResults = []
    var added = {}
    for (var j = 0; j < recent.length && blankResults.length < cap; j++) {
      var recentKey = ":" + recent[j]
      if (!hasOwn(byId, recentKey) || added[recentKey]) continue
      added[recentKey] = true
      blankResults.push(rows[byId[recentKey]])
    }

    for (var k = rows.length - 1; k >= 0 && blankResults.length < cap; k--) {
      var rowKey = ":" + rows[k].id
      if (added[rowKey]) continue
      added[rowKey] = true
      blankResults.push(rows[k])
    }
    return blankResults
  }

  var ranks = recencyMap(recent)
  var matches = []
  for (var rowIndex = 0; rowIndex < rows.length; rowIndex++) {
    var match = scoreRow(searchIndex, rowIndex, tokens)
    if (!match) continue

    var rankKey = ":" + rows[rowIndex].id
    matches.push({
      bookmark: rows[rowIndex],
      insertionIndex: rowIndex,
      weakest: match.weakest,
      total: match.total,
      recentRank: hasOwn(ranks, rankKey) ? ranks[rankKey] : RECENT_LIMIT + 1
    })
  }

  matches.sort(function(left, right) {
    if (left.weakest !== right.weakest) return right.weakest - left.weakest
    if (left.total !== right.total) return right.total - left.total
    if (left.recentRank !== right.recentRank) return left.recentRank - right.recentRank
    return right.insertionIndex - left.insertionIndex
  })

  var results = []
  for (var resultIndex = 0; resultIndex < matches.length && results.length < cap; resultIndex++) {
    results.push(matches[resultIndex].bookmark)
  }
  return results
}

if (typeof module !== "undefined") {
  module.exports = {
    BOOKMARK_SCHEMA_VERSION: BOOKMARK_SCHEMA_VERSION,
    RECENT_LIMIT: RECENT_LIMIT,
    DISPLAY_LIMIT: DISPLAY_LIMIT,
    BOOKMARK_FILE_LIMIT: BOOKMARK_FILE_LIMIT,
    RECENT_FILE_LIMIT: RECENT_FILE_LIMIT,
    BOOKMARK_LIMIT: BOOKMARK_LIMIT,
    ID_LIMIT: ID_LIMIT,
    TITLE_LIMIT: TITLE_LIMIT,
    URL_LIMIT: URL_LIMIT,
    TAG_LIMIT: TAG_LIMIT,
    TAG_LENGTH_LIMIT: TAG_LENGTH_LIMIT,
    JSON_DEPTH_LIMIT: JSON_DEPTH_LIMIT,
    utf8ByteLength: utf8ByteLength,
    emptyDocument: emptyDocument,
    emptyRecentDocument: emptyRecentDocument,
    parseBookmarks: parseBookmarks,
    serializeBookmarks: serializeBookmarks,
    isValidHttpUrl: isValidHttpUrl,
    normalizeUrl: normalizeUrl,
    duplicateKey: duplicateKey,
    urlHost: urlHost,
    normalizeTitle: normalizeTitle,
    normalizeTags: normalizeTags,
    normalizeFavicon: normalizeFavicon,
    normalizeSafeFavicon: normalizeSafeFavicon,
    generateId: generateId,
    createBookmark: createBookmark,
    parsePastedInput: parsePastedInput,
    parseLegacyInput: parseLegacyInput,
    snapshotActivation: snapshotActivation,
    appendBookmark: appendBookmark,
    updateBookmark: updateBookmark,
    deleteBookmark: deleteBookmark,
    normalizeRecentIds: normalizeRecentIds,
    touchRecent: touchRecent,
    removeRecent: removeRecent,
    parseRecent: parseRecent,
    serializeRecent: serializeRecent,
    buildSearchIndex: buildSearchIndex,
    searchBookmarks: searchBookmarks
  }
}
