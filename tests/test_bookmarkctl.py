from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOKMARKCTL = ROOT / "bookmarkctl"
CREATED_AT = "2026-08-30T12:00:00.000Z"


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
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), checksum_before)
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
            self.assertEqual(imported["bookmarks"][0]["title"], "Café trailing prose (#NotTag)")
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
                "https://valid.example/ Valid\n"
                "row without a URL\n",
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
            with self.subTest(url_index=index), tempfile.TemporaryDirectory() as temporary:
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
            original = json.dumps(
                document([bookmark("existing", "https://existing.example/")]),
                indent=2,
            ).encode() + b"\n"
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
            expected = work / "home" / ".local" / "share" / "io.github.idr4n.bookmarks" / "bookmarks.json"
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


if __name__ == "__main__":
    unittest.main()
