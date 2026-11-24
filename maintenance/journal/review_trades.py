"""Build or check a local journal report against a normalized USD trade CSV."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
from collections.abc import Sequence
import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
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


@dataclass(frozen=True)
class Fill:
    identity: str
    timestamp: str
    line: int
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal


@dataclass(frozen=True)
class Match:
    buy: Fill
    sell: Fill
    quantity: Fraction

    def allocated_fees(self) -> tuple[Fraction, Fraction]:
        return (
            Fraction(self.buy.fee) * self.quantity / Fraction(self.buy.quantity),
            Fraction(self.sell.fee) * self.quantity / Fraction(self.sell.quantity),
        )

    def net_cents(self) -> int:
        buy_fee, sell_fee = self.allocated_fees()
        gross = (Fraction(self.sell.price) - Fraction(self.buy.price)) * self.quantity
        return round((gross - buy_fee - sell_fee) * 100)


def _number(value: str, field: str, line: int) -> Decimal:
    if not _DECIMAL.fullmatch(value):
        raise ValueError(f"CSV line {line}: {field} must contain an explicit decimal")
    number = Decimal(value)
    native = float(number)
    if not math.isfinite(native) or (number and not native):
        raise ValueError(f"CSV line {line}: {field} is outside the supported numeric range")
    return number


def _check_fills(payload: bytes) -> dict:
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
        if row["currency"] != "USD" or not _US_SYMBOL.fullmatch(symbol):
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
        fills.append(Fill(identity, timestamp, line, symbol, side, quantity, price, fee))

    if not fills:
        raise ValueError("trade CSV contains no fills")
    quantities = defaultdict(Fraction)
    ordered = sorted(fills, key=lambda fill: (fill.timestamp, fill.line))
    for fill in ordered:
        quantity = Fraction(fill.quantity)
        quantities[fill.symbol] += quantity if fill.side == "buy" else -quantity
        if quantities[fill.symbol] < 0:
            raise ValueError(f"CSV line {fill.line}: sell exceeds recorded long holdings")
    if any(quantities.values()):
        raise ValueError("all recorded positions must be closed at the end of the CSV")

    return {
        "rows": len(fills),
        "buy_gross": purchases,
        "sell_gross": sales,
        "fees": fees,
        "symbols": quantities.keys(),
        "date_range": f"{ordered[0].timestamp[:10]} ~ {ordered[-1].timestamp[:10]}",
        "fills": ordered,
    }


def _cash_check(observed: dict, fifo_net: Decimal) -> dict[str, str]:
    cash_net = observed["sell_gross"] - observed["buy_gross"] - observed["fees"]
    return {
        "buy_gross": format(observed["buy_gross"], "f"),
        "sell_gross": format(observed["sell_gross"], "f"),
        "fees": format(observed["fees"], "f"),
        "net_trade_cash": format(cash_net, "f"),
        "fifo_minus_cash": format(fifo_net - cash_net, "f"),
    }


def build_report(payload: bytes) -> dict:
    from backtest.engines._market_hooks import code_currency
    from src.config.accessor import reset_env_config
    from src.tools.trade_journal_parsers import _infer_market_from_symbol
    from src.tools.trade_journal_tool import analyze_trade_journal

    observed = _check_fills(payload)
    if any(
        _infer_market_from_symbol(symbol) != "us" or code_currency(symbol) != "USD" for symbol in observed["symbols"]
    ):
        raise ValueError("expected only US equity symbols in the trade CSV")
    # Analyze the same bytes that were hashed and checked. The staged name is
    # fixed so the report cannot acquire the user's filename or local path.
    with tempfile.TemporaryDirectory(prefix="journal-source-") as staging:
        snapshot = Path(staging) / "source.csv"
        snapshot.write_bytes(payload)
        try:
            with patch.dict(os.environ, {"VIBE_TRADING_ALLOWED_FILE_ROOTS": staging}):
                reset_env_config()
                analysis = json.loads(analyze_trade_journal(str(snapshot), analysis_type="profile"))
        finally:
            reset_env_config()
    if analysis.get("status") != "ok":
        raise ValueError(analysis.get("error", "journal analysis failed"))
    if analysis.get("format_detected") != "generic" or analysis["total_records"] != observed["rows"]:
        raise ValueError("analysis did not preserve the validated trade CSV rows")
    profile = analysis["profile"]
    net_pnl = profile.get("total_pnl")
    if not isinstance(net_pnl, (int, float)) or not math.isfinite(net_pnl):
        raise ValueError("FIFO report does not contain a finite USD total")
    fifo_net = Decimal(str(net_pnl))
    return {
        "schema_version": 1,
        "source_sha256": sha256(payload).hexdigest(),
        "source_rows": observed["rows"],
        "currency": "USD",
        "cash_check": _cash_check(observed, fifo_net),
        "date_range": analysis["date_range"],
        "profile": profile,
    }


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("report JSON contains duplicate fields")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"report JSON contains a non-finite number: {value}")


def _match_fills(fills: list[Fill]) -> list[Match]:
    """Independently match validated closed positions by timestamp and source order."""
    queues: dict[str, deque[tuple[Fill, Fraction]]] = defaultdict(deque)
    matches = []
    for fill in fills:
        queue = queues[fill.symbol]
        remaining = Fraction(fill.quantity)
        if fill.side == "buy":
            queue.append((fill, remaining))
            continue
        while remaining:
            buy, available = queue.popleft()
            matched = min(remaining, available)
            matches.append(Match(buy, fill, matched))
            remaining -= matched
            if matched < available:
                queue.appendleft((buy, available - matched))
    return matches


def _same_report_time(value: object, source_time: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value) == datetime.fromisoformat(source_time)
    except ValueError:
        return False


def _match_sample(sample: object, match: Match) -> bool:
    if not isinstance(sample, dict):
        return False
    quantity, buy_price, sell_price, pnl = (sample.get(key) for key in ("qty", "buy_price", "sell_price", "pnl"))
    if any(type(value) not in (int, Decimal) for value in (quantity, buy_price, sell_price, pnl)):
        return False
    return (
        sample.get("symbol") == match.buy.symbol
        and sample.get("side") == "long"
        and sample.get("currency") == "USD"
        and _same_report_time(sample.get("buy_dt"), match.buy.timestamp)
        and _same_report_time(sample.get("sell_dt"), match.sell.timestamp)
        and math.isclose(float(quantity), float(match.quantity), rel_tol=1e-12)
        and float(buy_price) == float(match.buy.price)
        and float(sell_price) == float(match.sell.price)
        and pnl == Decimal(match.net_cents()).scaleb(-2)
    )


def _fifo_audit(fills: list[Fill], profile: dict, fifo_net: Decimal) -> dict:
    matches = _match_fills(fills)
    reported_count = profile.get("total_roundtrips")
    if type(reported_count) is not int or reported_count != len(matches):
        raise ValueError("FIFO match count differs from the CSV")
    total = Decimal(sum(match.net_cents() for match in matches)).scaleb(-2)
    if total != fifo_net:
        raise ValueError(f"FIFO total differs: report={fifo_net}, independent={total} (half-even cents per match)")
    samples = profile.get("roundtrips_sample")
    expected_samples = matches[:5]
    if not isinstance(samples, list) or len(samples) != len(expected_samples):
        raise ValueError("FIFO report sample count differs from the expected sample")
    for index, (sample, match) in enumerate(zip(samples, expected_samples), 1):
        if not _match_sample(sample, match):
            raise ValueError(f"FIFO report sample {index} differs from the independently matched trade")
    details = []
    for match in matches:
        buy_fee, sell_fee = match.allocated_fees()
        details.append(
            {
                "symbol": match.buy.symbol,
                "buy_execution_id": match.buy.identity,
                "sell_execution_id": match.sell.identity,
                "quantity_exact": str(match.quantity),
                "buy_fee_usd_exact": str(buy_fee),
                "sell_fee_usd_exact": str(sell_fee),
                "net_pnl_usd": format(Decimal(match.net_cents()).scaleb(-2), "f"),
            }
        )
    return {
        "fifo_check": "independent_total_and_reported_sample",
        "recomputed_fifo_pnl": format(total, "f"),
        "fifo_match_count": len(matches),
        "fifo_samples_checked": len(samples),
        "fifo_matches": details,
    }


def verify_report(source: bytes, report_bytes: bytes, *, verify_fifo: bool = False) -> dict:
    """Check source identity, cash, coverage and the reported FIFO difference."""
    report = json.loads(
        report_bytes.decode("utf-8"),
        parse_float=Decimal,
        parse_constant=_reject_json_constant,
        object_pairs_hook=_unique_object,
    )
    if not isinstance(report, dict) or type(report.get("schema_version")) is not int or report["schema_version"] != 1:
        raise ValueError("expected a version 1 journal report")
    source_id = sha256(source).hexdigest()
    if report.get("source_sha256") != source_id:
        raise ValueError("source CSV SHA256 does not match the report")
    observed = _check_fills(source)
    if (
        report.get("currency") != "USD"
        or type(report.get("source_rows")) is not int
        or report["source_rows"] != observed["rows"]
        or report.get("date_range") != observed["date_range"]
    ):
        raise ValueError("report currency, date range or row count differs from the CSV")
    profile = report.get("profile")
    net_pnl = profile.get("total_pnl") if isinstance(profile, dict) else None
    if type(net_pnl) not in (int, Decimal):
        raise ValueError("report does not contain a finite numeric FIFO total")
    native = float(net_pnl)
    if not math.isfinite(native) or (net_pnl and not native):
        raise ValueError("report FIFO total is outside the supported numeric range")
    fifo_net = Decimal(net_pnl)
    cash = _cash_check(observed, fifo_net)
    if report.get("cash_check") != cash:
        raise ValueError("cash amounts or FIFO difference do not match the recalculation")
    result = {
        "status": "ok",
        "source_sha256": source_id,
        "report_sha256": sha256(report_bytes).hexdigest(),
        "source_rows": observed["rows"],
        "date_range": observed["date_range"],
        "currency": "USD",
        "cash_check": cash,
        "reported_fifo_pnl": format(fifo_net, "f"),
        "fifo_check": "difference_arithmetic_only",
    }
    if verify_fifo:
        result.update(_fifo_audit(observed["fills"], profile, fifo_net))
    return result


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Normalized UTF-8 trade CSV")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path, help="New local JSON report path")
    mode.add_argument("--verify-report", type=Path, help="Check an existing report without dependencies")
    parser.add_argument("--verify-fifo", action="store_true", help="Also independently match trades and verify FIFO")
    args = parser.parse_args(argv)
    if args.verify_fifo and args.verify_report is None:
        parser.error("--verify-fifo requires --verify-report")
    try:
        source = args.source.expanduser()
        if source.suffix.lower() != ".csv" or not source.is_file():
            raise ValueError("source must be an existing CSV file")
        if args.verify_report is not None:
            result = verify_report(
                source.read_bytes(),
                args.verify_report.expanduser().read_bytes(),
                verify_fifo=args.verify_fifo,
            )
            print(json.dumps(result, indent=2))
            return 0
        if args.output.suffix.lower() != ".json":
            raise ValueError("output must be a new JSON file")
        from pytest_socket import disable_socket

        disable_socket()
        _write_new_report(args.output.expanduser(), build_report(source.read_bytes()))
    except (OSError, ValueError, OverflowError, csv.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
