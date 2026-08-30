# Local Bookmarks Plugin Implementation Plan

Authoritative contract: [`spec.md`](spec.md)

Work only in `~/Dropbox/Dev/omarchy-bookmarks` through Task 6. Task 7 is the
explicit dotfiles cutover boundary; do not touch prayers code or prayers-related
files in any task.

## Task 1: Implement the versioned bookmark model

### Context

The QML overlay needs a pure, testable data model before UI work. JSON is the
canonical user-data format; invalid bookmark data must never be converted to an
empty writable model. Keep the JavaScript compatible with both QML's engine and
Node (no Node-only runtime APIs in production functions).

### Change

- Add `manifest.json` for `io.github.idr4n.bookmarks`, kind `overlay`, entry
  point `Bookmarks.qml`, `keepLoaded: true`, `license: "MIT"`, and
  `homepage: "https://github.com/idr4n/omarchy-bookmarks"`.
- Add `BookmarkModel.js` with schema-1 parsing, normalization, URL validation,
  duplicate-key construction, title fallback, tag normalization, opaque ID
  generation, recent-list normalization, ranking, and serialization.
- Preserve unknown fields on valid schema-1 bookmark records.
- Return explicit parse states for missing, valid, malformed, and unsupported
  documents; malformed and unsupported states are not serializable.
- Add a minimal lifecycle-correct `Bookmarks.qml` entry point so the manifest
  and QML can be validated. It must expose `property bool opened`, tolerate an
  empty or malformed payload as `{}`, and implement real `open`, `close`, and
  `toggle` behavior rather than a placeholder.
- Add `tests/bookmark-model.js` covering the model contract.
- Add the MIT `LICENSE`, retaining upstream notices only if implementation code
  is actually derived from an MIT-licensed Omarchy plugin.

### Acceptance Criteria

- Valid schema-1 documents round-trip without dropping unknown bookmark fields.
- Missing data becomes an empty writable schema-1 document.
- Malformed JSON, unsupported schema versions, invalid top-level shape, invalid
  bookmark URLs, and duplicate IDs/URLs are reported deterministically.
- URL duplicate keys lowercase only scheme and authority; path, query, and
  fragment remain byte-for-byte significant.
- Recent IDs are unique, newest first, and capped at 10.
- Closing itself sets `opened` false so the shell's next `toggle` reopens it on
  the first keypress.
- The manifest includes the marketplace-required license and homepage fields.
- The manifest and QML entry point pass Omarchy's validators.

### Verify

```sh
cd ~/Dropbox/Dev/omarchy-bookmarks
node tests/bookmark-model.js
omarchy plugin validate .
qmllint -I "$OMARCHY_PATH/shell" Bookmarks.qml
```

## Task 2: Deliver resident search and open behavior

### Context

The hot path must be QML/JavaScript only. Follow the installed
`omarchy.clipboard` and `omarchy.image-picker` overlay contracts and the sibling
`omarchy-clipboard-plus` use of shared Omarchy UI primitives. Do not import the
plain-Arch dotfiles component tree. A `keepLoaded` overlay mounts at shell
startup, so startup loading must be safe before any summon.

### Change

- Build the themed, keyboard-first overlay in `Bookmarks.qml` using
  `qs.Commons`, `qs.Ui`, `PanelWindow`, and the shell overlay lifecycle.
- Keep `opened` synchronized with visibility. Treat `open("")` or malformed
  payload as search mode and let repeated `open()` calls reset/switch the
  already-mounted view.
- Set `WlrLayershell.namespace` to `idr4n-bookmarks`.
- Resolve absolute XDG data/state homes with fallback rules from the spec and
  store below `io.github.idr4n.bookmarks/`, never below the potentially
  root-owned `XDG_DATA_HOME/omarchy` symlink.
- Before activating `FileView`, create private parent directories and empty
  documents with modes `0700`/`0600`. Preserve or reapply private file modes
  after plugin-owned writes. Surface bootstrap and permission failures.
