"""Transfer a verified local trade CSV and report as one read-only ZIP."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import csv
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from zipfile import BadZipFile, ZIP_STORED, ZipFile, ZipInfo

from .review_trades import verify_report

MEMBERS = ("trades.csv", "report.json")
MAX_BUNDLE_BYTES = 128 * 1024 * 1024
_SHA256 = re.compile(r"[a-fA-F0-9]{64}")


def _read_bounded(path: Path) -> bytes:
    with path.open("rb") as handle:
        data = handle.read(MAX_BUNDLE_BYTES + 1)
    if len(data) > MAX_BUNDLE_BYTES:
        raise ValueError("file exceeds the 128 MiB review bundle size limit")
    return data


def _publish_new_file(output: Path, payload: bytes) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        temporary.write_bytes(payload)
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def build_bundle(source: bytes, report: bytes, output: Path, *, verify_fifo: bool = False) -> dict:
    """Check the two inputs before publishing a deterministic ZIP at a new path."""
    if len(source) + len(report) > MAX_BUNDLE_BYTES:
        raise ValueError("inputs exceed the 128 MiB review bundle size limit")
    result = verify_report(source, report, verify_fifo=verify_fifo)
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        for name, content in zip(MEMBERS, (source, report), strict=True):
            info = ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            info.compress_type = ZIP_STORED
            archive.writestr(info, content)
    payload = data.getvalue()
    if len(payload) > MAX_BUNDLE_BYTES:
        raise ValueError("ZIP exceeds the 128 MiB review bundle size limit")
    _publish_new_file(output, payload)
    return {**result, "bundle_sha256": sha256(payload).hexdigest()}


def verify_bundle(payload: bytes, *, expected_sha256: str | None = None, verify_fifo: bool = False) -> dict:
    """Read the two members directly without extracting any ZIP entries."""
    if len(payload) > MAX_BUNDLE_BYTES:
        raise ValueError("ZIP exceeds the 128 MiB review bundle size limit")
    digest = sha256(payload).hexdigest()
    if expected_sha256 is not None:
        if not _SHA256.fullmatch(expected_sha256):
            raise ValueError("expected bundle SHA256 must contain 64 hexadecimal characters")
        if digest != expected_sha256.lower():
            raise ValueError("ZIP SHA256 does not match the expected bundle SHA256")

    with ZipFile(BytesIO(payload)) as archive:
        entries = archive.infolist()
        if sorted(entry.filename for entry in entries) != sorted(MEMBERS):
            raise ValueError("ZIP must contain exactly one trades.csv and one report.json")
        for entry in entries:
            if (
                entry.orig_filename != entry.filename
                or entry.flag_bits & ~0x800
                or entry.compress_type != ZIP_STORED
                or entry.compress_size != entry.file_size
                or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)
                or entry.external_attr & 0x10
            ):
                raise ValueError("ZIP members must be regular, unencrypted, uncompressed files")
        if sum(entry.file_size for entry in entries) > MAX_BUNDLE_BYTES:
            raise ValueError("ZIP members exceed the 128 MiB review bundle size limit")
        source, report = (archive.read(name) for name in MEMBERS)
    return {**verify_report(source, report, verify_fifo=verify_fifo), "bundle_sha256": digest}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Verify the original CSV and report and package them")
    build.add_argument("source", type=Path)
    build.add_argument("report", type=Path)
    build.add_argument("--output", required=True, type=Path)
    verify = commands.add_parser("verify", help="Verify a bundle without extracting or installing packages")
    verify.add_argument("bundle", type=Path)
    verify.add_argument("--expected-sha256")
    verify.add_argument("--output-html", type=Path, help="Write a local receipt to a new .html file")
    for mode in (build, verify):
        mode.add_argument("--verify-fifo", action="store_true", help="Also independently match trades and verify FIFO")
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            if args.output.suffix.lower() != ".zip":
                raise ValueError("output must be a new ZIP file")
            result = build_bundle(
                _read_bounded(args.source.expanduser()),
                _read_bounded(args.report.expanduser()),
                args.output.expanduser(),
                verify_fifo=args.verify_fifo,
            )
        else:
            result = verify_bundle(
                _read_bounded(args.bundle.expanduser()),
                expected_sha256=args.expected_sha256,
                verify_fifo=args.verify_fifo,
            )
            if args.output_html is not None:
                from .review_receipt import render_receipt

                output = args.output_html.expanduser()
                if output.suffix.lower() != ".html":
                    raise ValueError("HTML receipt output must be a new .html file")
                html = render_receipt(result, matched_expected_sha256=args.expected_sha256 is not None)
                _publish_new_file(output, html)
    except (OSError, ValueError, OverflowError, BadZipFile, csv.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
