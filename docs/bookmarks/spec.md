# Local Bookmarks Plugin Specification

## What

Create a standalone, publishable Omarchy Quattro overlay plugin in
`~/Dropbox/Dev/omarchy-bookmarks`.

- Repository: `idr4n/omarchy-bookmarks`
- Plugin ID: `io.github.idr4n.bookmarks`
- Product name: `Local Bookmarks`
- Kind: `overlay`, with `keepLoaded: true`

The plugin replaces the current dotfiles-specific bookmark picker and its Rofi
fallback after a verified migration. It must search, open, and add local
bookmarks without starting another Quickshell process or a background service.

### Assumptions

- Omarchy Quattro manifest schema 1 is the only supported shell contract.
- The legacy source is a UTF-8 line file whose first HTTP(S) URL is the URL,
  whose standalone `#tag` tokens are tags, and whose remaining text is the
  title. The source file remains untouched.
- Editing, deleting, folders, remote sync, and remote metadata are not part of
  the first release.

## Requirements

### Plugin and interaction contract

1. `manifest.json` declares an `overlay` entry point and `keepLoaded: true`.
2. The root QML item exposes `property bool opened`, true exactly while its
   `PanelWindow` is visible, and implements `open(payloadJson)`, `close()`, and
   `toggle()`.
3. These commands are stable public entry points:
   - Search: `omarchy-shell shell toggle io.github.idr4n.bookmarks '{}'`
   - Add: `omarchy-shell shell summon io.github.idr4n.bookmarks '{"mode":"add"}'`
   `open("")` and malformed payloads behave as `{}`. An add summon delivered
   while search is already open switches the mounted overlay into add mode.
4. Search mode:
   - filters while typing across title, URL, derived domain, and tags;
   - uses weighted token matching, with exact/prefix matches before substring
     matches and recent bookmarks as a tie-breaker;
   - renders at most 60 results and performs no subprocess or disk query per
     keystroke;
   - supports arrows, `Ctrl+J` / `Ctrl+K`, Enter to open, `Ctrl+Enter` to switch
     to add mode, and Escape to clear then close.
5. Add mode has URL, optional title, and optional comma-separated tags. A pasted
   legacy `Title | URL` value is accepted when the title field is empty.
6. Only absolute HTTP(S) URLs are accepted. An empty title falls back to the
   URL host. Duplicate detection compares a key that lowercases only the scheme
   and authority; it does not rewrite the stored path, query, or fragment.
7. Opening a bookmark uses an argv-safe detached `xdg-open` call, closes the
   overlay, and updates the bounded recent list. Adding a duplicate does not
   create another record; it reports the existing bookmark and treats it as
   recent.
8. The UI uses Omarchy's shared `qs.Commons` and `qs.Ui` primitives and theme
   tokens. It must not copy the dotfiles-only `plain-arch` components.
9. The layer-shell namespace is `idr4n-bookmarks`. The README documents a
   matching Hyprland `no_anim = true, animation = "none"` layer rule so
   compositor fades do not hide the plugin's low-latency open path.

### Persistence contract

Bookmarks are portable user data, while recency is non-portable application
state. Keep them separate according to the XDG Base Directory specification:

- `${XDG_DATA_HOME:-$HOME/.local/share}/io.github.idr4n.bookmarks/bookmarks.json`
- `${XDG_STATE_HOME:-$HOME/.local/state}/io.github.idr4n.bookmarks/recent.json`

Do not place data below an `omarchy/` child of `XDG_DATA_HOME`: on upgraded
Omarchy systems that path can be a symlink to root-owned `/usr/share/omarchy`.
Before activating either `FileView`, bootstrap its parent and empty document
with mode `0700` and `0600`, respectively; plugin-owned writes must preserve or
reapply those modes. A bootstrap or permission failure is a visible storage
error, never a fallback to packaged paths or a reason to use privilege
escalation. The importer enforces the same private modes. No mutable data is
stored inside the installed Git checkout, and removing the plugin does not
remove user bookmarks.