- Use watched `FileView` objects with atomic writes. Track the last serialized
  payload so a self-write notification skips redundant parsing; a genuine load
  must preserve active query/add UI state and clear an earlier read-only error
  when repaired.
- Parse and precompute lowercase title, URL, domain, and tag search fields once
  per real data reload.
- Implement weighted token filtering, blank-query ordering, a 60-row display
  cap, selection clamping, pointer gating, arrows, `Ctrl+J` / `Ctrl+K`, Escape,
  and mouse activation.
- Open only validated HTTP(S) URLs through `Quickshell.execDetached(["xdg-open",
  url])`; never construct a shell command string.
- Show explicit loading, empty, malformed-data, unsupported-schema, and
  directory/file error states.
- Extend model tests for ranking and display caps.
- After static validation, development-link this repository at
  `~/.config/omarchy/plugins/io.github.idr4n.bookmarks`, rescan, and enable it.
  Keep this link through Tasks 2–5.

### Acceptance Criteria

- The keep-loaded root mounts safely at shell startup and reports storage errors
  without destabilizing `omarchy-shell`.
- `open("")` and `open("{}")` focus an empty search field and display recent
  bookmarks first, then newest remaining bookmarks.
- Escape-driven close sets `opened` false; the next search `toggle` opens on the
  first invocation.
- Typing filters title, domain, tags, and URL without starting a process or
  rereading disk per keypress.
- Keyboard and pointer activation open the selected exact stored URL and close
  the overlay.
- Private storage initializes successfully even though
  `~/.local/share/omarchy` resolves to root-owned `/usr/share/omarchy`; no plugin
  data is written through that symlink.
- Invalid bookmark data remains visible as read-only error state and is never
  overwritten. A valid external atomic replacement clears the error without a
  shell restart.
- A plugin-owned save/reload does not reset query, selection, or add state and
  preserves `0600` file mode.

### Verify

```sh
cd ~/Dropbox/Dev/omarchy-bookmarks
node tests/bookmark-model.js
omarchy plugin validate .
qmllint -I "$OMARCHY_PATH/shell" Bookmarks.qml
plugin_dir="$HOME/.config/omarchy/plugins/io.github.idr4n.bookmarks"
test ! -e "$plugin_dir" && test ! -L "$plugin_dir"
ln -s "$PWD" "$plugin_dir"
omarchy-shell shell rescanPlugins
omarchy plugin enable io.github.idr4n.bookmarks
omarchy plugin list --json | jq -e '.[] | select(.id == "io.github.idr4n.bookmarks" and .enabled == true)'
```

Exercise open, filtering, navigation, URL activation, self-write reload,
external replacement, and malformed-file recovery in the actual shell. The
shell uses the real XDG paths: first prove no prior bookmark data exists or back
it up, then use a synthetic schema-1 fixture with a recorded checksum. Never use
the legacy source as a writable fixture.

## Task 3: Add structured capture and bounded recency

### Context

Adding must remain fast but write structured data safely. Bookmark data and
recent state have different XDG lifecycles and different failure severity. The
development link from Task 2 supplies the live plugin.

### Change

- Parse every summon payload defensively and enter add mode for
  `{"mode":"add"}`. Re-delivery while search is open switches modes in place.
- Add keyboard-navigable URL, optional title, and optional comma-separated tag
  fields. Accept a pasted `Title | URL` legacy entry when title is empty.
- Implement `Ctrl+Enter` from search to add, Escape back/close behavior, and
  focus restoration.
- Validate HTTP(S), title fallback, normalized tags, unique IDs, and duplicate
  URL keys through `BookmarkModel.js`.
- Append new records and save pretty schema-1 JSON atomically while preserving
  unknown fields and private file mode.
- On a bookmark save failure, restore the prior in-memory document, keep the
  overlay open, and show an actionable error.
- Update `recent.json` only after open or duplicate activation. A recent-save
  failure must not corrupt or block bookmark data or opening.
