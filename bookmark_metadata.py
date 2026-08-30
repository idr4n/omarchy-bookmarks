#!/usr/bin/env python3
"""Fetch bookmark page metadata and maintain the private favicon cache."""

from __future__ import annotations

import argparse
import hashlib
import html.parser
import http.client
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Iterable
import urllib.error
import urllib.parse
import urllib.request

HTML_LIMIT = 1024 * 1024
ICON_LIMIT = 256 * 1024
REDIRECT_LIMIT = 3
REQUEST_BUDGET_SECONDS = 5.0
TITLE_LIMIT = 512
FAVICON_SIZE = 64
SVG_RASTER_SECONDS = 2.0
USER_AGENT = "io.github.idr4n.bookmarks/1 metadata-fetcher"
FAVICON_PATTERN = re.compile(r"^favicons/([0-9a-f]{64})\.(gif|ico|jpg|png|webp)$")


class RequestBudgetExpired(Exception):
    pass


class MetadataParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_title = False
        self._title_parts: list[str] = []
        self.open_graph_title = ""
        self.icon_hrefs: list[str] = []
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
            property_name = (attributes.get("property") or attributes.get("name") or "").lower()
            if property_name == "og:title":
                self.open_graph_title = normalize_title(attributes.get("content", ""))
        elif name == "link":
            rel = {value.lower() for value in attributes.get("rel", "").split()}
            href = attributes.get("href", "").strip()
            if href and "icon" in rel:
                self.icon_hrefs.append(href)
        elif name == "base" and not self.base_href:
            self.base_href = attributes.get("href", "").strip()

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)


class LimitedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self) -> None:
        super().__init__()
        self.redirect_count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        self.redirect_count += 1
        if self.redirect_count > REDIRECT_LIMIT or not valid_remote_url(newurl):
            raise urllib.error.HTTPError(newurl, code, "redirect refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def valid_remote_url(value: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if any(ord(character) <= 32 or ord(character) == 127 for character in value):
        return False
    try:
        parsed = urllib.parse.urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in {"http", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def normalize_title(value: str) -> str:
    cleaned = " ".join(str(value or "").split())
    return cleaned[:TITLE_LIMIT]


def remaining_timeout(deadline: float) -> float:
    return max(0.1, deadline - time.monotonic())


def read_url(url: str, limit: int, accept: str, deadline: float):
    if not valid_remote_url(url) or time.monotonic() >= deadline:
        raise ValueError("invalid or expired request")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "Accept-Encoding": "identity",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    opener = urllib.request.build_opener(LimitedRedirectHandler())
    with opener.open(request, timeout=remaining_timeout(deadline)) as response:
        final_url = response.geturl()
        if not valid_remote_url(final_url):
            raise ValueError("invalid response URL")
        declared_length = response.headers.get("Content-Length")
        if declared_length:
            try:
                if int(declared_length) > limit:
                    raise ValueError("response too large")
            except ValueError as error:
                if str(error) == "response too large":
                    raise
        payload = response.read(limit + 1)
        if len(payload) > limit:
            raise ValueError("response too large")
        return payload, final_url, response.headers


def parse_page(payload: bytes, headers) -> MetadataParser:  # noqa: ANN001
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


def rasterize_svg(payload: bytes, deadline: float) -> bytes:
    rasterizer = shutil.which("rsvg-convert")
    if not rasterizer or time.monotonic() >= deadline:
        return b""
    timeout = min(SVG_RASTER_SECONDS, max(0.1, deadline - time.monotonic()))
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
        )
    except (OSError, subprocess.SubprocessError):
        return b""
    raster = completed.stdout
    if (
        completed.returncode != 0
        or len(raster) > ICON_LIMIT
        or icon_extension(raster) != "png"
    ):
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


def store_icon(data_dir: Path, bookmark_url: str, payload: bytes, extension: str) -> str:
    cache_dir = data_dir / "favicons"
    ensure_private_directory(data_dir)
    ensure_private_directory(cache_dir)
    digest = hashlib.sha256(bookmark_url.encode("utf-8")).hexdigest()
    target = cache_dir / f"{digest}.{extension}"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{digest}.", dir=cache_dir)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
        fsync_directory(cache_dir)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return f"favicons/{target.name}"


def icon_candidates(page_url: str, parser: MetadataParser | None) -> Iterable[str]:
    base_url = page_url
    hrefs: list[str] = []
    if parser is not None:
        if parser.base_href:
            candidate_base = urllib.parse.urljoin(page_url, parser.base_href)
            if valid_remote_url(candidate_base):
                base_url = candidate_base
        hrefs.extend(parser.icon_hrefs[:3])
    hrefs.append(urllib.parse.urljoin(page_url, "/favicon.ico"))

    seen: set[str] = set()
    for href in hrefs:
        candidate = urllib.parse.urljoin(base_url, href)
        if candidate in seen or not valid_remote_url(candidate):
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
    except (http.client.HTTPException, OSError, TimeoutError, ValueError, urllib.error.URLError):
        pass

    title = ""
    if parser is not None:
        title = parser.open_graph_title or parser.title

    favicon = ""
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
            if not extension and is_svg_icon(icon_payload, icon_headers):
                icon_payload = rasterize_svg(icon_payload, deadline)
                extension = icon_extension(icon_payload)
            if not extension:
                continue
            favicon = store_icon(data_dir, url, icon_payload, extension)
            break
        except (http.client.HTTPException, OSError, TimeoutError, ValueError, urllib.error.URLError):
            continue

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
    def expire_request(_signum, _frame) -> None:  # noqa: ANN001
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


def remove_cached_favicon(data_dir: Path, relative_path: str) -> dict[str, object]:
    match = FAVICON_PATTERN.fullmatch(relative_path or "")
    if not match:
        return {"ok": False, "error": "invalid-favicon"}
    cache_dir = data_dir / "favicons"
    if not cache_dir.exists():
        return {"ok": True, "removed": False}
    if not cache_dir.is_absolute() or cache_dir.is_symlink() or not cache_dir.is_dir():
        return {"ok": False, "error": "cache-unavailable"}
    target = cache_dir / Path(relative_path).name
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
        result = fetch_metadata_with_budget(arguments.url, arguments.data_dir.expanduser())
    else:
        result = remove_cached_favicon(arguments.data_dir.expanduser(), arguments.favicon)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
