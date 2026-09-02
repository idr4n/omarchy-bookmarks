# Local Bookmarks

A resident bookmark search and capture overlay for Omarchy Quattro. The plugin loads once with `omarchy-shell`, searches local data in memory, opens URLs through an argv-safe `omarchy-launch-browser` call, and stores bookmarks as portable JSON.

## Preview

Search a local bookmark library with real site favicons:

![Search bookmarks](preview.png)

Capture a URL, review fetched page details, and add optional tags:

![Add a bookmark](preview-add.png)

## Features

- Search title, URL, derived domain, and tags while typing.
- Weighted exact, prefix, and substring ranking with bounded recency tie-breaking.
- At most 60 rendered results; no disk query or subprocess per keystroke.
- Add an HTTP(S) URL with an optional editable title and comma-separated tags.
- Extract the first HTTP(S) URL from pasted text and use the surrounding text as the title, including `Title | URL` lines.
- For URL-only input, fetch the Open Graph or HTML title and fall back to the URL host.
- Fetch and privately cache supported favicons for local-only rendering in search results.
- Edit title, URL, and tags with duplicate validation; changing URL refreshes metadata.
- Delete from search or edit mode, after a named confirmation.
- Detect duplicate URLs without rewriting the stored path, query, or fragment.
- Watch atomic external updates to bookmark and recent-state files.
- Keep malformed or unsupported bookmark data read-only and show the error in the overlay.
- Run without a helper daemon, second Quickshell process, runtime database, package manager, or install hook.

Folders, remote sync, notes, bulk operations, JavaScript-rendered metadata, authenticated metadata requests, and sharing are intentionally out of scope.

## Requirements

A stock Omarchy install already provides everything the plugin uses, so there is nothing to install by hand:

- Omarchy Quattro with manifest schema 1 and Quickshell;
- a POSIX shell and coreutils (`install`, `chmod`, `wc`, and `head`) for private storage and bounded reads;
- `omarchy-launch-browser` for opening URLs;
- Python 3 for bounded, one-shot page metadata and favicon cache operations, and for the optional text-file importer;
- ImageMagick 7 (`magick`) for resource-limited raster favicon decoding;
- librsvg's `rsvg-convert` for SVG favicons. If it is ever missing, SVG candidates are skipped and the site's raster icons are used instead.

Python, ImageMagick, and librsvg all arrive with Omarchy's base packages. There is no package-manager or third-party Python dependency, no install hook, and no setup step after `omarchy plugin add`.

## Install

```sh
omarchy plugin add https://github.com/idr4n/omarchy-bookmarks.git --enable --yes
```

The plugin ID is `io.github.idr4n.bookmarks`.

## Commands and keybindings

Search toggles the overlay. Invoking it again while visible hides it:

```sh
omarchy-shell shell toggle io.github.idr4n.bookmarks '{}'
```

Add opens the overlay directly in capture mode, or switches an already-open search overlay into capture mode:

```sh
omarchy-shell shell summon io.github.idr4n.bookmarks '{"mode":"add"}'
```

The plugin does not install keybindings. Recommended bindings are:

| Key | Command |
| --- | --- |
| `Ctrl+Alt+Space` | search toggle command above |
| `Ctrl+Alt+B` | add summon command above |

Search controls:

- `Up` / `Down` or `Ctrl+K` / `Ctrl+J`: select a result
- `Enter`: open the selected bookmark
- `Ctrl+E`: edit the selected bookmark
- `Delete`: confirm removal of the selected bookmark
- `Ctrl+Enter`: switch to add mode
- `Escape`: clear a non-empty query, then close
- Pointer click: select and open a result

Add and edit controls:

- Paste text anywhere in the URL field; the first HTTP(S) URL is extracted
- `Ctrl+Enter`: save immediately; any in-flight metadata fetch is cancelled, and an empty title falls back to the URL host
- `Delete…` in edit mode: open per-entry confirmation
- `Escape`: dismiss confirmation, then return to search

### Disable compositor animation

The layer-shell namespace is `idr4n-bookmarks`. Add a matching rule to the active Hyprland configuration so compositor fades do not mask the fast open path:

```ini
layerrule {
    name = no-anim-idr4n-bookmarks
    match:namespace = idr4n-bookmarks
    no_anim = true
    animation = none
}
```

Omarchy Lua configurations can express the same rule as:

```lua
hl.layer_rule({
  name = "no-anim-idr4n-bookmarks",
  match = { namespace = "idr4n-bookmarks" },
  no_anim = true,
  animation = "none",
})
```

## Data, privacy, and permissions

Bookmarks, cached favicons, and recency are deliberately separate:

```text
${XDG_DATA_HOME:-$HOME/.local/share}/io.github.idr4n.bookmarks/bookmarks.json
${XDG_DATA_HOME:-$HOME/.local/share}/io.github.idr4n.bookmarks/favicons-v2/
${XDG_STATE_HOME:-$HOME/.local/state}/io.github.idr4n.bookmarks/recent.json
```

Data/state/cache directories use mode `0700`; JSON and cached favicon files use mode `0600`. Plugin writes reapply private file modes. Mutable data is never stored in the installed Git checkout. Storage initialization failures are visible errors and never trigger privilege escalation or a fallback to a packaged path.

Search, opening, and usage tracking send nothing over the network. Entering a valid URL in add mode—or changing it in edit mode—starts one Python helper. It has a shared five-second request budget, follows at most three HTTP redirects, reads at most 1 MiB of HTML and 256 KiB per icon candidate, and accepts only HTTP(S). Every redirect hop is resolved separately, private or otherwise non-global addresses are discarded, and the connection is pinned to one validated numeric address while retaining the original host for HTTP and TLS. Proxy environment variables are not used.