- Keep FileView callbacks idempotent so own writes do not reset the active form.
- Extend tests for add, duplicate, round-trip, rollback inputs, and recent cap.

### Acceptance Criteria

- The documented add `summon` opens add mode when closed and switches from an
  already-open search overlay instead of hiding it.
- Direct add and `Ctrl+Enter` add produce the same valid stored record shape.
- A valid add survives shell restart and becomes searchable immediately.
- A duplicate does not add another record and becomes the newest recent item.
- A failed bookmark write leaves disk and the in-memory collection unchanged.
- Invalid recent state resets only recency; bookmark data remains available.
- Plugin-created and rewritten data/state files remain mode `0600`.

### Verify

```sh
cd ~/Dropbox/Dev/omarchy-bookmarks
node tests/bookmark-model.js
omarchy plugin validate .
qmllint -I "$OMARCHY_PATH/shell" Bookmarks.qml
omarchy-shell shell toggle io.github.idr4n.bookmarks '{}'
omarchy-shell shell summon io.github.idr4n.bookmarks '{"mode":"add"}'
```

In the actual overlay, add a controlled `https://example.com/` fixture with
Unicode title and two tags, find and open it, switch search to add while open,
restart `omarchy-shell`, confirm persistence, and test duplicate handling.
Afterward, remove only the checksum-verified synthetic fixture and restore any
pre-existing data backup; never delete an unrecognized document.

## Task 4: Ship the safe legacy importer

### Context

The existing source is valuable user data: 350 nonblank rows, all with an
HTTP(S) URL, no duplicate first URLs, one row containing multiple URLs, and 121
rows containing 170 standalone hashtag tokens. The migration must recover tags,
reveal ambiguity, never print private entries, and never mutate the source.

### Change

- Add executable `bookmarkctl` using only Python's standard library.
- Implement `import-legacy PATH [--dry-run] [--merge] [--data-file PATH]` exactly
  as specified, defaulting outside the `XDG_DATA_HOME/omarchy` symlink.
- Use deterministic URL-derived IDs for import idempotence.
- Extract standalone `#tag` tokens before and after the first URL into lowercase
  ordered, de-duplicated tags; remove them and the first URL from title text
  while preserving other before/after text.
- Report ambiguous line numbers and aggregate counts only; never print bookmark
  titles, tags, or URLs.
- Write private files via a same-directory temporary file, flush, `fsync`, chmod,
  atomic replacement, and parent-directory `fsync` where supported.
- Refuse a non-empty destination without `--merge`; merge by duplicate key while
  preserving existing records and unknown fields.
- Add `tests/test_bookmarkctl.py` and synthetic fixtures for malformed, Unicode,
  duplicate, ambiguous, before/after tags, trailing prose, overwrite, merge,
  permission, and source-integrity behavior.

### Acceptance Criteria

- Dry run and real import report only aggregate counts plus ambiguous line
  numbers.
- Rerunning with `--merge` adds nothing and preserves the same deterministic
  record identities.
- Any pre-write parse/schema failure leaves the destination byte-identical.
- The current legacy source dry run reports 350 valid, zero missing-URL, zero
  duplicate, one ambiguous row, 121 tagged rows, and 170 extracted tag tokens
  without displaying bookmark contents.
- No imported title contains the first URL or a recognized `#tag` token.
- A real import to a temporary destination creates 350 records and leaves the
  source checksum unchanged.

### Verify

```sh
cd ~/Dropbox/Dev/omarchy-bookmarks
python3 -m unittest discover -s tests -p 'test_bookmarkctl.py' -v
./bookmarkctl import-legacy "$HOME/Dropbox/bookmarks" --dry-run
work="$(mktemp -d)"
before="$(sha256sum "$HOME/Dropbox/bookmarks")"
./bookmarkctl import-legacy "$HOME/Dropbox/bookmarks" --data-file "$work/bookmarks.json"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8")); assert len(d["bookmarks"]) == 350; assert sum(bool(x["tags"]) for x in d["bookmarks"]) == 121; assert sum(len(x["tags"]) for x in d["bookmarks"]) == 170' "$work/bookmarks.json"
test "$before" = "$(sha256sum "$HOME/Dropbox/bookmarks")"
rm -rf "$work"
```

