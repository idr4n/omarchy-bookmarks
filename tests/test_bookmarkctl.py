from __future__ import annotations

import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BOOKMARKCTL = ROOT / "bookmarkctl"
CREATED_AT = "2026-08-30T12:00:00.000Z"


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind)
    checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)
    )


def solid_png(width: int, height: int, color: bytes = b"\xff\x66\x00\xff") -> bytes:
    row = b"\0" + color * width
    payload = zlib.compress(row * height)
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", payload)
        + png_chunk(b"IEND", b"")
    )


PNG = solid_png(32, 32)
PNG_16 = solid_png(16, 16, b"\x00\x66\xff\xff")
PNG_128 = solid_png(128, 128, b"\x44\xcc\x44\xff")

loader = importlib.machinery.SourceFileLoader(
    "bookmarkctl_test_module", str(BOOKMARKCTL)
)
spec = importlib.util.spec_from_loader(loader.name, loader)
if spec is None:
    raise RuntimeError("could not load bookmarkctl")
BOOKMARKCTL_MODULE = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = BOOKMARKCTL_MODULE
loader.exec_module(BOOKMARKCTL_MODULE)


def document(bookmarks: list[dict[str, object]], **extra: object) -> dict[str, object]:
    return {"schemaVersion": 1, "bookmarks": bookmarks, **extra}


def bookmark(record_id: str, url: str, **extra: object) -> dict[str, object]:
    return {
        "id": record_id,
        "title": extra.pop("title", "Existing"),
        "url": url,
        "tags": extra.pop("tags", []),
        "createdAt": extra.pop("createdAt", CREATED_AT),
        **extra,
    }


