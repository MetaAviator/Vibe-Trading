"""Version-one research bundle contract and read-only verification."""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
import stat
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .sharadar_prices import PRICE_BASES

CONTENT_NAMES = ("request.json", "prices.csv", "metadata.json", "REPORT.md")
MEMBER_NAMES = (*CONTENT_NAMES, "manifest.json")
MAX_BUNDLE_BYTES = 128 * 1024 * 1024
REQUEST_KEYS = {
    "schema_version",
    "source_sha256",
    "source_manifest_sha256",
    "tickers",
    "start",
    "end",
    "price_field",
    "max_rows",
}


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON constants are not allowed")


def _parse_json(data: bytes) -> Any:
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("invalid UTF-8 JSON") from None


def _check_digest(value: Any) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("expected a lowercase SHA256 digest")


def _date(value: Any) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        raise ValueError("expected a valid ISO YYYY-MM-DD date") from None


def _check_request(request: Any) -> None:
    if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
        raise ValueError("invalid request fields")
    if type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise ValueError("unsupported request schema_version")
    _check_digest(request["source_sha256"])
    if request["source_manifest_sha256"] is not None:
        _check_digest(request["source_manifest_sha256"])
    tickers = request["tickers"]
    if (
        not isinstance(tickers, list)
        or not tickers
        or any(
            not isinstance(t, str) or not t or t != t.strip().upper() for t in tickers
        )
        or tickers != sorted(set(tickers))
    ):
        raise ValueError("request tickers must be normalized, unique and sorted")
    if _date(request["start"]) > _date(request["end"]):
        raise ValueError("request start must not be later than end")
    if (
        not isinstance(request["price_field"], str)
        or request["price_field"] not in PRICE_BASES
    ):
        raise ValueError("invalid request price_field")
    if type(request["max_rows"]) is not int or request["max_rows"] < 1:
        raise ValueError("request max_rows must be a positive integer")


def _coverage(data: bytes, request: dict[str, Any]) -> tuple[dict[str, Any], int]:
    reader = csv.reader(io.StringIO(data.decode("utf-8"), newline=""), strict=True)
    if next(reader, []) != ["date", "ticker", "close", "lastupdated"]:
        raise ValueError("invalid prices.csv header")
    coverage = {}
    previous = None
    count = 0
    for row in reader:
        if len(row) != 4:
            raise ValueError("invalid prices.csv row width")
        day, ticker, price, updated = row
        _date(day)
        if (
            ticker not in request["tickers"]
            or not request["start"] <= day <= request["end"]
        ):
            raise ValueError("price lies outside the requested selection")
        key = (day, ticker)
        if previous is not None and key <= previous:
            raise ValueError("prices must have unique sorted date/ticker keys")
        previous = key
        try:
            number = Decimal(price)
        except InvalidOperation:
            raise ValueError("bundle price must be finite and positive") from None
        if not number.is_finite() or number <= 0:
            raise ValueError("bundle price must be finite and positive")
        if _date(updated) < _date(day):
            raise ValueError("lastupdated precedes the price date")
        count += 1
        if count > request["max_rows"]:
            raise ValueError("bundle exceeds the requested max_rows")
        item = coverage.setdefault(ticker, dict(first_date=day, last_date=day, rows=0))
        item["last_date"] = day
        item["rows"] += 1
    if set(coverage) != set(request["tickers"]):
        raise ValueError("bundle has no prices for one or more requested tickers")
    return dict(sorted(coverage.items())), count


