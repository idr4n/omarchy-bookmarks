"""Fetch bookmark page metadata and maintain the private favicon cache."""

from __future__ import annotations

import argparse
import hashlib
import html.parser
import http.client
import ipaddress
import json
import os
import re
import resource
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import zlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

HTML_LIMIT = 1024 * 1024
ICON_LIMIT = 256 * 1024
REDIRECT_LIMIT = 3
REQUEST_BUDGET_SECONDS = 5.0
TITLE_LIMIT = 512
FAVICON_SIZE = 128
PREFERRED_ICON_SIZE = 64
ICON_LINK_LIMIT = 3
DECODER_SECONDS = 2.0
DECODER_ADDRESS_SPACE = 512 * 1024 * 1024
DECODER_MEMORY = "32MiB"
DECODER_MAP = "64MiB"
MAX_RASTER_DIMENSION = 16_384
MAX_RASTER_AREA = 64 * 1024 * 1024
USER_AGENT = "io.github.idr4n.bookmarks/1 metadata-fetcher"
LEGACY_FAVICON_PATTERN = re.compile(
    r"^favicons/([0-9a-f]{64})\.(gif|ico|jpg|png|webp)$"
)
SAFE_FAVICON_PATTERN = re.compile(r"^favicons-v2/([0-9a-f]{64})\.png$")
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
NAT64_NETWORKS = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)


class RequestBudgetExpired(Exception):
    pass


class MetadataParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_title = False
        self._title_parts: list[str] = []
        self.open_graph_title = ""
        self.icon_links: list[tuple[str, frozenset[str], str, str]] = []
        self.base_href = ""

    @property
    def title(self) -> str:
        return normalize_title("".join(self._title_parts))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        if name == "title":
            self._in_title = True
        elif name == "meta" and not self.open_graph_title:
            property_name = (
                attributes.get("property") or attributes.get("name") or ""
            ).lower()
            if property_name == "og:title":
                self.open_graph_title = normalize_title(attributes.get("content", ""))
        elif name == "link":
            rel = frozenset(
                value.lower() for value in attributes.get("rel", "").split()
            )
            href = attributes.get("href", "").strip()
            if href and rel.intersection(
                {"icon", "apple-touch-icon", "apple-touch-icon-precomposed"}
            ):
                self.icon_links.append(
                    (href, rel, attributes.get("sizes", ""), attributes.get("type", ""))
                )
        elif name == "base" and not self.base_href:
            self.base_href = attributes.get("href", "").strip()

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)


AddressInfo = tuple[int, int, int, tuple[Any, ...]]


def valid_remote_url(value: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if any(
        ord(character) <= 32 or 127 <= ord(character) <= 159 or ord(character) == 0xFEFF
        for character in value
    ):
        return False
    try:
        parsed = urllib.parse.urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in {"http", "https"}
        and bool(parsed.hostname)
        and "%" not in (parsed.hostname or "")
        and parsed.username is None
        and parsed.password is None
    )


def normalize_title(value: str) -> str:
    cleaned = "".join(
        " " if ord(character) < 32 or 127 <= ord(character) <= 159 else character
        for character in str(value or "")
    )
    return " ".join(cleaned.split())[:TITLE_LIMIT]


def remaining_timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("request budget expired")
    return max(0.1, remaining)


def public_ip_address(value: str) -> bool:
    if "%" in value:
        return False
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.is_site_local:
            return False
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        elif any(address in network for network in NAT64_NETWORKS):
            return False
    return address.is_global and not address.is_multicast


def resolve_public_addresses(host: str, port: int) -> list[AddressInfo]:
    addresses: list[AddressInfo] = []
    seen: set[tuple[object, ...]] = set()
    for family, socktype, proto, _canonname, sockaddr in socket.getaddrinfo(
        host,
        port,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    ):
        if not sockaddr or not public_ip_address(str(sockaddr[0])):
            continue
        if family == socket.AF_INET6 and len(sockaddr) >= 4 and sockaddr[3] != 0:
            continue
        key = (family, socktype, proto, *sockaddr)
        if key in seen:
            continue
        seen.add(key)
        addresses.append((family, socktype, proto, sockaddr))
    if not addresses:
        raise OSError("remote host has no public address")
    return addresses