## Task 5: Prove real-engine performance and package the release candidate

### Context

The published repository must explain its privileges and data ownership and
must carry evidence that JSON remains comfortably below the plugin's latency
budget. Node is suitable for model correctness, but only Quickshell's JavaScript
engine represents runtime latency.

### Change

- Add `scripts/bench/shell.qml` to generate representative 350- and
  10,000-record documents, exercise production indexing/search code, record
  parse/index, no-match, and match/sort timings, and write `PASS` to the path in
  `BENCHMARK_RESULT` only when the configured budgets hold.
- Add a root `README.md` with features, external dependencies, install, search
  toggle, add summon, keybindings, `idr4n-bookmarks` no-animation layer rule,
  exact data/state paths, migration, privacy/permissions, validation, removal,
  and the fact that removal retains data.
- Document the Quickshell-engine JSON reconsideration thresholds from the spec
  without promising arbitrary-scale performance.
- Run every model/importer/static check together once.
- Create `preview.png` from the actual running overlay; do not use a mockup.
- Inspect every packaged path and ensure no private bookmark data, generated
  fixture, cache, symlink, token, or dotfiles-only path is present.
- Explicitly verify that the manifest contains `license` and `homepage`, because
  `omarchy plugin validate` does not enforce marketplace metadata.

### Acceptance Criteria

- In Quickshell 0.3.1, 10,000 representative rows parse/index under 150 ms and
  match/sort under 16 ms on the target workstation; the 350-row result is
  reported separately.
- The repository has no package-manager or runtime dependency beyond those in
  the spec.
- README commands match the final ID, payload, namespace, paths, and executable
  names and disclose Python, coreutils, and `xdg-open`.
- Manifest license/homepage, README removal instructions, root license, and
  preview satisfy marketplace structure requirements.
- The preview depicts the final themed plugin with synthetic, non-private data.
- All validators and tests pass from a clean repository checkout.

### Verify

```sh
cd ~/Dropbox/Dev/omarchy-bookmarks
node tests/bookmark-model.js
python3 -m unittest discover -s tests -p 'test_bookmarkctl.py' -v
marker="$(mktemp)"
BENCHMARK_RESULT="$marker" timeout 30 qs -p scripts/bench/shell.qml || test "$?" -eq 124
test "$(cat "$marker")" = PASS
rm -f "$marker"
omarchy plugin validate .
qmllint -I "$OMARCHY_PATH/shell" Bookmarks.qml
jq -e '.license == "MIT" and .homepage == "https://github.com/idr4n/omarchy-bookmarks"' manifest.json
```

## Task 6: Install locally and verify the real shell lifecycle

### Context

Marketplace readiness requires exercising the actual Omarchy clone/validate
path, not only the development symlink. A local Git URL installs committed
`HEAD`, so checkpoint only after Tasks 1–5 and remove the dev link first.

### Change

- Remove the development-linked plugin with
  `omarchy plugin remove io.github.idr4n.bookmarks --yes`; verify that only the
  symlink disappeared and the repository remains.
- Load and follow the commit workflow, create a reviewed local checkpoint, then
  install it with `omarchy plugin add "file://$PWD" --enable --yes`.
- Confirm discovery, enabled state, manifest source, and startup-mounted
  `keepLoaded` behavior.
- Exercise summon/hide/search-toggle/add-summon, keyboard and pointer paths,
  malformed-data guard and recovery, own/external atomic reload, write failure
  recovery, shell restart, disable/re-enable, and plugin removal.
- Confirm the plugin performs no network request and starts no resident helper
  process.
- Confirm removal leaves XDG data/state intact. Remove only checksum-verified
  synthetic fixture documents, or restore pre-test backups, so Task 7 starts
  with an absent or empty real destination; then reinstall for migration.

