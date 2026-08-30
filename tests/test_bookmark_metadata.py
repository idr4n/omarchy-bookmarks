from __future__ import annotations

import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "bookmark_metadata.py"
PNG = b"\x89PNG\r\n\x1a\n" + b"fixture-png"
PNG_16 = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (16).to_bytes(4, "big") * 2
PNG_128 = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (128).to_bytes(4, "big") * 2


class FixtureHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str | None, str | None]] = []

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        self.__class__.requests.append(
            (self.path, self.headers.get("Cookie"), self.headers.get("Authorization"))
        )
        if self.path == "/page":
            body = b"""<!doctype html><html><head>
                <title>Fallback title</title>
                <meta property="og:title" content=" Open &amp; Graph ">
                <link rel="shortcut icon" href="/assets/icon.png">
                </head><body></body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/quality":
            body = b"""<!doctype html><html><head>
                <link rel="icon" sizes="16x16" href="/assets/icon-16.png">
                <link rel="apple-touch-icon" href="/assets/icon-128.png">
                </head><body></body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/malformed-icon-url":
            body = b"""<!doctype html><html><head>
                <title>Title survives malformed icon URL</title>
                <link rel="icon" href="http://[bad/icon.png">
                </head><body></body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/page")
            self.end_headers()
            return
        if self.path == "/bad-icon":
            body = b"""<html><head><title>Title survives</title>
                <link rel="icon" href="/assets/not-an-image.svg">
                </head></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/oversize":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(1024 * 1024 + 1))
            self.end_headers()
            return
        if self.path == "/assets/icon.png":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG)))
            self.end_headers()
            self.wfile.write(PNG)
            return
        if self.path == "/assets/icon-16.png":
            body = PNG_16
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/assets/icon-128.png":
            body = PNG_128
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/assets/not-an-image.svg":
            body = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">
                <rect width="16" height="16" fill="#ff6600"/>
                </svg>"""
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/trickle":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<")
            self.wfile.flush()
            time.sleep(10)
            try:
                self.wfile.write(b"title>Too late</title>")
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        self.send_response(404)
        self.end_headers()


class MetadataHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        FixtureHandler.requests = []
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def run_helper(self, *arguments: str) -> dict[str, object]:
        completed = subprocess.run(
            [sys.executable, str(HELPER), *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.stderr, "")
        return json.loads(completed.stdout)

    def test_fetches_open_graph_title_and_relative_favicon_privately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            url = f"{self.base_url}/page"
            result = self.run_helper("fetch", "--url", url, "--data-dir", str(data_dir))

            self.assertEqual(result["ok"], True)
            self.assertEqual(result["title"], "Open & Graph")
            self.assertRegex(
                str(result["favicon"]),
                r"^favicons/[0-9a-f]{64}\.png$",
            )
            favicon = data_dir / str(result["favicon"])
            self.assertEqual(favicon.read_bytes(), PNG)
            self.assertEqual(stat.S_IMODE(data_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(favicon.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(favicon.stat().st_mode), 0o600)

            cleanup = self.run_helper(
                "remove",
                "--favicon",
                str(result["favicon"]),
                "--data-dir",
                str(data_dir),
            )
            self.assertEqual(cleanup, {"ok": True, "removed": True})
            self.assertFalse(favicon.exists())
            self.assertEqual(
                self.run_helper(
                    "remove",
                    "--favicon",
                    str(result["favicon"]),
                    "--data-dir",
                    str(data_dir),
                ),
                {"ok": True, "removed": False},
            )

    def test_fetch_keeps_an_existing_sibling_icon_format(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            cache_dir = data_dir / "favicons"
            cache_dir.mkdir(parents=True)
            url = f"{self.base_url}/page"
            digest = hashlib.sha256(url.encode()).hexdigest()
            existing = cache_dir / f"{digest}.ico"
            existing.write_bytes(b"\x00\x00\x01\x00existing")

            result = self.run_helper("fetch", "--url", url, "--data-dir", str(data_dir))

            self.assertTrue(str(result["favicon"]).endswith(".png"))
            self.assertEqual(existing.read_bytes(), b"\x00\x00\x01\x00existing")

    def test_follows_a_bounded_http_redirect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_helper(
                "fetch",
                "--url",
                f"{self.base_url}/redirect",
                "--data-dir",
                str(Path(temporary) / "data"),
            )
            self.assertEqual(result["ok"], True)
            self.assertEqual(result["title"], "Open & Graph")
            self.assertTrue(str(result["favicon"]).endswith(".png"))

    def test_prefers_high_resolution_touch_icon_over_small_favicon(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            FixtureHandler.requests = []
            data_dir = Path(temporary) / "data"
            result = self.run_helper(
                "fetch",
                "--url",
                f"{self.base_url}/quality",
                "--data-dir",
                str(data_dir),
            )

            self.assertEqual(result["ok"], True)
            favicon = data_dir / str(result["favicon"])
            self.assertEqual(favicon.read_bytes(), PNG_128)
            requested_paths = [
                path for path, _cookie, _authorization in FixtureHandler.requests
            ]
            self.assertIn("/assets/icon-128.png", requested_paths)
            self.assertNotIn("/assets/icon-16.png", requested_paths)

    def test_malformed_icon_url_keeps_valid_page_title(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            result = self.run_helper(
                "fetch",
                "--url",
                f"{self.base_url}/malformed-icon-url",
                "--data-dir",
                str(data_dir),
            )

            self.assertEqual(
                result,
                {
                    "ok": True,
                    "title": "Title survives malformed icon URL",
                    "favicon": "",
                    "warning": "favicon-unavailable",
                },
            )
            self.assertFalse(data_dir.exists())

    def test_rasterizes_svg_icon_and_keeps_page_title(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            result = self.run_helper(
                "fetch",
                "--url",
                f"{self.base_url}/bad-icon",
                "--data-dir",
                str(data_dir),
            )
            self.assertEqual(result["ok"], True)
            self.assertEqual(result["title"], "Title survives")
            self.assertRegex(str(result["favicon"]), r"^favicons/[0-9a-f]{64}\.png$")
            favicon = data_dir / str(result["favicon"])
            payload = favicon.read_bytes()
            self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(
                (
                    int.from_bytes(payload[16:20], "big"),
                    int.from_bytes(payload[20:24], "big"),
                ),
                (128, 128),
            )

    def test_size_bound_and_invalid_urls_fail_without_cache_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            oversized = self.run_helper(
                "fetch",
                "--url",
                f"{self.base_url}/oversize",
                "--data-dir",
                str(data_dir),
            )
            self.assertEqual(oversized["ok"], False)
            self.assertIn(
                oversized["error"], {"metadata-unavailable", "request-failed"}
            )
            self.assertFalse(data_dir.exists())

            invalid = self.run_helper(
                "fetch",
                "--url",
                "file:///tmp/private",
                "--data-dir",
                str(data_dir),
            )
            self.assertEqual(
                invalid,
                {"ok": False, "title": "", "favicon": "", "error": "invalid-url"},
            )
            self.assertFalse(data_dir.exists())

    def test_fetch_has_a_hard_wall_clock_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            started = time.monotonic()
            result = self.run_helper(
                "fetch",
                "--url",
                f"{self.base_url}/trickle",
                "--data-dir",
                str(Path(temporary) / "data"),
            )
            elapsed = time.monotonic() - started

            self.assertEqual(
                result,
                {"ok": False, "title": "", "favicon": "", "error": "timeout"},
            )
            self.assertLess(elapsed, 7)

    def test_cleanup_rejects_paths_outside_the_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            result = self.run_helper(
                "remove",
                "--favicon",
                "../bookmarks.json",
                "--data-dir",
                str(data_dir),
            )
            self.assertEqual(result, {"ok": False, "error": "invalid-favicon"})
            self.assertFalse(data_dir.exists())

    def test_http_protocol_failures_return_safe_fallback(self) -> None:
        spec = importlib.util.spec_from_file_location("bookmark_metadata_test", HELPER)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(
                module,
                "read_url",
                side_effect=http.client.BadStatusLine("broken"),
            ):
                result = module.fetch_metadata(
                    "https://example.com/",
                    Path(temporary) / "data",
                )

        self.assertEqual(
            result,
            {
                "ok": False,
                "title": "",
                "favicon": "",
                "error": "request-failed",
            },
        )

    def test_requests_send_no_cookie_or_authorization_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            FixtureHandler.requests = []
            self.run_helper(
                "fetch",
                "--url",
                f"{self.base_url}/page",
                "--data-dir",
                str(Path(temporary) / "data"),
            )
            self.assertGreaterEqual(len(FixtureHandler.requests), 2)
            for _path, cookie, authorization in FixtureHandler.requests:
                self.assertIsNone(cookie)
                self.assertIsNone(authorization)


if __name__ == "__main__":
    unittest.main()
