"""Create and validate the SQLite state bundle used by GitHub Actions artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path

STATE_FORMAT_VERSION = 1
_REQUIRED_DATABASES = ("jobs.sqlite3", "notifications.sqlite3")
_MANIFEST_NAME = "manifest.json"


class StateBundleError(ValueError):
    """A persisted state artifact is missing, corrupt, or incompatible."""


def create_state_manifest(state_dir: str | Path) -> Path:
    """Validate both SQLite files and write a versioned checksum manifest."""
    directory = Path(state_dir)
    files = _required_files(directory)
    for path in files.values():
        _validate_sqlite(path)
    manifest = {
        "format_version": STATE_FORMAT_VERSION,
        "files": {name: {"sha256": _sha256(path)} for name, path in files.items()},
    }
    output = directory / _MANIFEST_NAME
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def validate_state_bundle(state_dir: str | Path) -> None:
    """Reject missing files, incompatible manifests, checksum mismatches, and bad SQLite."""
    directory = Path(state_dir)
    manifest_path = directory / _MANIFEST_NAME
    if not manifest_path.is_file():
        raise StateBundleError("state manifest is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StateBundleError("state manifest is unreadable") from error
    if not isinstance(manifest, dict) or manifest.get("format_version") != STATE_FORMAT_VERSION:
        raise StateBundleError("state manifest format is unsupported")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, dict):
        raise StateBundleError("state manifest does not describe its database files")

    files = _required_files(directory)
    for name, path in files.items():
        entry = raw_files.get(name)
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str):
            raise StateBundleError(f"state manifest is missing a checksum for {name}")
        if _sha256(path) != entry["sha256"]:
            raise StateBundleError(f"state checksum mismatch for {name}")
        _validate_sqlite(path)


def main(argv: Sequence[str] | None = None) -> int:
    """Run state-bundle creation or validation from a workflow shell step."""
    parser = argparse.ArgumentParser(prog="internship-monitor-state")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "validate"):
        command = subparsers.add_parser(name)
        command.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            create_state_manifest(args.state_dir)
        else:
            validate_state_bundle(args.state_dir)
    except StateBundleError as error:
        parser.error(str(error))
    return 0


def _required_files(directory: Path) -> dict[str, Path]:
    files = {name: directory / name for name in _REQUIRED_DATABASES}
    missing = [name for name, path in files.items() if not path.is_file()]
    if missing:
        raise StateBundleError(f"state database files are missing: {', '.join(missing)}")
    return files


def _validate_sqlite(path: Path) -> None:
    try:
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as error:
        raise StateBundleError(f"state database is not valid SQLite: {path.name}") from error
    if row is None or row[0] != "ok":
        raise StateBundleError(f"state database integrity check failed: {path.name}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