def _check_metadata(
    metadata: Any, request: dict[str, Any], coverage: dict[str, Any], count: int
) -> None:
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    expected = {
        "source": "Sharadar",
        "table": "stocks/SEP",
        "currency": "USD",
        "unit": "USD/share",
        "frequency": "daily_EOD",
        "price_field": request["price_field"],
        "price_basis": PRICE_BASES[request["price_field"]],
        "snapshot_sha256": request["source_sha256"],
        "manifest_sha256": request["source_manifest_sha256"],
        "filters": {k: request[k] for k in ("tickers", "start", "end")},
        "observed_coverage": coverage,
        "point_in_time_status": "not_verified; lastupdated is a revision date",
        "expected_calendar_coverage": "not_verified",
        "permanent_security_identity": "not_verified",
        "validation_scope": {
            "whole_file": ["raw-byte hash", "row width", "nonempty ticker"],
            "requested_tickers": ["valid dates"],
            "selected_window": [
                "unique keys",
                "positive finite price",
                "valid lastupdated",
            ],
        },
    }
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise ValueError(
            "metadata conflicts with the request, prices or research limits"
        )
    for item in metadata["observed_coverage"].values():
        if type(item["rows"]) is not int:
            raise ValueError("coverage rows must be integers")
    if type(metadata.get("input_bytes")) is not int or metadata["input_bytes"] < 1:
        raise ValueError("metadata input_bytes must be positive")
    if (
        type(metadata.get("source_rows_scanned")) is not int
        or metadata["source_rows_scanned"] < count
    ):
        raise ValueError("metadata source row count is inconsistent")
    extracted = metadata.get("extracted_at")
    timezone = "unknown"
    if extracted is not None:
        if not isinstance(extracted, str):
            raise ValueError("invalid extracted_at")
        if datetime.fromisoformat(extracted).tzinfo is not None:
            timezone = "recorded_in_timestamp"
    if metadata.get("extracted_at_timezone") != timezone:
        raise ValueError("extraction timezone must preserve unknown status")
    evidence = "local_manifest_record" if extracted else "unknown"
    if metadata.get("extracted_at_evidence") != evidence:
        raise ValueError("invalid extraction evidence status")


