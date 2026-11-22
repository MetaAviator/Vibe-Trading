"""Build a private, source-bound journal report from a normalized USD trade CSV."""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Sequence
import csv
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from unittest.mock import patch

_COLUMNS = {"execution_id", "datetime", "symbol", "side", "quantity", "price", "amount", "fee", "currency"}
_DECIMAL = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")
_US_SYMBOL = re.compile(r"(?:[A-Z]+|[A-Z][A-Z0-9.-]*\.US)")


def _number(value: str, field: str, line: int) -> Decimal:
    if not _DECIMAL.fullmatch(value):
        raise ValueError(f"CSV line {line}: {field} must contain an explicit decimal")
    number = Decimal(value)
    native = float(number)
    if not math.isfinite(native) or (number and not native):
        raise ValueError(f"CSV line {line}: {field} is outside the supported numeric range")
    return number


def _check_fills(payload: bytes) -> dict:
    from backtest.engines._market_hooks import code_currency
    from src.tools.trade_journal_parsers import _infer_market_from_symbol

    rows = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""), strict=True)
    if not rows.fieldnames or len(rows.fieldnames) != len(_COLUMNS) or set(rows.fieldnames) != _COLUMNS:
        raise ValueError("expected exactly the normalized trade CSV columns listed in the guide")

    ids = set()
    fills = []
    purchases = sales = fees = Decimal(0)
    for row in rows:
        line = rows.line_num
        if None in row or any(value is None or not value.strip() for value in row.values()):
            raise ValueError(f"CSV line {line}: all columns are required; use 0 for a confirmed zero fee")
        identity = row["execution_id"].strip()
        if identity in ids:
            raise ValueError(f"CSV line {line}: duplicate execution_id")
        ids.add(identity)

        timestamp = row["datetime"]
        try:
            parsed = datetime.fromisoformat(timestamp)
        except ValueError:
            parsed = None
        if parsed is None or parsed.tzinfo is not None or parsed.isoformat(sep=" ", timespec="seconds") != timestamp:
            raise ValueError(f"CSV line {line}: datetime must be YYYY-MM-DD HH:MM:SS in source local time")

        symbol, side = row["symbol"], row["side"]
        if (
            row["currency"] != "USD"
            or not _US_SYMBOL.fullmatch(symbol)
            or _infer_market_from_symbol(symbol) != "us"
            or code_currency(symbol) != "USD"
        ):
            raise ValueError(f"CSV line {line}: expected a US equity symbol and USD")
        if side not in {"buy", "sell"}:
            raise ValueError(f"CSV line {line}: side must be buy or sell")
        quantity = _number(row["quantity"], "quantity", line)
        price = _number(row["price"], "price", line)
        amount = _number(row["amount"], "amount", line)
        fee = _number(row["fee"], "fee", line)
        if quantity <= 0 or price <= 0 or amount != quantity * price:
            raise ValueError(f"CSV line {line}: positive quantity and price must multiply to gross amount")
        if side == "buy":
            purchases += amount
        else:
            sales += amount
        fees += fee
        fills.append((timestamp, line, symbol, quantity if side == "buy" else -quantity))

    if not fills:
        raise ValueError("trade CSV contains no fills")
    quantities = defaultdict(Decimal)
    for _, line, symbol, change in sorted(fills):
        quantities[symbol] += change
        if quantities[symbol] < 0:
            raise ValueError(f"CSV line {line}: sell exceeds recorded long holdings")
    if any(quantities.values()):
        raise ValueError("all recorded positions must be closed at the end of the CSV")

    return {"rows": len(fills), "buy_gross": purchases, "sell_gross": sales, "fees": fees}


def build_report(payload: bytes) -> dict:
    from src.tools.trade_journal_tool import analyze_trade_journal

    observed = _check_fills(payload)
    # Analyze the same bytes that were hashed and checked. The staged name is
    # fixed so the report cannot acquire the user's filename or local path.
    with tempfile.TemporaryDirectory(prefix="journal-source-") as staging:
        snapshot = Path(staging) / "source.csv"
        snapshot.write_bytes(payload)
        with patch.dict(os.environ, {"VIBE_TRADING_ALLOWED_FILE_ROOTS": staging}):
            analysis = json.loads(analyze_trade_journal(str(snapshot), analysis_type="profile"))
    if analysis.get("status") != "ok":
        raise ValueError(analysis.get("error", "journal analysis failed"))
    if analysis.get("format_detected") != "generic" or analysis["total_records"] != observed["rows"]:
        raise ValueError("analysis did not preserve the validated trade CSV rows")
    profile = analysis["profile"]
    net_pnl = profile.get("total_pnl")
    if not isinstance(net_pnl, (int, float)) or not math.isfinite(net_pnl):
        raise ValueError("FIFO report does not contain a finite USD total")
    fifo_net = Decimal(str(net_pnl))
    cash_net = observed["sell_gross"] - observed["buy_gross"] - observed["fees"]
    return {
        "schema_version": 1,
        "source_sha256": sha256(payload).hexdigest(),
        "source_rows": observed["rows"],
        "currency": "USD",
        "cash_check": {
            "buy_gross": format(observed["buy_gross"], "f"),
            "sell_gross": format(observed["sell_gross"], "f"),
            "fees": format(observed["fees"], "f"),
            "net_trade_cash": format(cash_net, "f"),
            "fifo_minus_cash": format(fifo_net - cash_net, "f"),
        },
        "date_range": analysis["date_range"],
        "profile": profile,
    }


def _write_new_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    from pytest_socket import disable_socket

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Normalized UTF-8 trade CSV")
    parser.add_argument("--output", required=True, type=Path, help="New local JSON report path")
    args = parser.parse_args(argv)
    disable_socket()
    try:
        source = args.source.expanduser()
        if source.suffix.lower() != ".csv" or not source.is_file():
            raise ValueError("source must be an existing CSV file")
        if args.output.suffix.lower() != ".json":
            raise ValueError("output must be a new JSON file")
        _write_new_report(args.output.expanduser(), build_report(source.read_bytes()))
    except (OSError, ValueError, csv.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
