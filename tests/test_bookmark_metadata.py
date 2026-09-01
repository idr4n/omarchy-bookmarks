from __future__ import annotations

import hashlib
import http.client
import importlib.util
import json
import os
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "bookmark_metadata.py"


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind)
    checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)
    )


def solid_png(width: int, height: int, color: bytes = b"\xff\x66\x00\xff") -> bytes:
    compressor = zlib.compressobj()
    compressed = bytearray()
    row = b"\0" + color * width
    for _index in range(height):
        compressed.extend(compressor.compress(row))
    compressed.extend(compressor.flush())
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", bytes(compressed))
        + png_chunk(b"IEND", b"")
    )


PNG = solid_png(32, 32)
PNG_16 = solid_png(16, 16, b"\x00\x66\xff\xff")
PNG_128 = solid_png(128, 128, b"\x44\xcc\x44\xff")


class FixtureHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[tuple[str, str | None, str | None]]] = []

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
        if self.path == "/redirect-private":
            self.send_response(302)
            self.send_header(
                "Location", f"http://127.0.0.1:{self.server.server_port}/page"
            )
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
        spec = importlib.util.spec_from_file_location("bookmark_metadata_test", HELPER)
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load metadata helper")
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    @staticmethod
    def fixture_addresses(host: str, port: int) -> list[tuple[object, ...]]:
        if host != "127.0.0.1":
            raise OSError("unexpected fixture host")
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                ("127.0.0.1", port),
            )
        ]

    def run_helper(self, *arguments: str) -> dict[str, object]:
        if arguments and arguments[0] == "fetch":
            url = arguments[arguments.index("--url") + 1]
            data_dir = Path(arguments[arguments.index("--data-dir") + 1])
            with mock.patch.object(
                self.module,
                "resolve_public_addresses",
                side_effect=self.fixture_addresses,
            ):
                return self.module.fetch_metadata_with_budget(url, data_dir)
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
                r"^favicons-v2/[0-9a-f]{64}\.png$",
            )
            favicon = data_dir / str(result["favicon"])
            payload = favicon.read_bytes()
            self.assertGreater(self.module.normalized_png_size(payload), 0)
            expected_digest = hashlib.sha256(
                url.encode("utf-8") + b"\0" + payload
            ).hexdigest()
            self.assertEqual(result["favicon"], f"favicons-v2/{expected_digest}.png")
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
            self.assertEqual(self.module.normalized_png_size(favicon.read_bytes()), 128)
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
            self.assertRegex(str(result["favicon"]), r"^favicons-v2/[0-9a-f]{64}\.png$")
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

    def test_address_policy_rejects_private_and_translated_targets(self) -> None:
        for address in (
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254",
            "224.0.0.1",
            "::1",
            "fe80::1",
            "::ffff:127.0.0.1",
            "64:ff9b::808:808",
            "fec0::1",
        ):
            with self.subTest(address=address):
                self.assertFalse(self.module.public_ip_address(address))
        self.assertTrue(self.module.public_ip_address("8.8.8.8"))
        self.assertTrue(self.module.public_ip_address("2606:4700:4700::1111"))
        with self.assertRaises(OSError):
            self.module.resolve_public_addresses("127.0.0.1", 80)

    def test_url_policy_rejects_c1_controls(self) -> None:
        for control in ("\x7f", "\x85", "\x9f", "\ufeff"):
            with self.subTest(control=ord(control)):
                self.assertFalse(
                    self.module.valid_remote_url(f"https://control.example/a{control}b")
                )

    def test_request_target_preserves_double_slash_path(self) -> None:
        endpoint = (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            ("8.8.8.8", 80),
        )
        connection = mock.Mock()
        response = mock.sentinel.response
        connection.getresponse.return_value = response
        with (
            mock.patch.object(
                self.module,
                "resolve_public_addresses",
                return_value=[endpoint],
            ),
            mock.patch.object(
                self.module,
                "PinnedHTTPConnection",
                return_value=connection,
            ),
        ):
            returned_connection, returned_response = self.module.open_url_once(
                "http://example.com//nested?value=1",
                "text/html",
                time.monotonic() + 1,
            )

        self.assertIs(returned_connection, connection)
        self.assertIs(returned_response, response)
        connection.request.assert_called_once_with(
            "GET",
            "//nested?value=1",
            headers=mock.ANY,
        )

    def test_store_icon_does_not_reclose_fdopen_owned_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            with (
                mock.patch.object(
                    self.module.os,
                    "replace",
                    side_effect=OSError("replace failed"),
                ),
                mock.patch.object(
                    self.module.os,
                    "close",
                    wraps=os.close,
                ) as close,
                self.assertRaisesRegex(OSError, "replace failed"),
            ):
                self.module.store_icon(
                    data_dir,
                    "https://icon.example/",
                    PNG,
                )

            close.assert_not_called()
            self.assertEqual(list((data_dir / "favicons-v2").iterdir()), [])

    def test_private_redirect_is_revalidated_before_connecting(self) -> None:
        FixtureHandler.requests = []
        original_resolver = self.module.resolve_public_addresses

        def resolve(host: str, port: int) -> list[tuple[object, ...]]:
            if host == "public.test":
                return self.fixture_addresses("127.0.0.1", port)
            return original_resolver(host, port)

        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(
                self.module,
                "resolve_public_addresses",
                side_effect=resolve,
            ),
        ):
            result = self.module.fetch_metadata_with_budget(
                f"http://public.test:{self.server.server_port}/redirect-private",
                Path(temporary) / "data",
            )

        self.assertFalse(result["ok"])
        requested_paths = [
            path for path, _cookie, _authorization in FixtureHandler.requests
        ]
        self.assertIn("/redirect-private", requested_paths)
        self.assertNotIn("/page", requested_paths)

    def test_proxy_environment_is_ignored_by_pinned_transport(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.dict(
                os.environ,
                {
                    "HTTP_PROXY": "http://127.0.0.1:1",
                    "HTTPS_PROXY": "http://127.0.0.1:1",
                    "ALL_PROXY": "http://127.0.0.1:1",
                },
            ),
        ):
            result = self.run_helper(
                "fetch",
                "--url",
                f"{self.base_url}/page",
                "--data-dir",
                str(Path(temporary) / "data"),
            )
        self.assertTrue(result["ok"])

    def test_raster_decoder_normalizes_and_rejects_excessive_dimensions(self) -> None:
        normalized = self.module.normalize_raster(
            PNG,
            "png",
            time.monotonic() + 3,
        )
        self.assertEqual(self.module.normalized_png_size(normalized), 32)
        self.assertLessEqual(len(normalized), self.module.ICON_LIMIT)

        oversized_dimensions = solid_png(20_000, 1)
        self.assertLess(len(oversized_dimensions), self.module.ICON_LIMIT)
        self.assertEqual(
            self.module.normalize_raster(
                oversized_dimensions,
                "png",
                time.monotonic() + 3,
            ),
            b"",
        )

    def test_only_static_bounded_pngs_are_cache_eligible(self) -> None:
        animated = PNG[:33] + png_chunk(b"acTL", struct.pack(">II", 1, 0)) + PNG[33:]
        self.assertEqual(self.module.normalized_png_size(animated), 0)
        with mock.patch.object(self.module.shutil, "which", return_value=None):
            self.assertEqual(
                self.module.normalize_raster(PNG, "png", time.monotonic() + 3),
                b"",
            )

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

        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(
                module,
                "read_url",
                side_effect=http.client.BadStatusLine("broken"),
            ),
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
