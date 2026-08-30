#!/usr/bin/env node

const assert = require("node:assert/strict");
const model = require("../BookmarkModel.js");

const createdAt = "2026-08-30T12:00:00.000Z";

function bookmark(id, url, title = "Example", tags = []) {
  return { id, title, url, tags, createdAt };
}

function documentWith(bookmarks, extra = {}) {
  return { schemaVersion: 1, bookmarks, ...extra };
}

function parseDocument(document) {
  return model.parseBookmarks(JSON.stringify(document));
}

function assertFailure(result, state, code, index) {
  assert.equal(result.state, state);
  assert.equal(result.writable, false);
  assert.equal(result.document, null);
  assert.equal(result.error.code, code);
  if (index !== undefined) assert.equal(result.error.index, index);
}

const missing = model.parseBookmarks(null);
assert.equal(missing.state, "missing");
assert.equal(missing.writable, true);
assert.deepEqual(missing.document, { schemaVersion: 1, bookmarks: [] });
assert.deepEqual(JSON.parse(model.serializeBookmarks(missing)), missing.document);

const source = documentWith(
  [
    {
      ...bookmark("known", "https://example.com/Path?Case=Yes#Keep", "Known", ["Reference"]),
      futureFlag: true,
      futureData: { nested: [1, 2, 3] },
    },
  ],
  { futureTopLevel: "preserved" },
);
const valid = parseDocument(source);
assert.equal(valid.state, "valid");
assert.equal(valid.writable, true);
assert.equal(valid.error, null);
assert.deepEqual(valid.document.bookmarks[0].tags, ["reference"]);
const roundTripped = JSON.parse(model.serializeBookmarks(valid));
assert.equal(roundTripped.futureTopLevel, "preserved");
assert.equal(roundTripped.bookmarks[0].futureFlag, true);
assert.deepEqual(roundTripped.bookmarks[0].futureData, { nested: [1, 2, 3] });
assert.equal(roundTripped.bookmarks[0].url, "https://example.com/Path?Case=Yes#Keep");

const malformedJson = model.parseBookmarks("{");
assertFailure(malformedJson, "malformed", "invalid-json");
assert.throws(() => model.serializeBookmarks(malformedJson), /not writable/);

const unsupported = parseDocument({ schemaVersion: 2, bookmarks: [] });
assertFailure(unsupported, "unsupported", "unsupported-schema");
assert.throws(() => model.serializeBookmarks(unsupported), /not writable/);

assertFailure(model.parseBookmarks("[]"), "malformed", "invalid-top-level");
assertFailure(parseDocument({ bookmarks: [] }), "malformed", "missing-schema-version");
assertFailure(
  parseDocument({ schemaVersion: 1, bookmarks: {} }),
  "malformed",
  "invalid-bookmarks",
);
assertFailure(
  parseDocument(documentWith([bookmark("bad-url", "ftp://example.com/")])),
  "malformed",
  "invalid-url",
  0,
);
assertFailure(
  parseDocument(documentWith([{ ...bookmark("bad-tags", "https://example.com/"), tags: "tag" }])),
  "malformed",
  "invalid-tags",
  0,
);
assertFailure(
  parseDocument(documentWith([{ ...bookmark("bad-date", "https://example.com/"), createdAt: "later" }])),
  "malformed",
  "invalid-created-at",
  0,
);
assertFailure(
  parseDocument(documentWith([{
    ...bookmark("bad-favicon", "https://example.com/"),
    favicon: "../private.png",
  }])),
  "malformed",
  "invalid-favicon",
  0,
);
assert.equal(
  parseDocument(documentWith([{
    ...bookmark("favicon", "https://icon.example/"),
    favicon: `favicons/${"a".repeat(64)}.png`,
  }])).state,
  "valid",
);

for (const [index, isoDate] of [
  "2026-08-30T12:00:00Z",
  "2026-08-30T12:00:00.1Z",
  "2026-08-30T12:00:00.12+03:00",
  "2026-08-30T12:00:00.123-04:30",
].entries()) {
  const result = parseDocument(
    documentWith([{ ...bookmark(`valid-date-${index}`, `https://date-${index}.example/`), createdAt: isoDate }]),
  );
  assert.equal(result.state, "valid");
}
for (const [index, isoDate] of [
  "2026-08-30T12:00:00.1234Z",
  "2026-08-30T12:00:00+0300",
  "2026-08-30T12:00:00.Z",
]) {
  assertFailure(
    parseDocument(
      documentWith([{ ...bookmark(`invalid-date-${index}`, `https://bad-date-${index}.example/`), createdAt: isoDate }]),
    ),
    "malformed",
    "invalid-created-at",
    0,
  );
}