### Acceptance Criteria

- Cold shell startup and repeated summons expose the correct overlay without a
  second Quickshell process.
- Search, add, in-place mode switch, duplicate, open, reload, repair, restart,
  disable/re-enable, and removal behave as specified on the actual desktop.
- Search self-close followed by one `toggle` reopens immediately.
- Errors stay within the plugin surface and do not destabilize `omarchy-shell`.
- Plugin removal removes source only; bookmark data remains until the explicit
  checksum-guarded fixture cleanup.
- Before Task 7, the real bookmarks destination is absent or a valid empty
  schema-1 document and no synthetic recent state remains.

### Verify

```sh
cd ~/Dropbox/Dev/omarchy-bookmarks
omarchy plugin remove io.github.idr4n.bookmarks --yes
test ! -e "$HOME/.config/omarchy/plugins/io.github.idr4n.bookmarks" && test ! -L "$HOME/.config/omarchy/plugins/io.github.idr4n.bookmarks"
omarchy plugin add "file://$PWD" --enable --yes
omarchy plugin list --json | jq -e '.[] | select(.id == "io.github.idr4n.bookmarks" and .enabled == true)'
omarchy-shell shell toggle io.github.idr4n.bookmarks '{}'
omarchy-shell shell hide io.github.idr4n.bookmarks
omarchy-shell shell summon io.github.idr4n.bookmarks '{"mode":"add"}'
omarchy plugin disable io.github.idr4n.bookmarks
omarchy plugin enable io.github.idr4n.bookmarks
```

Complete the remaining acceptance paths interactively in the actual overlay.
Test plugin removal and retained data, then perform only the verified fixture
cleanup described above before reinstalling.

## Task 7: Migrate data and perform the dotfiles cutover

### Context

This is the only task allowed to modify `/home/iduran/dotfiles`. Start only when
no other agent is actively changing that worktree; block and coordinate if the
prayers agent is still active. The old picker remains the rollback path until
import and plugin smoke tests pass. Any cutover commit stages explicit bookmark
paths only—never `git add -A`.

### Change

- Record the legacy file checksum and make a dated backup without changing the
  source.
- Confirm the real destination
  `${XDG_DATA_HOME:-$HOME/.local/share}/io.github.idr4n.bookmarks/bookmarks.json`
  is absent or a valid empty document. Remove only the known development
  fixture after checksum verification; never delete unrecognized user data.
- Run importer dry run, import into that real XDG data path, verify aggregate
  counts, then summon/search/open/add through the installed plugin.
- Change both bookmark bindings in
  `.config/hypr/common/bindings-scripts.lua` and
  `.config/hypr/common/bindings-scripts.conf`: search uses `shell toggle` and
  add uses `shell summon`, preserving the existing key combinations and
  descriptions.
- Add the `idr4n-bookmarks` no-animation layer rule to the active
  `.config/hypr/common/looknfeel.lua`; remove the obsolete
  `dotfiles-bookmarks` rule from `.config/hypr/profiles/arch/looknfeel.lua`.
- Reload Hyprland and verify `hyprctl configerrors` is empty before cleanup.
- Remove every obsolete bookmark-only implementation/caller after the new
  bindings pass: `scripts/bookmarks`, `scripts/bookmarks-json`,
  `scripts/bookmarks-rofi`,
  `.config/quickshell/plain-arch/widgets/BookmarksOverlay.qml`, and bookmark
  properties, visibility checks, close/reset logic, mode functions, IPC
  handler, and instantiated overlay in `.config/quickshell/plain-arch/shell.qml`.
- Inspect and update active guidance/references in
  `.config/quickshell/plain-arch/README.md`, `setup/README.md`,
  `docs/roadmap.md`, `docs/plans/plain-arch-quickshell.md`, and
  `docs/plans/plain-arch-quickshell-island-theme.md`. Historical context may
  remain only when clearly historical; no obsolete command remains as current
  guidance.