def _md(value: Any) -> str:
    return (
        html.escape(str(value))
        .replace("|", r"\|")
        .replace("`", r"\`")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def _render_report(metadata: dict[str, Any], request: dict[str, Any]) -> bytes:
    rows = sum(item["rows"] for item in metadata["observed_coverage"].values())
    lines = [
        "# Research input bundle",
        "",
        f"Source schema: Sharadar stocks/SEP; snapshot SHA256: `{request['source_sha256']}`.",
        f"Selected field: `{request['price_field']}`; basis: `{metadata['price_basis']}`; USD/share; daily EOD.",
        f"Requested dates (inclusive): {request['start']} to {request['end']}. Selected rows: {rows}.",
        f"Extraction timestamp recorded: {_md(metadata['extracted_at'] or 'unknown')}; timezone: {_md(metadata['extracted_at_timezone'])}.",
        "",
        "| Ticker | Observed first date | Observed last date | Rows |",
        "|---|---|---|---:|",
    ]
    for ticker, item in metadata["observed_coverage"].items():
        lines.append(
            f"| {_md(ticker)} | {item['first_date']} | {item['last_date']} | {item['rows']} |"
        )
    lines += [
        "",
        "Coverage is observed only. Expected trading-calendar coverage, permanent security identity, and point-in-time availability are not verified.",
        "lastupdated is a revision date. The CSV close column contains the explicitly selected source field; keep metadata.json and request.json with it.",
        "Checksums verify package integrity and file identity, not supplier authenticity, investment results, or account transactions.",
        "Source paths are intentionally omitted. Rebuild with the original local task and the pinned local snapshot.",
        "The ZIP container uses a fixed technical timestamp for reproducible bytes; it is not a data extraction or market timestamp.",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def verify_bundle(
    bundle_path: str | Path, *, expected_package_id: str | None = None
) -> dict[str, Any]:
    """Verify an input bundle without extracting it or reading its source CSV.

    Args:
        bundle_path: Local version-one ZIP bundle.
        expected_package_id: Optional previously recorded content identity.

    Returns:
        Package identity, archive hash, selected row count, basis and coverage.

    Raises:
        ValueError: The package format, hashes or semantic contracts disagree.
        OSError: The local bundle cannot be read.
    """
    path = Path(bundle_path)
    if expected_package_id is not None:
        _check_digest(expected_package_id)
    if path.stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError("bundle exceeds the version-one size limit")
    with path.open("rb") as stream:
        archive_bytes = stream.read(MAX_BUNDLE_BYTES + 1)
    if len(archive_bytes) > MAX_BUNDLE_BYTES:
        raise ValueError("bundle exceeds the version-one size limit")
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            members = archive.infolist()
            if len(members) != len(MEMBER_NAMES) or {
                m.filename for m in members
            } != set(MEMBER_NAMES):
                raise ValueError(
                    "bundle must contain exactly the five approved members"
                )
            if sum(m.file_size for m in members) > MAX_BUNDLE_BYTES:
                raise ValueError("bundle members exceed the size limit")
            for member in members:
                if (
                    member.flag_bits & 1
                    or member.compress_type != zipfile.ZIP_STORED
                    or stat.S_ISLNK(member.external_attr >> 16)
                ):
                    raise ValueError(
                        "encrypted, compressed or symbolic-link members are not allowed"
                    )
            contents = {name: archive.read(name) for name in MEMBER_NAMES}
    except (zipfile.BadZipFile, csv.Error):
        raise ValueError("invalid bundle ZIP") from None
    manifest = _parse_json(contents["manifest.json"])
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version",
        "package_id",
        "builder",
        "files",
    }:
        raise ValueError("invalid manifest fields")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise ValueError("unsupported manifest schema_version")
    _check_digest(manifest["package_id"])
    builder = manifest["builder"]
    if (
        not isinstance(builder, dict)
        or set(builder)
        != {
            "name",
            "writer_sha256",
            "adapter_sha256",
            "checker_sha256",
            "python_version",
        }
        or builder["name"] != "journal-research-input-v1"
    ):
        raise ValueError("invalid builder provenance")
    if not isinstance(builder["python_version"], str) or not re.fullmatch(
        r"\d+\.\d+\.\d+", builder["python_version"]
    ):
        raise ValueError("invalid recorded Python version")
    for key in ("writer_sha256", "adapter_sha256", "checker_sha256"):
        _check_digest(builder[key])
    files = manifest["files"]
    if not isinstance(files, dict) or set(files) != set(CONTENT_NAMES):
        raise ValueError("invalid manifest file inventory")
    for name in CONTENT_NAMES:
        facts = files[name]
        if (
            not isinstance(facts, dict)
            or set(facts) != {"bytes", "sha256"}
            or type(facts["bytes"]) is not int
        ):
            raise ValueError("invalid member facts")
        if facts["bytes"] != len(contents[name]) or facts["sha256"] != _digest(
            contents[name]
        ):
            raise ValueError("bundle member checksum mismatch")
    identity = _digest(
        _json_bytes(
            {key: value for key, value in manifest.items() if key != "package_id"}
        )
    )
    if identity != manifest["package_id"] or (
        expected_package_id is not None and identity != expected_package_id
    ):
        raise ValueError("package_id mismatch")
    request, metadata = _parse_json(contents["request.json"]), _parse_json(
        contents["metadata.json"]
    )
    _check_request(request)
    try:
        coverage, count = _coverage(contents["prices.csv"], request)
    except (csv.Error, UnicodeError):
        raise ValueError("invalid prices.csv") from None
    _check_metadata(metadata, request, coverage, count)
    if contents["REPORT.md"] != _render_report(metadata, request):
        raise ValueError("report conflicts with verified metadata")
    return {
        "package_id": identity,
        "archive_sha256": _digest(archive_bytes),
        "rows": count,
        "price_field": request["price_field"],
        "price_basis": metadata["price_basis"],
        "snapshot_sha256": request["source_sha256"],
        "observed_coverage": coverage,
    }
