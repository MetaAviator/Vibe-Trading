"""Read a pinned local Sharadar price snapshot; no downloads or account data."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, BinaryIO

PRICE_BASES = {
    "close": "split_adjusted",
    "closeadj": "split_dividend_spinoff_adjusted",
    "closeunadj": "unadjusted",
}


class _HashingReader(io.RawIOBase):
    """Hash the same bytes the CSV parser consumes, including BOM/newlines."""

    def __init__(self, stream: BinaryIO) -> None:
        super().__init__()
        self.stream = stream
        self.digest = hashlib.sha256()
        self.size = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray | memoryview) -> int | None:
        size = self.stream.readinto(buffer)
        if size:
            self.digest.update(memoryview(buffer)[:size])
            self.size += size
        return size


def _file_state(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _iso_date(value: str, label: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{label} must be an ISO YYYY-MM-DD date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label} must be a valid date") from None


def read_sharadar_prices(
    path: str | Path,
    *,
    tickers: Iterable[str],
    start: str,
    end: str,
    price_field: str,
    expected_sha256: str,
    manifest_path: str | Path | None = None,
    max_rows: int = 100_000,
) -> dict[str, Any]:
    """Return validated prices and provenance for an explicit ticker/date subset.

    All source bytes are hashed; values and duplicate keys are validated in the
    returned subset. Requested tickers' dates must be valid even outside the
    window so malformed dates cannot disappear through filtering. This is not
    a full-source quality audit, calendar-completeness check, or PIT guarantee.

    Args:
        path: Existing local stocks/SEP CSV snapshot.
        tickers: Nonempty collection of source ticker strings.
        start: Inclusive first price date in ISO YYYY-MM-DD format.
        end: Inclusive last price date in ISO YYYY-MM-DD format.
        price_field: Explicit close, closeadj, or closeunadj source column.
        expected_sha256: Previously recorded digest of the complete source file.
        manifest_path: Optional existing pull manifest with a stocks entry.
        max_rows: Maximum selected rows; overflow fails without truncation.

    Returns:
        A metadata dictionary and sorted prices with decimal text preserved.

    Raises:
        ValueError: The request, selected records, snapshot, or manifest is invalid.
        OSError: A local input cannot be read.
    """
    if price_field not in PRICE_BASES:
        raise ValueError("price_field must be close, closeadj, or closeunadj")
    first, last = _iso_date(start, "start"), _iso_date(end, "end")
    if first > last:
        raise ValueError("start must not be later than end")
    if isinstance(tickers, str):
        raise ValueError("tickers must be a collection of ticker strings")
    requested = set()
    for ticker in tickers:
        if not isinstance(ticker, str) or not ticker.strip():
            raise ValueError("requested tickers must be nonempty strings")
        requested.add(ticker.strip().upper())
    if not requested:
        raise ValueError("at least one ticker is required")
    if not isinstance(expected_sha256, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", expected_sha256
    ):
        raise ValueError("expected_sha256 must be a 64-character SHA256 digest")
    if type(max_rows) is not int or max_rows < 1:
        raise ValueError("max_rows must be a positive integer")

    path = Path(path)
    before = _file_state(path)
    rows, seen, coverage = [], set(), {}
    source_rows = 0
    with path.open("rb") as binary:
        hashing = _HashingReader(binary)
        with io.TextIOWrapper(
            io.BufferedReader(hashing), encoding="utf-8-sig", newline=""
        ) as text:
            reader = csv.reader(text, strict=True)
            try:
                columns = next(reader, [])
            except csv.Error:
                raise ValueError("invalid CSV header syntax") from None
            required = {"ticker", "date", price_field, "lastupdated"}
            if len(columns) != len(set(columns)) or not required.issubset(columns):
                raise ValueError(
                    "CSV needs unique ticker/date/price_field/lastupdated columns"
                )
            ticker_i, date_i, price_i, update_i = (
                columns.index(field)
                for field in ("ticker", "date", price_field, "lastupdated")
            )
            try:
                for record in reader:
                    source_rows += 1
                    if len(record) != len(columns):
                        raise ValueError(
                            f"CSV row {source_rows + 1} has the wrong column count"
                        )
                    ticker = record[ticker_i].strip().upper()
                    if not ticker:
                        raise ValueError(
                            f"CSV row {source_rows + 1} has an empty ticker"
                        )
                    if ticker not in requested:
                        continue
                    day = _iso_date(record[date_i], "requested ticker date")
                    if not first <= day <= last:
                        continue
                    key = ticker, day
                    if key in seen:
                        raise ValueError(
                            "duplicate normalized ticker/date in selected prices"
                        )
                    seen.add(key)
                    value = record[price_i].strip()
                    try:
                        number = Decimal(value)
                    except InvalidOperation:
                        raise ValueError(
                            "selected price must be finite and positive"
                        ) from None
                    if not number.is_finite() or number <= 0:
                        raise ValueError("selected price must be finite and positive")
                    updated = _iso_date(record[update_i], "selected lastupdated")
                    if updated < day:
                        raise ValueError("selected lastupdated precedes its price date")
                    if len(rows) >= max_rows:
                        raise ValueError(
                            "selected prices exceed max_rows; narrow the request"
                        )
                    rows.append(
                        dict(
                            ticker=ticker,
                            date=day.isoformat(),
                            price=value,
                            lastupdated=updated.isoformat(),
                        )
                    )
                    item = coverage.setdefault(
                        ticker,
                        dict(
                            first_date=day.isoformat(),
                            last_date=day.isoformat(),
                            rows=0,
                        ),
                    )
                    item["first_date"] = min(item["first_date"], day.isoformat())
                    item["last_date"] = max(item["last_date"], day.isoformat())
                    item["rows"] += 1
            except csv.Error:
                raise ValueError("invalid CSV syntax") from None

    actual_hash = hashing.digest.hexdigest()
    if _file_state(path) != before:
        raise ValueError("input file changed during the read")
    if actual_hash != expected_sha256.lower():
        raise ValueError("snapshot SHA256 does not match expected_sha256")
    if requested - coverage.keys():
        raise ValueError("one or more requested tickers have no prices in the window")

    extracted_at, extraction_timezone, manifest_hash = None, "unknown", None
    if manifest_path is not None:
        manifest_bytes = Path(manifest_path).read_bytes()
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        try:
            facts = json.loads(manifest_bytes)["stocks"]
            matches = (
                facts["sha256"] == actual_hash
                and facts["bytes"] == hashing.size
                and facts["lines_minus_header"] == source_rows
                and facts["cols"] == columns
            )
            if not matches:
                raise ValueError("manifest does not match the snapshot")
            extracted_at = facts.get("pulled_at")
            if extracted_at is not None:
                stamp = datetime.fromisoformat(extracted_at)
                if stamp.tzinfo is not None:
                    extraction_timezone = "recorded_in_timestamp"
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ValueError(
                "manifest must contain valid stocks snapshot facts"
            ) from None

    rows.sort(key=lambda row: (row["date"], row["ticker"]))
    return {
        "metadata": {
            "source": "Sharadar",
            "table": "stocks/SEP",
            "currency": "USD",
            "unit": "USD/share",
            "frequency": "daily_EOD",
            "price_field": price_field,
            "price_basis": PRICE_BASES[price_field],
            "snapshot_sha256": actual_hash,
            "input_bytes": hashing.size,
            "source_rows_scanned": source_rows,
            "extracted_at": extracted_at,
            "extracted_at_timezone": extraction_timezone,
            "extracted_at_evidence": (
                "local_manifest_record" if extracted_at else "unknown"
            ),
            "manifest_sha256": manifest_hash,
            "filters": dict(tickers=sorted(requested), start=start, end=end),
            "observed_coverage": dict(sorted(coverage.items())),
            "validation_scope": {
                "whole_file": ["raw-byte hash", "row width", "nonempty ticker"],
                "requested_tickers": ["valid dates"],
                "selected_window": [
                    "unique keys",
                    "positive finite price",
                    "valid lastupdated",
                ],
            },
            "point_in_time_status": "not_verified; lastupdated is a revision date",
            "expected_calendar_coverage": "not_verified",
            "permanent_security_identity": "not_verified",
        },
        "prices": rows,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run the local price CLI and create a new JSON output without replacement.

    Args:
        argv: CLI arguments, or None to use the process arguments.

    Returns:
        Zero after writing a result, or two for input/output validation errors.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument(
        "--tickers", required=True, help="Comma-separated source ticker symbols"
    )
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--price-field", required=True, choices=tuple(PRICE_BASES))
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--max-rows", type=int, default=100_000)
    parser.add_argument(
        "--output", type=Path, required=True, help="New local JSON file"
    )
    args = parser.parse_args(argv)
    temporary = None
    try:
        if args.output.exists():
            raise ValueError(
                "output must be a new file; existing files are never replaced"
            )
        result = read_sharadar_prices(
            args.input,
            tickers=args.tickers.split(","),
            start=args.start,
            end=args.end,
            price_field=args.price_field,
            expected_sha256=args.expected_sha256,
            manifest_path=args.manifest,
            max_rows=args.max_rows,
        )
        payload = (
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.output.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        # Atomic creation without overwriting a file created after the check.
        os.link(temporary, args.output)
    except (OSError, ValueError, UnicodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"Wrote {len(result['prices'])} prices to the new local output file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