- Leave `~/.cache/bookmarks_recent` unmigrated as harmless legacy cache and
  document that decision; never treat it as canonical data.
- Search for remaining old command, IPC target, QML file, namespace, and
  legacy-path references. Remove active aliases and fallbacks.
- Do not alter or stage any prayers file, symbol, binding, test, documentation,
  or work in progress.

### Acceptance Criteria

- Real XDG data contains exactly 350 migrated records, 121 tagged records, and
  170 tags before any new manual add.
- The legacy source and backup checksums match the pre-migration checksum.
- `Ctrl+Alt+Space` opens search on the first press and `Ctrl+Alt+B` opens or
  switches to add mode through the installed plugin.
- `hyprctl -j layers` reports namespace `idr4n-bookmarks`; observed open/close
  has no compositor fade.
- No old bookmark script, plain-Arch overlay, IPC target, active namespace rule,
  or caller remains in dotfiles.
- Hyprland reports no config errors and unrelated picker/prayers behavior is
  unchanged.
- Any cutover commit stages only the enumerated bookmark-owned paths.

### Verify

```sh
cd /home/iduran/dotfiles
hyprctl reload
hyprctl configerrors
hyprctl -j layers | jq -e '.. | objects | select(.namespace? == "idr4n-bookmarks")'
```

Also run the focused dotfiles setup checks covering changed binding and
plain-Arch shell files, invoke both real keybindings, and use exact repository
searches to prove the obsolete bookmark implementation has no active callers.
Keep the legacy data file and backup as rollback data; do not delete either.

## Task 8: Publish and submit to the marketplace

### Context

Publish only the implementation already exercised through the plugin manager.
The marketplace validates listings, not security; the repository documentation
must remain the authoritative permission and data-lifecycle disclosure.
Marketplace submission is bound to an exact commit and requires owner approval
of the ownership/checklist statement.

### Change

- Re-run the full clean-checkout verification matrix and review the final diff.
- Create the public `idr4n/omarchy-bookmarks` GitHub repository, push the
  reviewed main branch, and install once from the public HTTPS Git URL.
- Confirm the root manifest includes the permanent ID, version, MIT license,
  and homepage and that README documents install, remove, dependencies, storage,
  permissions, and retained data.
- Tag the manifest version only after the public install passes.
- Prepare the exact marketplace issue title/body for category `Productivity`
  and allowed tags `launcher, quickshell`. Show the ownership statement and all
  checklist items to the owner; submit to `omacom/omarchy-plugin-marketplace`
  only after explicit approval.
- Keep marketplace copy aligned with the actual no-network, local-data behavior
  and exact reviewed commit.

### Acceptance Criteria

- `omarchy plugin add https://github.com/idr4n/omarchy-bookmarks.git --enable`
  installs, validates, enables, and opens the released plugin.
- Public source, manifest ID/version/homepage/license, tag, README, license, and
  preview agree.
- Marketplace category and tags exactly match the allowed lists.
- The approved marketplace submission links the exact reviewed commit and
  discloses storage, permissions, external dependencies, and retained data.

### Verify

```sh
fresh="$(mktemp -d)"
git clone https://github.com/idr4n/omarchy-bookmarks.git "$fresh/repo"
cd "$fresh/repo"
node tests/bookmark-model.js
python3 -m unittest discover -s tests -p 'test_bookmarkctl.py' -v
marker="$(mktemp)"
BENCHMARK_RESULT="$marker" timeout 30 qs -p scripts/bench/shell.qml || test "$?" -eq 124
test "$(cat "$marker")" = PASS
rm -f "$marker"
omarchy plugin validate .
qmllint -I "$OMARCHY_PATH/shell" Bookmarks.qml
jq -e '.id == "io.github.idr4n.bookmarks" and .license == "MIT" and .homepage == "https://github.com/idr4n/omarchy-bookmarks"' manifest.json
```

Then install from the public HTTPS URL, summon both modes, and compare the
approved issue body with the exact repository, commit, ID, category, tags, and
preview before considering publication complete.