def connect_address(endpoint: AddressInfo, timeout: float) -> socket.socket:
    family, socktype, proto, sockaddr = endpoint
    connection = socket.socket(family, socktype, proto)
    try:
        connection.settimeout(timeout)
        connection.connect(sockaddr)
        return connection
    except Exception:
        connection.close()
        raise


class PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(
        self,
        host: str,
        port: int,
        endpoint: AddressInfo,
        timeout: float,
    ) -> None:
        super().__init__(host, port, timeout=timeout)
        self.endpoint = endpoint

    def connect(self) -> None:
        self.sock = connect_address(self.endpoint, self.timeout)


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        port: int,
        endpoint: AddressInfo,
        timeout: float,
    ) -> None:
        super().__init__(host, port, timeout=timeout)
        self.endpoint = endpoint

    def connect(self) -> None:
        raw_socket = connect_address(self.endpoint, self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except Exception:
            raw_socket.close()
            raise


def open_url_once(
    url: str,
    accept: str,
    deadline: float,
) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname
    if not host:
        raise ValueError("URL has no host")
    scheme = parsed.scheme.lower()
    port = parsed.port or (443 if scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    headers = {
        "Accept": accept,
        "Accept-Encoding": "identity",
        "Connection": "close",
        "Host": parsed.netloc,
        "User-Agent": USER_AGENT,
    }
    last_error: Exception | None = None
    for endpoint in resolve_public_addresses(host, port):
        connection: http.client.HTTPConnection
        timeout = remaining_timeout(deadline)
        if scheme == "https":
            connection = PinnedHTTPSConnection(host, port, endpoint, timeout)
        else:
            connection = PinnedHTTPConnection(host, port, endpoint, timeout)
        try:
            connection.request("GET", path, headers=headers)
            return connection, connection.getresponse()
        except (OSError, TimeoutError, http.client.HTTPException) as error:
            last_error = error
            connection.close()
    raise OSError("could not connect to public address") from last_error


def read_url(url: str, limit: int, accept: str, deadline: float):
    current_url = url
    for redirect_count in range(REDIRECT_LIMIT + 1):
        if not valid_remote_url(current_url):
            raise ValueError("invalid remote URL")
        connection, response = open_url_once(current_url, accept, deadline)
        try:
            if response.status in REDIRECT_STATUSES:
                location = response.getheader("Location")
                if not location or redirect_count >= REDIRECT_LIMIT:
                    raise ValueError("redirect refused")
                redirected = urllib.parse.urljoin(current_url, location)
                if not valid_remote_url(redirected):
                    raise ValueError("redirect refused")
                current_url = redirected
                continue
            if response.status < 200 or response.status >= 300:
                raise OSError(f"HTTP request failed with status {response.status}")
            declared_length = response.getheader("Content-Length")
            if declared_length:
                try:
                    if int(declared_length) < 0 or int(declared_length) > limit:
                        raise ValueError("response too large")
                except ValueError as error:
                    if str(error) == "response too large":
                        raise
            payload = bytearray()
            while len(payload) <= limit:
                if connection.sock is not None:
                    connection.sock.settimeout(remaining_timeout(deadline))
                chunk = response.read(min(65536, limit + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
            if len(payload) > limit:
                raise ValueError("response too large")
            return bytes(payload), current_url, response.headers
        finally:
            response.close()
            connection.close()
    raise ValueError("redirect refused")


def parse_page(payload: bytes, headers) -> MetadataParser:
    charset = headers.get_content_charset() or "utf-8"
    try:
        text = payload.decode(charset, errors="replace")
    except LookupError:
        text = payload.decode("utf-8", errors="replace")
    parser = MetadataParser()
    parser.feed(text)
    parser.close()
    return parser


def icon_extension(payload: bytes) -> str:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if payload.startswith(b"\x00\x00\x01\x00"):
        return "ico"
    if len(payload) >= 12 and payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return "webp"
    return ""


def icon_pixel_size(payload: bytes, extension: str) -> int:
    if extension == "png" and len(payload) >= 24 and payload[12:16] == b"IHDR":
        return min(struct.unpack(">II", payload[16:24]))
    if extension == "gif" and len(payload) >= 10:
        return min(struct.unpack("<HH", payload[6:10]))
    if extension == "ico" and len(payload) >= 6:
        count = int.from_bytes(payload[4:6], "little")
        if count < 1 or len(payload) < 6 + count * 16:
            return 0
        sizes = []
        for offset in range(6, 6 + count * 16, 16):
            width = payload[offset] or 256
            height = payload[offset + 1] or 256
            sizes.append(min(width, height))
        return max(sizes, default=0)
    if extension == "jpg":
        offset = 2
        start_of_frame = {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }
        while offset + 4 <= len(payload):
            if payload[offset] != 0xFF:
                offset += 1
                continue
            while offset < len(payload) and payload[offset] == 0xFF:
                offset += 1
            if offset >= len(payload):
                return 0
            marker = payload[offset]
            offset += 1
            if marker in {0x01, *range(0xD0, 0xDA)}:
                continue
            if offset + 2 > len(payload):
                return 0
            segment_length = int.from_bytes(payload[offset : offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(payload):
                return 0
            if marker in start_of_frame and segment_length >= 7:
                height = int.from_bytes(payload[offset + 3 : offset + 5], "big")
                width = int.from_bytes(payload[offset + 5 : offset + 7], "big")
                return min(width, height)
            offset += segment_length
        return 0
    if extension == "webp" and len(payload) >= 30:
        chunk = payload[12:16]
        data = payload[20:]
        if chunk == b"VP8X" and len(data) >= 10:
            width = 1 + int.from_bytes(data[4:7], "little")
            height = 1 + int.from_bytes(data[7:10], "little")
            return min(width, height)
        if chunk == b"VP8L" and len(data) >= 5 and data[0] == 0x2F:
            bits = int.from_bytes(data[1:5], "little")
            width = 1 + (bits & 0x3FFF)
            height = 1 + ((bits >> 14) & 0x3FFF)
            return min(width, height)
        if chunk == b"VP8 " and len(data) >= 10 and data[3:6] == b"\x9d\x01\x2a":
            width = int.from_bytes(data[6:8], "little") & 0x3FFF
            height = int.from_bytes(data[8:10], "little") & 0x3FFF
            return min(width, height)
    return 0


def declared_icon_size(value: str) -> int:
    sizes = value.lower().split()
    if "any" in sizes:
        return FAVICON_SIZE
    result = 0
    for size in sizes:
        match = re.fullmatch(r"(\d+)x(\d+)", size)
        if match:
            result = max(result, min(int(match.group(1)), int(match.group(2))))
    return result


def icon_link_score(link: tuple[str, frozenset[str], str, str]) -> tuple[int, int]:
    href, rel, sizes, media_type = link
    try:
        path = urllib.parse.urlsplit(href).path.lower()
    except ValueError:
        return -1, -1
    scalable = (
        "any" in sizes.lower().split()
        or media_type.lower().split(";", 1)[0].strip() == "image/svg+xml"
        or path.endswith((".svg", ".svgz"))
    )
    if scalable:
        return 10_000, FAVICON_SIZE
    declared = declared_icon_size(sizes)
    if declared:
        return declared, 2
    if rel.intersection({"apple-touch-icon", "apple-touch-icon-precomposed"}):
        return 180, 1
    return 0, 0


def is_svg_icon(payload: bytes, headers) -> bool:
    try:
        if headers.get_content_type().lower() == "image/svg+xml":
            return True
    except (AttributeError, TypeError):
        pass
    prefix = payload[:4096].lstrip().lower()
    return prefix.startswith(b"<svg") or (
        prefix.startswith(b"<?xml") and b"<svg" in prefix
    )


def apply_decoder_limits() -> None:
    def set_limit(kind: int, value: int) -> None:
        _soft, hard = resource.getrlimit(kind)
        target = value if hard == resource.RLIM_INFINITY else min(value, hard)
        resource.setrlimit(kind, (target, target))

    set_limit(resource.RLIMIT_AS, DECODER_ADDRESS_SPACE)
    set_limit(resource.RLIMIT_CPU, max(1, int(DECODER_SECONDS) + 1))
    set_limit(resource.RLIMIT_FSIZE, ICON_LIMIT)
    set_limit(resource.RLIMIT_NOFILE, 32)


def normalized_png_size(payload: bytes) -> int:
    if (
        len(payload) < 33
        or len(payload) > ICON_LIMIT
        or not payload.startswith(b"\x89PNG\r\n\x1a\n")
    ):
        return 0
    offset = 8
    width = 0
    height = 0
    saw_header = False
    while offset + 12 <= len(payload):
        length = int.from_bytes(payload[offset : offset + 4], "big")
        chunk_type = payload[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        chunk_end = data_end + 4
        if chunk_end > len(payload):
            return 0
        expected_crc = int.from_bytes(payload[data_end:chunk_end], "big")
        actual_crc = zlib.crc32(chunk_type)
        actual_crc = zlib.crc32(payload[data_start:data_end], actual_crc) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            return 0
        if not saw_header:
            if chunk_type != b"IHDR" or length != 13:
                return 0
            width, height = struct.unpack(">II", payload[data_start : data_start + 8])
            if width < 1 or height < 1 or width > FAVICON_SIZE or height > FAVICON_SIZE:
                return 0
            saw_header = True
        elif chunk_type in {b"IHDR", b"acTL"}:
            return 0
        if chunk_type == b"IEND":
            return (
                min(width, height) if length == 0 and chunk_end == len(payload) else 0
            )
        offset = chunk_end
    return 0


def decoder_environment(temporary_directory: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment["MAGICK_MEMORY_LIMIT"] = DECODER_MEMORY
    environment["MAGICK_MAP_LIMIT"] = DECODER_MAP
    environment["MAGICK_DISK_LIMIT"] = "0"
    environment["MAGICK_THREAD_LIMIT"] = "1"
    environment["MAGICK_TMPDIR"] = temporary_directory
    return environment


def normalize_raster(payload: bytes, extension: str, deadline: float) -> bytes:
    decoder = shutil.which("magick")
    decoder_format = {
        "gif": "gif",
        "ico": "ico",
        "jpg": "jpeg",
        "png": "png",
        "webp": "webp",
    }.get(extension)
    if not decoder or not decoder_format or time.monotonic() >= deadline:
        return b""
    with tempfile.TemporaryDirectory(prefix=".bookmark-raster-") as temporary_name:
        os.chmod(temporary_name, 0o700)
        source = Path(temporary_name) / "input"
        source.write_bytes(payload)
        os.chmod(source, 0o600)
        timeout = min(DECODER_SECONDS, remaining_timeout(deadline))
        try:
            completed = subprocess.run(
                [
                    decoder,
                    "-limit",
                    "thread",
                    "1",
                    "-limit",
                    "time",
                    str(max(1, int(DECODER_SECONDS))),
                    "-limit",
                    "memory",
                    DECODER_MEMORY,
                    "-limit",
                    "map",
                    DECODER_MAP,
                    "-limit",
                    "disk",
                    "0",
                    "-limit",
                    "width",
                    str(MAX_RASTER_DIMENSION),
                    "-limit",
                    "height",
                    str(MAX_RASTER_DIMENSION),
                    "-limit",
                    "area",
                    str(MAX_RASTER_AREA),
                    "-limit",
                    "list-length",
                    "2",
                    f"{decoder_format}:{source}[0]",
                    "-auto-orient",
                    "-thumbnail",
                    f"{FAVICON_SIZE}x{FAVICON_SIZE}>",
                    "-strip",
                    "png:-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=timeout,
                cwd=temporary_name,
                env=decoder_environment(temporary_name),
                preexec_fn=apply_decoder_limits,
            )
        except (OSError, subprocess.SubprocessError):
            return b""
    raster = completed.stdout
    if completed.returncode != 0 or not normalized_png_size(raster):
        return b""
    return raster


def rasterize_svg(payload: bytes, deadline: float) -> bytes:
    rasterizer = shutil.which("rsvg-convert")
    if not rasterizer or time.monotonic() >= deadline:
        return b""
    with tempfile.TemporaryDirectory(prefix=".bookmark-svg-") as temporary_name:
        os.chmod(temporary_name, 0o700)
        timeout = min(DECODER_SECONDS, remaining_timeout(deadline))
        try:
            completed = subprocess.run(
                [
                    rasterizer,
                    "--format=png",
                    f"--width={FAVICON_SIZE}",
                    f"--height={FAVICON_SIZE}",
                    "--keep-aspect-ratio",
                ],
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=timeout,
                cwd=temporary_name,
                env=decoder_environment(temporary_name),
                preexec_fn=apply_decoder_limits,
            )
        except (OSError, subprocess.SubprocessError):
            return b""
    raster = completed.stdout
    if completed.returncode != 0 or not normalized_png_size(raster):
        return b""
    return raster


def ensure_private_directory(path: Path) -> None:
    if not path.is_absolute():
        raise OSError("cache path must be absolute")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise OSError("cache path is not a directory")
    os.chmod(path, 0o700)


def fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def store_icon(
    data_dir: Path, bookmark_url: str, payload: bytes, extension: str = "png"
) -> str:
    if extension != "png" or not normalized_png_size(payload):
        raise OSError("icon is not a normalized PNG")
    cache_dir = data_dir / "favicons-v2"
    ensure_private_directory(data_dir)
    ensure_private_directory(cache_dir)
    digest_builder = hashlib.sha256()
    digest_builder.update(bookmark_url.encode("utf-8"))
    digest_builder.update(b"\0")
    digest_builder.update(payload)
    digest = digest_builder.hexdigest()
    target = cache_dir / f"{digest}.png"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{digest}.", dir=cache_dir)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            descriptor = -1
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
        fsync_directory(cache_dir)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return f"favicons-v2/{target.name}"


def joined_remote_url(base_url: str, href: str) -> str:
    try:
        candidate = urllib.parse.urljoin(base_url, href)
    except ValueError:
        return ""
    return candidate if valid_remote_url(candidate) else ""


def icon_candidates(page_url: str, parser: MetadataParser | None) -> Iterable[str]:
    base_url = page_url
    hrefs: list[str] = []
    if parser is not None:
        if parser.base_href:
            candidate_base = joined_remote_url(page_url, parser.base_href)
            if candidate_base:
                base_url = candidate_base
        ranked_links = sorted(parser.icon_links, key=icon_link_score, reverse=True)
        hrefs.extend(link[0] for link in ranked_links[:ICON_LINK_LIMIT])
    hrefs.extend(
        (
            joined_remote_url(page_url, "/apple-touch-icon.png"),
            joined_remote_url(page_url, "/favicon.ico"),
        )
    )

    seen: set[str] = set()
    for href in hrefs:
        candidate = joined_remote_url(base_url, href)
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        yield candidate


def fetch_metadata(url: str, data_dir: Path) -> dict[str, object]:
    if not valid_remote_url(url):
        return {"ok": False, "title": "", "favicon": "", "error": "invalid-url"}

    deadline = time.monotonic() + REQUEST_BUDGET_SECONDS
    parser: MetadataParser | None = None
    page_url = url
    page_ok = False
    try:
        payload, page_url, headers = read_url(
            url,
            HTML_LIMIT,
            "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
            deadline,
        )
        parser = parse_page(payload, headers)
        page_ok = True
    except (
        http.client.HTTPException,
        OSError,
        TimeoutError,
        ValueError,
    ):
        pass

    title = ""
    if parser is not None:
        title = parser.open_graph_title or parser.title

    favicon = ""
    best_icon: tuple[bytes, int] | None = None
    for candidate in icon_candidates(page_url, parser):
        if time.monotonic() >= deadline:
            break
        try:
            icon_payload, _final_url, icon_headers = read_url(
                candidate,
                ICON_LIMIT,
                "image/png,image/jpeg,image/gif,image/x-icon,image/vnd.microsoft.icon,image/webp,image/svg+xml;q=0.9,*/*;q=0.1",
                deadline,
            )
            extension = icon_extension(icon_payload)
            if extension:
                quality = icon_pixel_size(icon_payload, extension)
                normalized = normalize_raster(icon_payload, extension, deadline)
            elif is_svg_icon(icon_payload, icon_headers):
                normalized = rasterize_svg(icon_payload, deadline)
                quality = normalized_png_size(normalized)
            else:
                continue
            if not normalized:
                continue
            quality = quality or normalized_png_size(normalized)
            if best_icon is None or quality > best_icon[1]:
                best_icon = normalized, quality
            if quality >= PREFERRED_ICON_SIZE:
                break
        except (
            http.client.HTTPException,
            OSError,
            TimeoutError,
            ValueError,
        ):
            continue
    if best_icon is not None:
        try:
            favicon = store_icon(data_dir, url, best_icon[0])
        except OSError:
            pass

    if title or favicon:
        warning = ""
        if not title:
            warning = "title-unavailable"
        elif not favicon:
            warning = "favicon-unavailable"
        return {"ok": True, "title": title, "favicon": favicon, "warning": warning}
    return {
        "ok": False,
        "title": "",
        "favicon": "",
        "error": "metadata-unavailable" if page_ok else "request-failed",
    }


def fetch_metadata_with_budget(url: str, data_dir: Path) -> dict[str, object]:
    def expire_request(_signum, _frame) -> None:
        raise RequestBudgetExpired

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, expire_request)
    signal.setitimer(signal.ITIMER_REAL, REQUEST_BUDGET_SECONDS)
    try:
        return fetch_metadata(url, data_dir)
    except RequestBudgetExpired:
        return {"ok": False, "title": "", "favicon": "", "error": "timeout"}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def favicon_cache_parts(relative_path: str) -> tuple[str, str] | None:
    safe_match = SAFE_FAVICON_PATTERN.fullmatch(relative_path or "")
    if safe_match:
        return "favicons-v2", f"{safe_match.group(1)}.png"
    legacy_match = LEGACY_FAVICON_PATTERN.fullmatch(relative_path or "")
    if legacy_match:
        return "favicons", f"{legacy_match.group(1)}.{legacy_match.group(2)}"
    return None


def remove_cached_favicon(data_dir: Path, relative_path: str) -> dict[str, object]:
    parts = favicon_cache_parts(relative_path)
    if parts is None:
        return {"ok": False, "error": "invalid-favicon"}
    directory_name, filename = parts
    cache_dir = data_dir / directory_name
    if not cache_dir.exists():
        return {"ok": True, "removed": False}
    if not cache_dir.is_absolute() or cache_dir.is_symlink() or not cache_dir.is_dir():
        return {"ok": False, "error": "cache-unavailable"}
    target = cache_dir / filename
    try:
        target.unlink()
        fsync_directory(cache_dir)
        return {"ok": True, "removed": True}
    except FileNotFoundError:
        return {"ok": True, "removed": False}
    except OSError:
        return {"ok": False, "error": "cache-unavailable"}


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(prog="bookmark_metadata.py")
    subparsers = command_parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch")
    fetch.add_argument("--url", required=True)
    fetch.add_argument("--data-dir", required=True, type=Path)

    remove = subparsers.add_parser("remove")
    remove.add_argument("--favicon", required=True)
    remove.add_argument("--data-dir", required=True, type=Path)
    return command_parser


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.command == "fetch":
        result = fetch_metadata_with_budget(
            arguments.url, arguments.data_dir.expanduser()
        )
    else:
        result = remove_cached_favicon(
            arguments.data_dir.expanduser(), arguments.favicon
        )
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