`bookmarks.json` schema 1:

```json
{
  "schemaVersion": 1,
  "bookmarks": [
    {
      "id": "opaque-unique-id",
      "title": "Example",
      "url": "https://example.com/",
      "tags": ["reference"],
      "createdAt": "2026-08-30T12:00:00.000Z"
    }
  ]
}
```

`recent.json` schema 1:

```json
{
  "schemaVersion": 1,
  "recentIds": ["opaque-unique-id"]
}
```

- `recentIds` is ordered newest first, unique, and capped at 10.
- The bookmark array retains insertion order; blank search shows recent records
  first, then the newest remaining records.
- Unknown fields on valid schema-1 bookmark objects survive a load/add/save
  round trip.
- A missing file initializes as an empty valid document after private storage
  bootstrap succeeds.
- Invalid JSON, an unsupported schema, or invalid top-level shape puts bookmark
  data into visible read-only error state. Mutations are disabled and the bad
  file is never overwritten. A later valid watched load clears this error
  without requiring a shell restart.
- Invalid recent state is recoverable: reset only recency, never bookmark data.
- `FileView` watches both files and reloads external atomic replacements.
  Self-write notifications whose bytes equal the last serialized payload skip
  redundant parsing. Any real reload updates data without resetting the active
  query, selection, or add form. `FileView` already serializes its own
  read/write activity; no additional lock is required for the single QML
  writer.

### Storage decision

Use versioned JSON loaded once into the keep-loaded QML process. Do not use the
legacy text format, JSON Lines, or SQLite in the first release.

Measured on the target workstation with representative records:

| Runtime | Records | JSON size | Parse + search-index build | No-match scan | Match + sort up to 60 rows |
| --- | ---: | ---: | ---: | ---: | ---: |
| Quickshell 0.3.1 JavaScript | 350 | about 100 KB | 4 ms | 0.11 ms | 0.22 ms |
| Quickshell 0.3.1 JavaScript | 10,000 | about 2.9 MB | 119–125 ms | 4.4 ms | 11.1 ms |
| Bun preliminary harness | 350 | 66 KB | 0.497 ms | 0.011 ms | not measured |
| Bun preliminary harness | 10,000 | 1.94 MB | 11.794 ms | 0.301 ms | not measured |

SQLite FTS through the `sqlite3` CLI measured about 2.1–2.2 ms per query at
350–10,000 rows, before QML result decoding. The current source contains 350
entries and 40,530 bytes. JSON therefore provides fields, schema migration,
atomic `FileView` writes, easy backup, a 4 ms startup-only parse, and no
search-time IPC. Quickshell's built-in `QtQuick.LocalStorage` API has no FTS and
uses a Qt-managed location; an external SQLite design needs a native binding or
a subprocess/daemon.

Reconsider a precomputed or incremental index near 5,000 records or 1 MB, or
whenever a Quickshell-engine cold load exceeds 50 ms on supported hardware.
Benchmark SQLite/FTS as a migration candidate only if those simpler measures do
not keep the real query path within one 16 ms frame.

### Legacy migration contract

Ship a dependency-free Python CLI named `bookmarkctl` with:

```text
bookmarkctl import-legacy PATH [--dry-run] [--merge] [--data-file PATH]
```

- Parse every nonblank line using the first HTTP(S) URL.
- From the non-URL text before and after that URL, extract standalone tokens
  matching `#[\w-]+` into lowercase tags, strip `#`, de-duplicate while
  preserving order, and remove them from title text.
- Join the remaining before/after text with one space, trim common separators,
  and use it as the title; fall back to the URL host.
- Preserve Unicode. A second URL remains in the derived title and produces a
  line-number-only warning rather than silent data loss.
- Generate deterministic IDs from the duplicate key so rerunning `--merge` is
  idempotent.
- Report imported, duplicate, malformed, ambiguous, rows-with-tags, and tag
  token counts without printing private bookmark contents.
