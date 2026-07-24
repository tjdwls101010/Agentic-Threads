#!/usr/bin/env python3
"""Store one JSON body from stdin in the repository's gitignored ``scratch/``.

This developer helper performs no network or browser activity. It preserves the
validated input bytes as ``scratch/<name>.raw.json`` and never writes into the
committed fixture tree.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRATCH_DIR = PROJECT_ROOT / "scratch"
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"
WARNING = (
    "WARNING: RAW CAPTURES MAY CONTAIN CREDENTIALS AND THIRD-PARTY PII. "
    "THEY MUST NEVER BE COMMITTED, SHARED, OR COPIED INTO tests/fixtures/."
)

_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600


class CaptureError(ValueError):
    """Raised when a capture cannot be stored without violating safety rules."""


def _capture_filename(name: str) -> str:
    if (
        not _NAME_RE.fullmatch(name)
        or ".." in name
        or "/" in name
        or "\\" in name
        or Path(name).is_absolute()
    ):
        raise CaptureError("capture name must be a single safe filename stem without paths or '..'")
    return f"{name}.raw.json"


def _validate_payload(payload: bytes) -> None:
    if not payload.strip():
        raise CaptureError("stdin did not contain JSON")
    try:
        json.loads(payload)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise CaptureError("stdin did not contain valid JSON") from exc


def _ensure_private_scratch() -> int:
    if SCRATCH_DIR.name != "scratch":
        raise CaptureError("capture destination is not the repository scratch directory")
    try:
        SCRATCH_DIR.mkdir(mode=_DIRECTORY_MODE)
    except FileExistsError:
        pass
    except OSError as exc:
        raise CaptureError("scratch directory could not be created safely") from exc

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(SCRATCH_DIR, flags)
    except OSError as exc:
        raise CaptureError("scratch path must be a real directory, not a symlink") from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise CaptureError("scratch path must be a real directory, not a symlink")
    try:
        os.fchmod(descriptor, _DIRECTORY_MODE)
    except OSError as exc:
        os.close(descriptor)
        raise CaptureError("scratch directory permissions could not be secured") from exc
    return descriptor


def _assert_destination_scope(filename: str) -> Path:
    destination = SCRATCH_DIR / filename
    try:
        destination.relative_to(SCRATCH_DIR)
    except ValueError as exc:
        raise CaptureError("capture destination escaped the scratch directory") from exc

    scratch_resolved = SCRATCH_DIR.resolve(strict=False)
    fixtures_resolved = FIXTURES_DIR.resolve(strict=False)
    try:
        scratch_resolved.relative_to(fixtures_resolved)
    except ValueError:
        pass
    else:
        raise CaptureError("capture destination may not be tests/fixtures")
    return destination


def write_capture(name: str, payload: bytes) -> Path:
    """Validate and exclusively create ``scratch/<name>.raw.json`` with mode 0600."""
    filename = _capture_filename(name)
    _validate_payload(payload)
    destination = _assert_destination_scope(filename)
    directory_descriptor = _ensure_private_scratch()
    file_descriptor = -1
    created = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            file_descriptor = os.open(
                filename,
                flags,
                _FILE_MODE,
                dir_fd=directory_descriptor,
            )
        except FileExistsError as exc:
            raise CaptureError(
                "capture destination already exists; refusing to overwrite it"
            ) from exc
        except OSError as exc:
            raise CaptureError("capture destination could not be created safely") from exc
        created = True
        os.fchmod(file_descriptor, _FILE_MODE)
        with os.fdopen(file_descriptor, "wb") as stream:
            file_descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        if created:
            try:
                os.unlink(filename, dir_fd=directory_descriptor)
            except OSError:
                pass
        raise
    finally:
        os.close(directory_descriptor)
    return destination


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read one JSON body from stdin into private, gitignored scratch storage."
    )
    parser.add_argument("name", help="safe output stem; writes scratch/NAME.raw.json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    print(WARNING, file=sys.stderr)
    try:
        destination = write_capture(args.name, sys.stdin.buffer.read())
    except CaptureError as exc:
        print(f"record_fixture: {exc}", file=sys.stderr)
        return 2
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
