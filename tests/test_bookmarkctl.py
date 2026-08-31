from __future__ import annotations

import hashlib
import json
import os
import signal
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parents[1]
BOOKMARKCTL = ROOT / "bookmarkctl"
CREATED_AT = "2026-08-30T12:00:00.000Z"
PNG = b"\x89PNG\r\n\x1a\n" + b"backfill-fixture"
PNG_16 = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (16).to_bytes(4, "big") * 2
PNG_128 = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (128).to_bytes(4, "big") * 2


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


def run_backfill(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(BOOKMARKCTL), "backfill-favicons", *arguments],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )


def run_refresh(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(BOOKMARKCTL), "refresh-favicons", *arguments],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )


def output_stats(stdout: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)


class FaviconFixtureHandler(BaseHTTPRequestHandler):
    paths: ClassVar[list[str]] = []
    lock: ClassVar[threading.Lock] = threading.Lock()
    slow_started: ClassVar[threading.Event] = threading.Event()

    def do_GET(self) -> None:
        with self.lock:
            self.paths.append(self.path)
        if self.path.startswith("/slow/"):
            self.slow_started.set()
            time.sleep(1)
            self.send_payload(
                b"<html><head><title>Slow</title></head></html>", "text/html"
            )
            return
        if self.path == "/refresh":
            self.send_payload(
                b"<html><head><title>Refresh</title>"
                b"<link rel='icon' sizes='128x128' href='/refresh-large.png'>"
                b"</head></html>",
                "text/html",
            )
            return
        if self.path == "/ok":
            self.send_payload(
                b"<html><head><title>Fetched title</title>"
                b"<link rel='icon' href='/icon.png'></head></html>",
                "text/html",
            )
        elif self.path == "/icon.png":
            self.send_payload(PNG, "image/png")
        elif self.path == "/refresh-large.png":
            self.send_payload(PNG_128, "image/png")
        else:
            self.send_error(404)

    def send_payload(self, payload: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_arguments: object) -> None:
        return


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
            "https://host/path\x01suffix",
            "https://host/path\ufeffsuffix",
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

    def test_backfill_favicons_is_explicit_bounded_and_partial_failure_safe(
        self,
    ) -> None:
        FaviconFixtureHandler.paths = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), FaviconFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                data_dir = Path(temporary) / "data"
                data_dir.mkdir()
                destination = data_dir / "bookmarks.json"
                base_url = f"http://127.0.0.1:{server.server_port}"
                existing_favicon = f"favicons/{'a' * 64}.png"
                destination.write_text(
                    json.dumps(
                        document(
                            [
                                bookmark(
                                    "fetch",
                                    f"{base_url}/ok",
                                    title="Preserve this title",
                                    futureData={"keep": True},
                                ),
                                bookmark("fail", f"{base_url}/missing"),
                                bookmark(
                                    "existing",
                                    f"{base_url}/already",
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

                dry_run = run_backfill(
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
                self.assertEqual(FaviconFixtureHandler.paths, [])

                result = run_backfill(
                    "--workers",
                    "2",
                    "--data-file",
                    str(destination),
                )

                self.assertEqual(result.returncode, 0, result.stderr)
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
                self.assertRegex(
                    favicon,
                    r"^favicons/[0-9a-f]{64}\.png$",
                )
                self.assertEqual((data_dir / favicon).read_bytes(), PNG)
                self.assertNotIn("favicon", saved["bookmarks"][1])
                self.assertEqual(saved["bookmarks"][2]["favicon"], existing_favicon)
                self.assertNotIn("/already", FaviconFixtureHandler.paths)
                self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
                self.assertEqual(
                    stat.S_IMODE((data_dir / favicon).stat().st_mode), 0o600
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_refresh_favicons_only_installs_strictly_larger_icons(self) -> None:
        FaviconFixtureHandler.paths = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), FaviconFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                data_dir = Path(temporary) / "data"
                cache_dir = data_dir / "favicons"
                cache_dir.mkdir(parents=True)
                destination = data_dir / "bookmarks.json"
                base_url = f"http://127.0.0.1:{server.server_port}"
                refresh_url = f"{base_url}/refresh"
                stable_url = f"{base_url}/ok"
                old_small = f"favicons/{'a' * 64}.png"
                old_stable = f"favicons/{'b' * 64}.png"
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

                dry_run = run_refresh(
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

                result = run_refresh(
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
                expected_favicon = f"favicons/{expected_digest}.png"
                self.assertEqual(refreshed["favicon"], expected_favicon)
                self.assertEqual(refreshed["futureData"], {"keep": True})
                self.assertEqual((data_dir / expected_favicon).read_bytes(), PNG_128)
                self.assertFalse((data_dir / old_small).exists())
                self.assertEqual(stable["favicon"], old_stable)
                self.assertEqual((data_dir / old_stable).read_bytes(), PNG_128)
                self.assertEqual(saved["futureTopLevel"], "preserved")
                self.assertEqual(
                    list(data_dir.glob(".favicon-refresh-*")),
                    [],
                )
                self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
                self.assertEqual(
                    stat.S_IMODE((data_dir / expected_favicon).stat().st_mode),
                    0o600,
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_backfill_interrupt_cancels_queued_fetches(self) -> None:
        FaviconFixtureHandler.paths = []
        FaviconFixtureHandler.slow_started = threading.Event()
        server = ThreadingHTTPServer(("127.0.0.1", 0), FaviconFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                destination = Path(temporary) / "data" / "bookmarks.json"
                destination.parent.mkdir()
                base_url = f"http://127.0.0.1:{server.server_port}"
                original = document(
                    [
                        bookmark(f"slow-{index}", f"{base_url}/slow/{index}")
                        for index in range(20)
                    ]
                )
                destination.write_text(
                    json.dumps(original, indent=2) + "\n",
                    encoding="utf-8",
                )
                process = subprocess.Popen(
                    [
                        str(BOOKMARKCTL),
                        "backfill-favicons",
                        "--workers",
                        "1",
                        "--data-file",
                        str(destination),
                    ],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertTrue(FaviconFixtureHandler.slow_started.wait(2))

                process.send_signal(signal.SIGINT)
                stdout, stderr = process.communicate(timeout=4)

                self.assertEqual(process.returncode, 130, stderr)
                self.assertIn("interrupted", stderr)
                self.assertEqual(output_stats(stdout)["scheduled"], "20")
                slow_requests = [
                    path
                    for path in FaviconFixtureHandler.paths
                    if path.startswith("/slow/")
                ]
                self.assertEqual(slow_requests, ["/slow/0"])
                self.assertEqual(
                    json.loads(destination.read_text(encoding="utf-8")),
                    original,
                )
                self.assertEqual(
                    list(destination.parent.glob(".favicon-refresh-*")),
                    [],
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