- `--dry-run` performs no writes.
- Without `--merge`, refuse to replace a non-empty destination.
- Write through a same-directory temporary file, `fsync`, atomic rename, and
  private permissions.
- For the current file, migration must report 350 valid entries, zero missing
  URLs, zero duplicate URLs, one multiple-URL warning, 121 tagged rows, and 170
  extracted tag tokens, then produce 350 records while leaving
  `~/Dropbox/bookmarks` unchanged.

### Distribution and cutover

1. The public repository root contains `manifest.json` with `license: "MIT"`
   and `homepage: "https://github.com/idr4n/omarchy-bookmarks"`, README, MIT
   license, source, tests, and a real plugin preview.
2. Runtime dependencies are limited to Omarchy Quattro, Quickshell, Python 3
   for the explicit migration CLI, coreutils for private storage bootstrap, and
   `xdg-open` for opening URLs.
3. The README documents permissions, exact data paths, install, the search and
   add commands, keybindings, the `idr4n-bookmarks` no-animation layer rule,
   migration, validation, removal, external dependencies, and retained data.
4. Build and validate entirely in the isolated repository. Do not modify the
   prayers plugin or any prayers-related files.
5. Switch dotfiles bindings and remove the old bookmark scripts/QML only after
   local install, migration-count verification, search/add/open smoke tests, and
   a backup of the legacy source. Coordinate the dotfiles cutover if another
   agent still has that worktree active.

## Design

- `Bookmarks.qml`: Omarchy overlay lifecycle, `FileView` persistence, keyboard
  interaction, and argv-safe open action.
- `BookmarkModel.js`: pure normalization, parsing, duplicate keys, ranking,
  recency, and serialization. It stays QML-compatible and is also exercised by
  Node tests.
- `bookmarkctl`: one-shot import CLI. It is never involved in overlay startup,
  searching, opening, or adding.
- No package manager, runtime database, watcher daemon, network request, install
  hook, or second Quickshell process.

Because `keepLoaded: true` mounts the overlay at shell startup, it loads and
indexes JSON before the first summon. Startup storage errors remain isolated to
the plugin. Later summons only reset view state and focus the requested field.
Each query is a bounded linear scan over precomputed lowercase fields; only the
top 60 rows reach QML delegates.

## Testing Strategy

- Node model tests: valid/invalid schema, unknown-field preservation, URL
  validation and duplicate keys, tags, title fallback, ranking, recent cap,
  add serialization, and malformed-file write guard.
- Python importer tests: dry run, Unicode, malformed lines, multiple URLs,
  duplicate merge, refusal to overwrite, permissions, atomic replacement, and
  exact current-corpus aggregate counts without exposing contents.
- Static checks: `omarchy plugin validate .`, `qmllint -I "$OMARCHY_PATH/shell"
  Bookmarks.qml`, Node tests, and Python unit tests.
- Performance smoke runs inside Quickshell, not only Node: generated
  10,000-record JSON must load/index under 150 ms and match/sort a query under
  16 ms on the target workstation. Node remains the unit-test runner.
- Actual-surface smoke: development-link the repository into the user plugin
  directory, summon search and add payloads, exercise keyboard navigation, add
  a controlled example entry, search it, open it, restart the shell, verify
  persistence, remove the known fixture, disable/re-enable, and remove the
  plugin while confirming data remains.
- Cutover smoke: imported count equals 350, both new bindings summon the correct
  modes without compositor fades, and no old bookmark script or plain-Arch
  overlay caller remains.

## Out of Scope

- Browser bookmark import, synchronization, cloud APIs, favicons, and page
  metadata fetching.
- Bookmark editing, deletion, folders, notes, bulk operations, and sharing.
- SQLite/FTS, a resident helper daemon, or a subprocess per query.
- Automatic keybinding installation or plugin-manager lifecycle hooks.
- Continuing the Rofi/plain-Arch fallback after the verified Omarchy cutover.