The helper prefers scalable, explicitly large, and touch-icon candidates over small document-order favicons. PNG/JPEG/GIF/ICO/WebP candidates are decoded as a forced input format and first frame in a resource-limited ImageMagick subprocess, resized to at most 128 pixels, stripped, and re-encoded as a static PNG. SVG candidates receive equivalent process limits through `rsvg-convert` when available. Only validated normalized PNGs enter `favicons-v2/`, and only those files are ever rendered. The helper sends no browser cookies, credentials, or bookmark collection, never bypasses invalid TLS or authentication, and never queries a third-party favicon service. Metadata failure is visible but never blocks saving a valid bookmark. Pasted title text remains authoritative. QML and CLI collectors stop helpers whose stdout or stderr exceeds 64 KiB.

`bookmarks.json` is limited to 1 MiB, 5,000 bookmarks, and JSON depth 32. Per record: IDs are at most 128 Unicode code points, titles 512, URLs 2,048, and tags 32 values of at most 64 each. `recent.json` is limited to 64 KiB and contains at most ten bookmark IDs, newest first. Files that exceed these limits are rejected before parsing and never overwritten. Deletion removes its recency entry and unreferenced cached favicon; cancellation also cleans an unreferenced fetched icon.

Back up `bookmarks.json` as ordinary portable user data. Include `favicons-v2/` only if you want to preserve the optional icon cache. Treat `recent.json` as disposable local application state.

### Multi-machine sync

With Syncthing, share the plugin data directory itself on every machine:

```text
${XDG_DATA_HOME:-$HOME/.local/share}/io.github.idr4n.bookmarks/
```

This keeps `bookmarks.json` and its deterministic `favicons-v2/` cache together while leaving device-local `recent.json` unsynced. Use the same Syncthing folder ID and the corresponding local data path on each device; enable versioning on every receiving device. Do not move the data file into another synced tree and symlink it back—the plugin deliberately rejects mutable-data symlinks.

Atomic replacements are Syncthing-friendly, but `bookmarks.json` is still one file rather than a record-level sync protocol. Avoid editing bookmarks on two disconnected machines at the same time. If Syncthing creates a `.sync-conflict-*.json` file, preserve both copies and merge them deliberately; the plugin never guesses which concurrent edit should win.

## Import bookmarks from a text file

`bookmarkctl` can import a plain UTF-8 text list with one bookmark per line: the first HTTP(S) URL on a line is the destination, standalone `#tag` words become tags, and the remaining text becomes the title. The source file is left untouched.

Review a dry run first:

```sh
./bookmarkctl import-legacy /path/to/bookmarks.txt --dry-run
```

Import into the default data path:

```sh
./bookmarkctl import-legacy /path/to/bookmarks.txt
```

If the plugin already created an empty bookmark file, or you want to merge into existing bookmarks, opt in explicitly:

```sh
./bookmarkctl import-legacy /path/to/bookmarks.txt --merge
```

Use `--data-file PATH` to select another destination. Input is capped at 4 MiB; the destination uses the same 1 MiB, 5,000-record, depth, and field limits as the overlay. Writes use a same-directory temporary file, `fsync`, atomic replacement, and private permissions. Malformed source rows refuse the write. Output is aggregate-only; it reports line numbers only for ambiguous multiple-URL rows.

## Favicon maintenance

Icons are fetched when you add a bookmark or change its URL. Imported bookmarks start without icons, and sites that were unreachable stay blank. After explicitly approving network access, inspect and fetch the missing ones:

```sh
./bookmarkctl backfill-favicons --dry-run
./bookmarkctl backfill-favicons --workers 4
```

Backfill runs independently of the resident overlay, uses at most eight concurrent workers, and gives each bookmark one five-second budget shared by its page and icon requests. Successful icons are checkpointed atomically in batches while failed sites remain unchanged, so the command is safe to rerun. It never replaces titles or existing icons. Use `--limit N` for a smaller batch. `Ctrl+C` cancels queued requests, checkpoints results already collected by the command, and exits after at most the already-running workers finish.

To replace low-resolution icons without degrading working ones:

```sh
./bookmarkctl refresh-favicons --dry-run
./bookmarkctl refresh-favicons --workers 4
```

Refresh downloads icons into a private temporary directory, measures both PNGs, and installs a result only when it is larger than the current icon or the current file is missing. New files use a URL-and-content-derived name so the running shell sees a changed source path; the old file is removed only after the JSON update succeeds and nothing references it. Titles, URLs, tags, timestamps, unknown fields, and failed fetches remain unchanged.

Avoid saving bookmark edits in the overlay while backfill or refresh is active. Both writers make valid atomic whole-file replacements and the command reloads current data before each checkpoint, but they do not share an interprocess lock; a save in the checkpoint's final read/write window can win or lose as one whole file.

## Contributing

Contributor checks and release validation live in [CONTRIBUTING.md](CONTRIBUTING.md).

## Disable, update, and remove

```sh
omarchy plugin disable io.github.idr4n.bookmarks
omarchy plugin enable io.github.idr4n.bookmarks
omarchy plugin update io.github.idr4n.bookmarks --yes
omarchy plugin remove io.github.idr4n.bookmarks --yes
```

Removal deletes the installed plugin source. It intentionally retains the XDG bookmark data, favicon cache, and recent state; delete those paths separately only when you intend to delete your bookmarks, cached icons, and recent history.

## License

MIT. See [LICENSE](LICENSE).

Third-party website names and favicons shown in the preview images remain the property of their respective owners and are included only as bookmark-rendering examples.