const duplicateId = parseDocument(
  documentWith([
    bookmark("same", "https://one.example/"),
    bookmark("same", "https://two.example/"),
  ]),
);
assertFailure(duplicateId, "malformed", "duplicate-id", 1);
assert.equal(duplicateId.error.firstIndex, 0);

const duplicateUrl = parseDocument(
  documentWith([
    bookmark("one", "HTTPS://Example.COM/Path?Q=One#Frag"),
    bookmark("two", "https://example.com/Path?Q=One#Frag"),
  ]),
);
assertFailure(duplicateUrl, "malformed", "duplicate-url", 1);
assert.equal(duplicateUrl.error.firstIndex, 0);

assert.equal(
  model.duplicateKey("HTTPS://User@Example.COM:8443/Path?Q=Mixed#Fragment"),
  "https://user@example.com:8443/Path?Q=Mixed#Fragment",
);
assert.notEqual(
  model.duplicateKey("https://example.com/Path?Q=Mixed#Fragment"),
  model.duplicateKey("https://example.com/path?Q=Mixed#Fragment"),
);
assert.notEqual(
  model.duplicateKey("https://example.com/Path?Q=Mixed#Fragment"),
  model.duplicateKey("https://example.com/Path?q=Mixed#Fragment"),
);
assert.equal(
  parseDocument(
    documentWith([
      bookmark("upper-path", "https://example.com/Path"),
      bookmark("lower-path", "https://example.com/path"),
    ]),
  ).state,
  "valid",
);

assert.equal(model.isValidHttpUrl("https://example.com/a?b=c#d"), true);
assert.equal(model.isValidHttpUrl("http://localhost:8080/"), true);
assert.equal(model.isValidHttpUrl("https://[::1]/"), true);
assert.equal(model.isValidHttpUrl("/relative"), false);
assert.equal(model.isValidHttpUrl("file:///tmp/bookmarks"), false);
assert.equal(model.isValidHttpUrl("https://example.com:70000/"), false);
assert.equal(model.normalizeUrl("  https://example.com/Exact  "), "https://example.com/Exact");
assert.equal(model.normalizeTitle("   ", "https://WWW.Example.COM:8443/path"), "www.example.com");
assert.deepEqual(
  model.normalizeTags([" Reference ", "#LINUX", "reference", "", "# spaced tag "]),
  ["reference", "linux", "spaced tag"],
);
assert.deepEqual(model.normalizeTags("One, #TWO, one"), ["one", "two"]);

const firstId = model.generateId([], 1000, () => 0);
const secondId = model.generateId([firstId], 1000, () => 0);
assert.match(firstId, /^bkm_[a-z0-9_]+$/);
assert.notEqual(secondId, firstId);
assert.match(secondId, /^bkm_[a-z0-9_]+$/);

const created = model.createBookmark(
  {
    url: " https://Example.COM/new ",
    title: " ",
    tags: "One, #TWO, one",
    favicon: `favicons/${"b".repeat(64)}.webp`,
  },
  [],
  0,
  () => 0,
);
assert.equal(created.ok, true);
assert.equal(created.bookmark.title, "example.com");
assert.equal(created.bookmark.url, "https://Example.COM/new");
assert.deepEqual(created.bookmark.tags, ["one", "two"]);
assert.equal(created.bookmark.createdAt, "1970-01-01T00:00:00.000Z");
assert.equal(created.bookmark.favicon, `favicons/${"b".repeat(64)}.webp`);
const duplicateCreated = model.createBookmark(
  { url: "HTTPS://example.com/new", title: "Duplicate", tags: [] },
  [created.bookmark],
  1,
  () => 0.5,
);
assert.equal(duplicateCreated.ok, false);
assert.equal(duplicateCreated.error.code, "duplicate-url");
assert.equal(duplicateCreated.existing, created.bookmark);

assert.deepEqual(
  model.parsePastedInput("Legacy title | https://example.com/legacy", ""),
  { url: "https://example.com/legacy", title: "Legacy title" },
);
assert.deepEqual(
  model.parsePastedInput("Read https://example.com/direct for later", ""),
  { url: "https://example.com/direct", title: "Read for later" },
);
assert.deepEqual(
  model.parsePastedInput("(https://example.com/wrapped).", ""),
  { url: "https://example.com/wrapped", title: "" },
);
assert.deepEqual(
  model.parsePastedInput("Ignored | https://example.com/direct", "Explicit"),
  { url: "https://example.com/direct", title: "Explicit" },
);
assert.deepEqual(
  model.parsePastedInput("not a URL", ""),
  { url: "not a URL", title: "" },
);
assert.deepEqual(
  model.parseLegacyInput("Legacy | https://example.com/alias", ""),
  { url: "https://example.com/alias", title: "Legacy" },
);