def run_cli(
    source: Path,
    *arguments: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [str(BOOKMARKCTL), "import-legacy", str(source), *arguments]
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def run_module(*arguments: str) -> subprocess.CompletedProcess[str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        returncode = BOOKMARKCTL_MODULE.main(list(arguments))
    return subprocess.CompletedProcess(
        [str(BOOKMARKCTL), *arguments],
        returncode,
        stdout.getvalue(),
        stderr.getvalue(),
    )


def fake_favicon_fetch(
    _metadata_helper: Path,
    data_dir: Path,
    record_id: str,
    url: str,
    expected_favicon: str | None,
) -> tuple[str, str, str | None, str]:
    if url.endswith("/missing"):
        return record_id, url, expected_favicon, ""
    payload = PNG_128 if url.endswith("/refresh") else PNG
    favicon = BOOKMARKCTL_MODULE.store_icon(data_dir, url, payload, "png")
    return record_id, url, expected_favicon, favicon


def output_stats(stdout: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)


class BookmarkCtlTests(unittest.TestCase):
    def test_dry_run_reports_only_counts_and_ambiguous_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / "legacy"
            destination = work / "data" / "bookmarks.json"
            source.write_text(
                "Café #Read | https://example.com/path #After trailing prose\n"
                "https://second.example/a other https://other.example/b #Tag\n"
                "Private malformed row #secret\n",
                encoding="utf-8",
            )

            result = run_cli(source, "--dry-run", "--data-file", str(destination))

            self.assertEqual(result.returncode, 1)
            self.assertFalse(destination.exists())
            self.assertEqual(
                output_stats(result.stdout),
                {
                    "valid": "2",
                    "imported": "2",
                    "duplicate": "0",
                    "malformed": "1",
                    "missing_url": "1",
                    "ambiguous": "1",
                    "rows_with_tags": "2",
                    "tag_tokens": "3",
                    "ambiguous_lines": "2",
                },
            )
            combined = result.stdout + result.stderr
            for private_value in (
                "Café",
                "example.com",
                "second.example",
                "Private malformed",
                "secret",
            ):
                self.assertNotIn(private_value, combined)

    def test_real_import_preserves_unicode_tags_ambiguity_and_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / "legacy"
            destination = work / "private" / "bookmarks.json"
            source.write_text(
                "Café #Référence | https://example.com/path #After trailing prose (#NotTag)\n"
                "https://second.example/a other https://other.example/b #Tag\n"
                "#Before https://fallback.example/\n",
                encoding="utf-8",
            )
            checksum_before = hashlib.sha256(source.read_bytes()).hexdigest()

            result = run_cli(source, "--data-file", str(destination))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                hashlib.sha256(source.read_bytes()).hexdigest(), checksum_before
            )
            self.assertEqual(
                output_stats(result.stdout),
                {
                    "valid": "3",
                    "imported": "3",
                    "duplicate": "0",
                    "malformed": "0",
                    "missing_url": "0",
                    "ambiguous": "1",
                    "rows_with_tags": "3",
                    "tag_tokens": "4",
                    "ambiguous_lines": "2",
                },
            )

            imported = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(len(imported["bookmarks"]), 3)
            self.assertEqual(
                imported["bookmarks"][0]["title"], "Café trailing prose (#NotTag)"
            )
            self.assertEqual(imported["bookmarks"][0]["tags"], ["référence", "after"])
            self.assertEqual(
                imported["bookmarks"][1]["title"],
                "other https://other.example/b",
            )
            self.assertEqual(imported["bookmarks"][1]["tags"], ["tag"])
            self.assertEqual(imported["bookmarks"][2]["title"], "fallback.example")
            self.assertEqual(imported["bookmarks"][2]["tags"], ["before"])
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(destination.parent.stat().st_mode), 0o700)
            self.assertEqual(list(destination.parent.glob(".bookmarks-*.tmp")), [])

            second_destination = work / "second" / "bookmarks.json"
            second = run_cli(source, "--data-file", str(second_destination))
            self.assertEqual(second.returncode, 0, second.stderr)
            second_import = json.loads(second_destination.read_text(encoding="utf-8"))
            self.assertEqual(
                [record["id"] for record in imported["bookmarks"]],
                [record["id"] for record in second_import["bookmarks"]],
            )

    def test_malformed_source_leaves_destination_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / "legacy"
            destination = work / "bookmarks.json"
            source.write_text(
                "https://valid.example/ Valid\nrow without a URL\n",
                encoding="utf-8",
            )
            original = json.dumps(document([]), indent=2).encode() + b"\n"
            destination.write_bytes(original)

            result = run_cli(source, "--data-file", str(destination))

            self.assertEqual(result.returncode, 1)
            self.assertEqual(destination.read_bytes(), original)
            self.assertNotIn("row without", result.stdout + result.stderr)

    def test_import_rejects_urls_the_runtime_model_rejects(self) -> None:
        invalid_urls = (
            "https://host:/",
            "https://ho\\st/x",
            "https://host/path\x00suffix",
            "https://host/path\x01suffix",
            "https://host/path\ufeffsuffix",
            "\x00https://host/path",
        )
        for index, url in enumerate(invalid_urls):
            with (
                self.subTest(url_index=index),
                tempfile.TemporaryDirectory() as temporary,
            ):
                work = Path(temporary)
                source = work / "legacy"
                destination = work / "bookmarks.json"
                source.write_text(f"Invalid | {url}\n", encoding="utf-8")

                result = run_cli(
                    source,
                    "--dry-run",
                    "--data-file",
                    str(destination),
                )

                self.assertEqual(result.returncode, 1)
                self.assertEqual(output_stats(result.stdout)["valid"], "0")
                self.assertEqual(output_stats(result.stdout)["malformed"], "1")
                self.assertFalse(destination.exists())

    def test_import_enforces_source_destination_depth_and_count_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / "legacy"
            destination = work / "bookmarks.json"

            source.write_bytes(b"x" * (BOOKMARKCTL_MODULE.LEGACY_FILE_LIMIT + 1))
            oversized_source = run_cli(
                source,
                "--data-file",
                str(destination),
            )
            self.assertEqual(oversized_source.returncode, 1)
            self.assertIn(
                "legacy source exceeds the byte limit", oversized_source.stderr
            )
            self.assertFalse(destination.exists())

            source.write_text("Valid | https://valid.example/\n", encoding="utf-8")
            destination.write_bytes(b" " * (BOOKMARKCTL_MODULE.BOOKMARK_FILE_LIMIT + 1))
            oversized_destination_before = destination.read_bytes()
            oversized_destination = run_cli(
                source,
                "--merge",
                "--data-file",
                str(destination),
            )
            self.assertEqual(oversized_destination.returncode, 1)
            self.assertIn(
                "destination exceeds the byte limit", oversized_destination.stderr
            )
            self.assertEqual(destination.read_bytes(), oversized_destination_before)

            nested: object = "leaf"
            for _index in range(BOOKMARKCTL_MODULE.JSON_DEPTH_LIMIT + 1):
                nested = {"nested": nested}
            destination.write_text(
                json.dumps(document([], future=nested)),
                encoding="utf-8",
            )
            deeply_nested_before = destination.read_bytes()
            deeply_nested = run_cli(
                source,
                "--merge",
                "--data-file",
                str(destination),
            )
            self.assertEqual(deeply_nested.returncode, 1)
            self.assertIn("JSON depth limit", deeply_nested.stderr)
            self.assertEqual(destination.read_bytes(), deeply_nested_before)

            too_many = document(
                [
                    bookmark(
                        f"id-{index}",
                        f"https://count.example/{index}",
                        title="",
                    )
                    for index in range(BOOKMARKCTL_MODULE.BOOKMARK_LIMIT + 1)
                ]
            )
            count_payload = json.dumps(too_many, separators=(",", ":")).encode("utf-8")
            self.assertLess(len(count_payload), BOOKMARKCTL_MODULE.BOOKMARK_FILE_LIMIT)
            destination.write_bytes(count_payload)
            too_many_before = destination.read_bytes()
            too_many_result = run_cli(
                source,
                "--merge",
                "--data-file",
                str(destination),
            )
            self.assertEqual(too_many_result.returncode, 1)
            self.assertIn("exceeds 5000 bookmarks", too_many_result.stderr)
            self.assertEqual(destination.read_bytes(), too_many_before)

    def test_helper_process_output_and_runtime_are_bounded(self) -> None:
        completed = BOOKMARKCTL_MODULE.run_bounded_process(
            [
                sys.executable,
                "-c",
                "import sys;sys.stdout.buffer.write(b'o'*512);sys.stderr.buffer.write(b'e'*512)",
            ],
            timeout=2,
            output_limit=1024,
        )
        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertEqual(len(completed.stdout), 512)
        self.assertEqual(len(completed.stderr), 512)

        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream):
                oversized = BOOKMARKCTL_MODULE.run_bounded_process(
                    [
                        sys.executable,
                        "-c",
                        f"import sys;sys.{stream}.buffer.write(b'x'*1025)",
                    ],
                    timeout=2,
                    output_limit=1024,
                )
                self.assertIsNone(oversized)
        self.assertIsNone(
            BOOKMARKCTL_MODULE.run_bounded_process(
                [sys.executable, "-c", "import time;time.sleep(2)"],
                timeout=0.05,
                output_limit=1024,
            )
        )

    def test_atomic_write_refuses_aggregate_output_over_limit(self) -> None:
        large_document = document(
            [
                bookmark(
                    f"id-{index}",
                    f"https://aggregate.example/{index}",
                    title="t" * BOOKMARKCTL_MODULE.TITLE_LIMIT,
                )
                for index in range(2000)
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "data" / "bookmarks.json"
            with self.assertRaisesRegex(
                BOOKMARKCTL_MODULE.ImportFailure,
                "destination exceeds the byte limit",
            ):
                BOOKMARKCTL_MODULE.atomic_write(destination, large_document)
            self.assertFalse(destination.exists())

    def test_nonempty_destination_requires_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / "legacy"
            destination = work / "bookmarks.json"
            source.write_text("New | https://new.example/\n", encoding="utf-8")
            original = (
                json.dumps(
                    document([bookmark("existing", "https://existing.example/")]),
                    indent=2,
                ).encode()
                + b"\n"
            )
            destination.write_bytes(original)

            result = run_cli(source, "--data-file", str(destination))

            self.assertEqual(result.returncode, 1)
            self.assertIn("use --merge", result.stderr)
            self.assertEqual(destination.read_bytes(), original)

    def test_merge_is_idempotent_and_preserves_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / "legacy"
            destination = work / "private" / "bookmarks.json"
            destination.parent.mkdir()
            source.write_text(
                "Duplicate | HTTPS://Existing.Example/Path\n"
                "Fresh #Tag | https://fresh.example/new\n",
                encoding="utf-8",
            )
            original_document = document(
                [
                    bookmark(
                        "existing",
                        "https://existing.example/Path",
                        future={"keep": True},
                    )
                ],
                futureTop="keep",
            )
            destination.write_text(
                json.dumps(original_document, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            first = run_cli(source, "--merge", "--data-file", str(destination))

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(output_stats(first.stdout)["imported"], "1")
            self.assertEqual(output_stats(first.stdout)["duplicate"], "1")
            merged = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(merged["futureTop"], "keep")
            self.assertEqual(merged["bookmarks"][0]["future"], {"keep": True})
            self.assertEqual(len(merged["bookmarks"]), 2)
            self.assertEqual(merged["bookmarks"][1]["tags"], ["tag"])
            first_bytes = destination.read_bytes()

            second = run_cli(source, "--merge", "--data-file", str(destination))

            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(output_stats(second.stdout)["imported"], "0")
            self.assertEqual(output_stats(second.stdout)["duplicate"], "2")
            self.assertEqual(destination.read_bytes(), first_bytes)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(destination.parent.stat().st_mode), 0o700)

    def test_invalid_destination_schema_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / "legacy"
            destination = work / "bookmarks.json"
            source.write_text("Valid | https://valid.example/\n", encoding="utf-8")
            original = b'{"schemaVersion":2,"bookmarks":[]}\n'
            destination.write_bytes(original)

            result = run_cli(source, "--merge", "--data-file", str(destination))

            self.assertEqual(result.returncode, 1)
            self.assertIn("unsupported schema", result.stderr)
            self.assertEqual(destination.read_bytes(), original)

    def test_destination_normalizes_runtime_compatible_optional_fields(self) -> None:
        stored = document(
            [
                bookmark(
                    "existing",
                    "https://existing.example/",
                    tags=[" Foo ", "#Bar", "foo", ""],
                    favicon=None,
                )
            ]
        )

        validated = BOOKMARKCTL_MODULE.validate_destination(stored)

        self.assertIs(validated, stored)
        self.assertEqual(validated["bookmarks"][0]["tags"], ["foo", "bar"])
        self.assertNotIn("favicon", validated["bookmarks"][0])

    def test_destination_rejects_boolean_schema_version(self) -> None:
        stored = {"schemaVersion": True, "bookmarks": []}

        with self.assertRaisesRegex(
            BOOKMARKCTL_MODULE.ImportFailure,
            "unsupported schema",
        ):
            BOOKMARKCTL_MODULE.validate_destination(stored)

    def test_destination_rejects_c1_url_control(self) -> None:
        stored = document(
            [bookmark("controlled", "https://control.example/path\x85suffix")]
        )

        with self.assertRaisesRegex(
            BOOKMARKCTL_MODULE.ImportFailure,
            "invalid bookmark URL",
        ):
            BOOKMARKCTL_MODULE.validate_destination(stored)

    def test_default_destination_ignores_relative_xdg_data_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / "legacy"
            source.write_text("Default | https://default.example/\n", encoding="utf-8")
            env = os.environ.copy()
            env["HOME"] = str(work / "home")
            env["XDG_DATA_HOME"] = "relative-data"

            result = run_cli(source, env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            expected = (
                work
                / "home"
                / ".local"
                / "share"
                / "io.github.idr4n.bookmarks"
                / "bookmarks.json"
            )
            self.assertTrue(expected.is_file())
            self.assertFalse((work / "home" / ".local" / "share" / "omarchy").exists())

    def test_destination_symlink_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / "legacy"
            target = work / "target.json"
            destination = work / "bookmarks.json"
            source.write_text("Valid | https://valid.example/\n", encoding="utf-8")
            target.write_text(json.dumps(document([])) + "\n", encoding="utf-8")
            destination.symlink_to(target)
            original = target.read_bytes()

            result = run_cli(source, "--data-file", str(destination))

            self.assertEqual(result.returncode, 1)
            self.assertIn("must not be a symlink", result.stderr)
            self.assertEqual(target.read_bytes(), original)

    def test_normalize_favicons_migrates_legacy_cache_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            legacy_dir = data_dir / "favicons"
            legacy_dir.mkdir(parents=True)
            destination = data_dir / "bookmarks.json"
            url = "https://migration.example/page"
            legacy_favicon = f"favicons/{'c' * 64}.png"
            (data_dir / legacy_favicon).write_bytes(PNG)
            original = document(
                [
                    bookmark(
                        "migration",
                        url,
                        favicon=legacy_favicon,
                        futureData={"keep": True},
                    )
                ],
                futureTopLevel="preserved",
            )
            destination.write_text(
                json.dumps(original, indent=2) + "\n",
                encoding="utf-8",
            )
            original_bytes = destination.read_bytes()

            dry_run = run_module(
                "normalize-favicons",
                "--dry-run",
                "--data-file",
                str(destination),
            )
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            self.assertEqual(
                output_stats(dry_run.stdout),
                {
                    "legacy": "1",
                    "scheduled": "1",
                    "processed": "0",
                    "normalized": "0",
                    "failed": "0",
                    "applied": "0",
                    "skipped": "0",
                },
            )
            self.assertEqual(destination.read_bytes(), original_bytes)

            with mock.patch.object(
                BOOKMARKCTL_MODULE,
                "normalize_raster",
                return_value=PNG,
            ) as decoder:
                result = run_module(
                    "normalize-favicons",
                    "--data-file",
                    str(destination),
                )

            self.assertEqual(result.returncode, 0, result.stderr)
            decoder.assert_called_once()
            self.assertEqual(
                output_stats(result.stdout),
                {
                    "legacy": "1",
                    "scheduled": "1",
                    "processed": "1",
                    "normalized": "1",
                    "failed": "0",
                    "applied": "1",
                    "skipped": "0",
                },
            )
            saved = json.loads(destination.read_text(encoding="utf-8"))
            migrated = saved["bookmarks"][0]
            self.assertRegex(
                migrated["favicon"],
                r"^favicons-v2/[0-9a-f]{64}\.png$",
            )
            self.assertEqual(migrated["futureData"], {"keep": True})
            self.assertEqual(saved["futureTopLevel"], "preserved")
            self.assertFalse((data_dir / legacy_favicon).exists())
            self.assertIsNotNone(
                BOOKMARKCTL_MODULE.read_cached_icon(data_dir, migrated["favicon"])
            )

    def test_backfill_favicons_is_explicit_bounded_and_partial_failure_safe(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            data_dir.mkdir()
            destination = data_dir / "bookmarks.json"
            existing_favicon = f"favicons-v2/{'a' * 64}.png"
            destination.write_text(
                json.dumps(
                    document(
                        [
                            bookmark(
                                "fetch",
                                "https://fetch.example/ok",
                                title="Preserve this title",
                                futureData={"keep": True},
                            ),
                            bookmark("fail", "https://fetch.example/missing"),
                            bookmark(
                                "existing",
                                "https://fetch.example/already",
                                favicon=existing_favicon,
                            ),
                        ],
                        futureTopLevel="preserved",
                    ),
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            dry_run = run_module(
                "backfill-favicons",
                "--dry-run",
                "--data-file",
                str(destination),
            )
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            self.assertEqual(
                output_stats(dry_run.stdout),
                {
                    "missing": "2",
                    "scheduled": "2",
                    "processed": "0",
                    "fetched": "0",
                    "failed": "0",
                    "applied": "0",
                    "skipped": "0",
                },
            )

            with mock.patch.object(
                BOOKMARKCTL_MODULE,
                "fetch_favicon",
                side_effect=fake_favicon_fetch,
            ) as fetch:
                result = run_module(
                    "backfill-favicons",
                    "--workers",
                    "2",
                    "--data-file",
                    str(destination),
                )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(
                output_stats(result.stdout),
                {
                    "missing": "2",
                    "scheduled": "2",
                    "processed": "2",
                    "fetched": "1",
                    "failed": "1",
                    "applied": "1",
                    "skipped": "0",
                },
            )
            saved = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(saved["futureTopLevel"], "preserved")
            self.assertEqual(saved["bookmarks"][0]["title"], "Preserve this title")
            self.assertEqual(saved["bookmarks"][0]["futureData"], {"keep": True})
            favicon = saved["bookmarks"][0]["favicon"]
            self.assertRegex(favicon, r"^favicons-v2/[0-9a-f]{64}\.png$")
            self.assertEqual((data_dir / favicon).read_bytes(), PNG)
            self.assertNotIn("favicon", saved["bookmarks"][1])
            self.assertEqual(saved["bookmarks"][2]["favicon"], existing_favicon)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((data_dir / favicon).stat().st_mode), 0o600)

    def test_refresh_favicons_only_installs_strictly_larger_icons(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            cache_dir = data_dir / "favicons-v2"
            cache_dir.mkdir(parents=True)
            destination = data_dir / "bookmarks.json"
            refresh_url = "https://refresh.example/refresh"
            stable_url = "https://refresh.example/ok"
            old_small = f"favicons-v2/{'a' * 64}.png"
            old_stable = f"favicons-v2/{'b' * 64}.png"
            (data_dir / old_small).write_bytes(PNG_16)
            (data_dir / old_stable).write_bytes(PNG_128)
            original = document(
                [
                    bookmark(
                        "refresh",
                        refresh_url,
                        favicon=old_small,
                        futureData={"keep": True},
                    ),
                    bookmark("stable", stable_url, favicon=old_stable),
                ],
                futureTopLevel="preserved",
            )
            destination.write_text(
                json.dumps(original, indent=2) + "\n",
                encoding="utf-8",
            )

            dry_run = run_module(
                "refresh-favicons",
                "--dry-run",
                "--data-file",
                str(destination),
            )
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            self.assertEqual(
                output_stats(dry_run.stdout),
                {
                    "missing": "0",
                    "scheduled": "2",
                    "processed": "0",
                    "fetched": "0",
                    "failed": "0",
                    "applied": "0",
                    "skipped": "0",
                },
            )

            with mock.patch.object(
                BOOKMARKCTL_MODULE,
                "fetch_favicon",
                side_effect=fake_favicon_fetch,
            ):
                result = run_module(
                    "refresh-favicons",
                    "--workers",
                    "2",
                    "--data-file",
                    str(destination),
                )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                output_stats(result.stdout),
                {
                    "missing": "0",
                    "scheduled": "2",
                    "processed": "2",
                    "fetched": "2",
                    "failed": "0",
                    "applied": "1",
                    "skipped": "1",
                },
            )
            saved = json.loads(destination.read_text(encoding="utf-8"))
            refreshed = saved["bookmarks"][0]
            stable = saved["bookmarks"][1]
            expected_digest = hashlib.sha256(
                refresh_url.encode("utf-8") + b"\0" + PNG_128
            ).hexdigest()
            expected_favicon = f"favicons-v2/{expected_digest}.png"
            self.assertEqual(refreshed["favicon"], expected_favicon)
            self.assertEqual(refreshed["futureData"], {"keep": True})
            self.assertEqual((data_dir / expected_favicon).read_bytes(), PNG_128)
            self.assertFalse((data_dir / old_small).exists())
            self.assertEqual(stable["favicon"], old_stable)
            self.assertEqual((data_dir / old_stable).read_bytes(), PNG_128)
            self.assertEqual(saved["futureTopLevel"], "preserved")
            self.assertEqual(list(data_dir.glob(".favicon-refresh-*")), [])
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
            self.assertEqual(
                stat.S_IMODE((data_dir / expected_favicon).stat().st_mode),
                0o600,
            )

    def test_backfill_interrupt_cancels_queued_fetches(self) -> None:
        class FakeExecutor:
            def __init__(self) -> None:
                self.futures: list[Future[object]] = []
                self.shutdown_arguments: tuple[bool, bool] | None = None

            def submit(self, *_arguments: object) -> Future[object]:
                future: Future[object] = Future()
                self.futures.append(future)
                return future

            def shutdown(self, wait: bool, cancel_futures: bool) -> None:
                self.shutdown_arguments = wait, cancel_futures
                if cancel_futures:
                    for future in self.futures:
                        future.cancel()

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "data" / "bookmarks.json"
            destination.parent.mkdir()
            original = document(
                [
                    bookmark(
                        f"slow-{index}",
                        f"https://slow.example/{index}",
                    )
                    for index in range(20)
                ]
            )
            destination.write_text(
                json.dumps(original, indent=2) + "\n",
                encoding="utf-8",
            )
            executor = FakeExecutor()
            with (
                mock.patch.object(
                    BOOKMARKCTL_MODULE.concurrent.futures,
                    "ThreadPoolExecutor",
                    return_value=executor,
                ),
                mock.patch.object(
                    BOOKMARKCTL_MODULE.concurrent.futures,
                    "as_completed",
                    side_effect=KeyboardInterrupt,
                ),
            ):
                result = run_module(
                    "backfill-favicons",
                    "--workers",
                    "1",
                    "--data-file",
                    str(destination),
                )

            self.assertEqual(result.returncode, 130, result.stderr)
            self.assertIn("interrupted", result.stderr)
            self.assertEqual(output_stats(result.stdout)["scheduled"], "20")
            self.assertEqual(executor.shutdown_arguments, (True, True))
            self.assertEqual(len(executor.futures), 20)
            self.assertTrue(all(future.cancelled() for future in executor.futures))
            self.assertEqual(
                json.loads(destination.read_text(encoding="utf-8")),
                original,
            )
            self.assertEqual(
                list(destination.parent.glob(".favicon-refresh-*")),
                [],
            )


if __name__ == "__main__":
    unittest.main()
