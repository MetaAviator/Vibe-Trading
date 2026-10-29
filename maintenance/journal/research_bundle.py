"""Build or verify reproducible local research input bundles."""

from __future__ import annotations

import argparse
import csv
import io
import os
import platform
import stat
import sys
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .research_bundle_check import (
    CONTENT_NAMES,
    MAX_BUNDLE_BYTES,
    _check_request,
    _digest,
    _json_bytes,
    _parse_json,
    _render_report,
    verify_bundle,
)
from .sharadar_prices import read_sharadar_prices

_REQUIRED = {
    "schema_version",
    "source_csv",
    "expected_sha256",
    "tickers",
    "start",
    "end",
    "price_field",
}
_OPTIONAL = {"manifest", "max_rows"}


def _local_path(base: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value.strip() or "://" in value:
        raise ValueError("source paths must be nonempty local path strings")
    return (base / value).resolve()


def _load_task(task_path: Path) -> tuple[dict[str, Any], Path, Path | None]:
    with task_path.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("task JSON exceeds 1 MiB")
    config = _parse_json(raw)
    if (
        not isinstance(config, dict)
        or not _REQUIRED.issubset(config)
        or set(config) - (_REQUIRED | _OPTIONAL)
    ):
        raise ValueError("task has missing or unknown fields")
    tickers, expected = config["tickers"], config["expected_sha256"]
    if not isinstance(tickers, list) or any(
        not isinstance(t, str) or not t.strip() for t in tickers
    ):
        raise ValueError("task tickers must be a nonempty list of ticker strings")
    if not isinstance(expected, str):
        raise ValueError("task expected_sha256 must be a string")
    request = {
        "schema_version": config["schema_version"],
        "source_sha256": expected.lower(),
        "source_manifest_sha256": None,
        "tickers": sorted({ticker.strip().upper() for ticker in tickers}),
        "start": config["start"],
        "end": config["end"],
        "price_field": config["price_field"],
        "max_rows": config.get("max_rows", 100_000),
    }
    _check_request(request)
    base = task_path.resolve().parent
    source = _local_path(base, config["source_csv"])
    manifest = _local_path(base, config["manifest"]) if "manifest" in config else None
    return request, source, manifest


def _price_csv(prices: list[dict[str, str]]) -> bytes:
    text = io.StringIO(newline="")
    writer = csv.writer(text, lineterminator="\n")
    writer.writerow(["date", "ticker", "close", "lastupdated"])
    writer.writerows(
        (row["date"], row["ticker"], row["price"], row["lastupdated"]) for row in prices
    )
    return text.getvalue().encode("utf-8")


def build_bundle(task_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    """Build and verify a new bundle from a pinned local task configuration.

    Args:
        task_path: Local JSON configuration; relative input paths use its directory.
        output_path: New ZIP filename. Existing files and symlinks are never replaced.

    Returns:
        The verified package identity, archive hash, row count and observed coverage.

    Raises:
        ValueError: Configuration, snapshot, selected data or package is invalid.
        OSError: A local input/output operation fails or the output already exists.
    """
    output = Path(output_path)
    if output.exists() or output.is_symlink():
        raise ValueError("output must be a new file; existing files are never replaced")
    request, source, manifest_path = _load_task(Path(task_path))
    result = read_sharadar_prices(
        source,
        tickers=request["tickers"],
        start=request["start"],
        end=request["end"],
        price_field=request["price_field"],
        expected_sha256=request["source_sha256"],
        manifest_path=manifest_path,
        max_rows=request["max_rows"],
    )
    metadata = result["metadata"]
    request["source_manifest_sha256"] = metadata["manifest_sha256"]
    _check_request(request)
    contents = {
        "request.json": _json_bytes(request),
        "prices.csv": _price_csv(result["prices"]),
        "metadata.json": _json_bytes(metadata),
        "REPORT.md": _render_report(metadata, request),
    }
    directory = Path(__file__).resolve().parent
    manifest = {
        "schema_version": 1,
        "builder": {
            "name": "journal-research-input-v1",
            "writer_sha256": _digest(Path(__file__).read_bytes()),
            "checker_sha256": _digest(
                (directory / "research_bundle_check.py").read_bytes()
            ),
            "adapter_sha256": _digest((directory / "sharadar_prices.py").read_bytes()),
            "python_version": platform.python_version(),
        },
        "files": {
            name: {"bytes": len(contents[name]), "sha256": _digest(contents[name])}
            for name in CONTENT_NAMES
        },
    }
    identity = _digest(_json_bytes(manifest))
    manifest["package_id"] = identity
    contents["manifest.json"] = _json_bytes(manifest)
    if sum(map(len, contents.values())) > MAX_BUNDLE_BYTES:
        raise ValueError(
            "bundle exceeds the version-one size limit; narrow the request"
        )

    temporary = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=output.parent, suffix=".zip", delete=False
        ) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(
            temporary, mode="w", compression=zipfile.ZIP_STORED
        ) as archive:
            for name, payload in contents.items():
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                archive.writestr(info, payload)
        summary = verify_bundle(temporary, expected_package_id=identity)
        # A hard link creates one complete output and rejects concurrent writers.
        os.link(temporary, output)
        return summary
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the build/verify CLI from the repository root.

    Args:
        argv: CLI arguments, or None to use process arguments.

    Returns:
        Zero for success or two for a local validation/input/output error.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser(
        "build", help="Build a new package from a local task JSON"
    )
    build.add_argument("task", type=Path)
    build.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser(
        "verify", help="Check a package without extracting it"
    )
    verify.add_argument("bundle", type=Path)
    verify.add_argument("--expected-id")
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            summary = build_bundle(args.task, args.output)
        else:
            summary = verify_bundle(args.bundle, expected_package_id=args.expected_id)
    except (OSError, ValueError, UnicodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(_json_bytes(summary).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