let activationRowValid = true;
const activationRow = {};
Object.defineProperties(activationRow, {
  bookmarkId: {
    get: () => activationRowValid ? "selected-id" : undefined,
  },
  url: {
    get: () => activationRowValid ? "https://example.com/selected" : undefined,
  },
});
const activationTarget = model.snapshotActivation(activationRow);
activationRowValid = false;
assert.deepEqual(activationTarget, {
  bookmarkId: "selected-id",
  url: "https://example.com/selected",
});

for (const url of [
  "https://en.wikipedia.org/wiki/Python_(programming_language)",
  "https://example.com/search?q=what?",
  "https://example.com/it's-here",
  "https://example.com/a|b",
]) {
  assert.deepEqual(
    model.parsePastedInput(url, "Edited title"),
    { url, title: "Edited title" },
  );
}
assert.deepEqual(
  model.parsePastedInput(
    "Read (https://en.wikipedia.org/wiki/Python_(programming_language)).",
    "",
  ),
  {
    url: "https://en.wikipedia.org/wiki/Python_(programming_language)",
    title: "Read",
  },
);
const punctuationRoundTrip = parseDocument(documentWith([
  bookmark(
    "punctuation",
    "https://en.wikipedia.org/wiki/Python_(programming_language)",
    "Python",
  ),
]));
const punctuationInput = model.parsePastedInput(
  punctuationRoundTrip.document.bookmarks[0].url,
  "Renamed",
);
const punctuationUpdated = model.updateBookmark(
  punctuationRoundTrip,
  "punctuation",
  { ...punctuationInput, tags: [] },
);
assert.equal(punctuationUpdated.ok, true);
assert.equal(
  punctuationUpdated.bookmark.url,
  "https://en.wikipedia.org/wiki/Python_(programming_language)",
);

const appended = model.appendBookmark(valid, created.bookmark);
assert.equal(appended.ok, true);
assert.equal(valid.document.bookmarks.length, 1);
assert.equal(appended.parseResult.document.bookmarks.length, 2);
const appendedRoundTrip = JSON.parse(model.serializeBookmarks(appended.parseResult));
assert.equal(appendedRoundTrip.futureTopLevel, "preserved");
assert.deepEqual(appendedRoundTrip.bookmarks[0].futureData, { nested: [1, 2, 3] });
assert.equal(appendedRoundTrip.bookmarks[1].id, created.bookmark.id);
assert.equal(model.appendBookmark(malformedJson, created.bookmark).error.code, "read-only");
const updated = model.updateBookmark(appended.parseResult, "known", {
  url: "https://example.com/updated",
  title: "Updated",
  tags: "Edited, #Tags",
  favicon: `favicons/${"c".repeat(64)}.ico`,
});
assert.equal(updated.ok, true);
assert.equal(updated.bookmark.id, "known");
assert.equal(updated.bookmark.createdAt, createdAt);
assert.equal(updated.bookmark.futureFlag, true);
assert.deepEqual(updated.bookmark.futureData, { nested: [1, 2, 3] });
assert.equal(updated.bookmark.url, "https://example.com/updated");
assert.deepEqual(updated.bookmark.tags, ["edited", "tags"]);
assert.equal(updated.bookmark.favicon, `favicons/${"c".repeat(64)}.ico`);
assert.equal(appended.parseResult.document.bookmarks[0].url, "https://example.com/Path?Case=Yes#Keep");

const duplicateUpdate = model.updateBookmark(updated.parseResult, "known", {
  url: created.bookmark.url,
  title: "Duplicate",
  tags: [],
});
assert.equal(duplicateUpdate.ok, false);
assert.equal(duplicateUpdate.error.code, "duplicate-url");
assert.equal(updated.parseResult.document.bookmarks[0].url, "https://example.com/updated");

const clearedFavicon = model.updateBookmark(updated.parseResult, "known", {
  url: "https://example.com/updated",
  title: "Updated",
  tags: [],
  favicon: "",
});
assert.equal(clearedFavicon.ok, true);
assert.equal(Object.hasOwn(clearedFavicon.bookmark, "favicon"), false);
assert.equal(model.updateBookmark(malformedJson, "known", {}).error.code, "read-only");
assert.equal(model.updateBookmark(updated.parseResult, "missing", {}).error.code, "not-found");

