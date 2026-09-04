# Contributing

This document covers contributor checks and release validation. Installed users do not need any of it.

## Development validation

From the repository root:

```sh
node tests/bookmark-model.js
python3 -m unittest discover -s tests -v
qmllint scripts/test/storage-boundary.qml scripts/test/fileview-cache.qml scripts/test/favicon-visibility.qml
marker="$(mktemp)"
BENCHMARK_RESULT="$marker" timeout 30 qs -p scripts/bench/shell.qml || test "$?" -eq 124
test "$(cat "$marker")" = PASS
rm -f "$marker"
omarchy plugin validate .
qmllint -I "$OMARCHY_PATH/shell" Bookmarks.qml
jq -e '.license == "MIT" and .homepage == "https://github.com/idr4n/omarchy-bookmarks"' manifest.json
```

The QML boundary suite runs offscreen without opening the desktop overlay. Its literal-text regression instantiates the query, search-state, form-status, and footer `Text` elements from `Bookmarks.qml` and compares their layout with plain text containing markup-like input.

The Quickshell benchmark reports 350-row and 5,000-row results separately. Its release budget is under 150 ms for 5,000-row parse plus index construction and under 16 ms for a matching search and sort on the target workstation.

JSON is intentionally bounded at 5,000 records and 1 MiB. Reconsider a precomputed or incremental index if a Quickshell-engine cold load approaches 50 ms on supported hardware. Benchmark SQLite/FTS only if simpler indexing changes cannot keep the real query path within one 16 ms frame.

## Pre-release favicon cache

Builds before 2026-09-01 stored raw downloaded icons under `favicons/`. Those paths remain schema-compatible but are never rendered. Convert such a cache once, offline:

```sh
./bookmarkctl normalize-favicons --dry-run
./bookmarkctl normalize-favicons
```

Normalization uses the bounded ImageMagick decoder, writes only static PNGs under `favicons-v2/`, atomically updates matching records, and deletes an old file only after it is unreferenced. Use `--limit N` for a smaller batch.