const deleted = model.deleteBookmark(clearedFavicon.parseResult, "known");
assert.equal(deleted.ok, true);
assert.equal(deleted.removed.id, "known");
assert.deepEqual(deleted.parseResult.document.bookmarks.map((entry) => entry.id), [created.bookmark.id]);
assert.equal(clearedFavicon.parseResult.document.bookmarks.length, 2);
assert.equal(model.deleteBookmark(malformedJson, "known").error.code, "read-only");
assert.equal(model.deleteBookmark(deleted.parseResult, "missing").error.code, "not-found");

const recentInput = [
  "id-0", "id-1", "id-0", "", null,
  "id-2", "id-3", "id-4", "id-5", "id-6", "id-7", "id-8", "id-9", "id-10",
];
assert.deepEqual(
  model.normalizeRecentIds(recentInput),
  ["id-0", "id-1", "id-2", "id-3", "id-4", "id-5", "id-6", "id-7", "id-8", "id-9"],
);
assert.deepEqual(model.normalizeRecentIds([" a ", "a", "b"], 2), ["a", "b"]);
assert.deepEqual(
  model.touchRecent(["two", "one", "three"], "one"),
  ["one", "two", "three"],
);
assert.deepEqual(
  model.removeRecent(["one", "two", "one", "three"], "one"),
  ["two", "three"],
);

const missingRecent = model.parseRecent(null);
assert.equal(missingRecent.state, "missing");
assert.equal(missingRecent.writable, true);
assert.deepEqual(missingRecent.document, { schemaVersion: 1, recentIds: [] });

const validRecent = model.parseRecent(JSON.stringify({
  schemaVersion: 1,
  recentIds: ["two", "one", "two"],
  futureState: true,
}));
assert.equal(validRecent.state, "valid");
assert.deepEqual(validRecent.document.recentIds, ["two", "one"]);
assert.equal(JSON.parse(model.serializeRecent(validRecent)).futureState, true);

for (const rawRecent of [
  "{",
  "[]",
  JSON.stringify({ schemaVersion: 2, recentIds: ["one"] }),
  JSON.stringify({ schemaVersion: 1, recentIds: "one" }),
]) {
  const resetRecent = model.parseRecent(rawRecent);
  assert.equal(resetRecent.state, "reset");
  assert.equal(resetRecent.writable, true);
  assert.deepEqual(resetRecent.document, { schemaVersion: 1, recentIds: [] });
}

const rankedBookmarks = [
  bookmark("substring", "https://substring.test/", "Xalpha notes"),
  bookmark("prefix", "https://prefix.test/", "Alphabet soup"),
  bookmark("exact", "https://exact.test/", "Alpha guide"),
];
const rankedIndex = model.buildSearchIndex(rankedBookmarks);
assert.deepEqual(
  model.searchBookmarks(rankedIndex, "alpha", [], 60).map((entry) => entry.id),
  ["exact", "prefix", "substring"],
);

const weightedBookmarks = [
  bookmark("tag", "https://tag.test/", "Notes", ["alpha"]),
  bookmark("title", "https://title.test/", "Alpha", []),
];
assert.deepEqual(
  model.searchBookmarks(model.buildSearchIndex(weightedBookmarks), "alpha", [], 60)
    .map((entry) => entry.id),
  ["title", "tag"],
);

const tiedBookmarks = [
  bookmark("recent", "https://recent.test/", "Alpha one"),
  bookmark("newest", "https://newest.test/", "Alpha two"),
];
assert.deepEqual(
  model.searchBookmarks(model.buildSearchIndex(tiedBookmarks), "alpha", ["recent"], 60)
    .map((entry) => entry.id),
  ["recent", "newest"],
);

const blankBookmarks = [
  bookmark("one", "https://one.test/"),
  bookmark("two", "https://two.test/"),
  bookmark("three", "https://three.test/"),
  bookmark("four", "https://four.test/"),
];
assert.deepEqual(
  model.searchBookmarks(model.buildSearchIndex(blankBookmarks), "", ["two", "one"], 60)
    .map((entry) => entry.id),
  ["two", "one", "four", "three"],
);

const manyBookmarks = [];
for (let index = 0; index < 65; index += 1) {
  manyBookmarks.push(bookmark(`many-${index}`, `https://many-${index}.test/`));
}
const manyIndex = model.buildSearchIndex(manyBookmarks);
assert.equal(model.searchBookmarks(manyIndex, "", [], 100).length, 60);
assert.equal(model.searchBookmarks(manyIndex, "", [], 0).length, 0);
assert.equal(model.searchBookmarks(manyIndex, "no-match", [], 60).length, 0);

console.log("bookmark model: ok");
