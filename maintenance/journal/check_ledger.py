"""Offline journal validation. All fixtures are synthetic; no broker access.

Expected pair links are manually specified. Decimal cash/fee calculations do
not call the application's pairing, parsing or profile calculations.
Run: python maintenance/journal/check_ledger.py --output /path/to/synthetic-output
"""

from __future__ import annotations

import argparse
import csv
import sys
import hashlib
import json
import os
import platform
import subprocess
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

import pandas as pd
from pytest_socket import disable_socket

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "agent"))

# The checkout's agent/ package must be discoverable before importing it.
from src.tools.trade_journal_parsers import (  # noqa: E402
    parse_file,
    records_to_dataframe,
)
from src.tools.trade_journal_tool import (  # noqa: E402
    analyze_trade_journal,
    pair_trades_fifo,
)

ROOT: Path
FILES: Path
OUTPUTS: Path


def D(value):
    return Decimal(str(value))


def Q(value):
    return float(D(value).quantize(D(".01"), rounding=ROUND_HALF_EVEN))


def legs(spec, symbol="AAPL", currency="USD", same_time=False):
    out = []
    for i, (side, qty, price, fee) in enumerate(spec):
        dt = datetime(2026, 1, 1, 10) + timedelta(days=0 if same_time else i)
        out.append(
            dict(
                id=f"F{i+1:02}",
                datetime=dt.isoformat(sep=" "),
                symbol=symbol,
                currency=currency,
                side=side,
                quantity=str(qty),
                price=str(price),
                amount=str(D(qty) * D(price)),
                fee=str(fee),
            )
        )
    return out


def write_fixture(name, records, fmt="generic", excel=False, split=False):
    data = []
    for r in records:
        date, time = r["datetime"].split(" ")
        common = dict(
            datetime=r["datetime"],
            symbol=r["symbol"],
            name=r["id"],
            side=r["side"],
            quantity=r["quantity"],
            price=r["price"],
            amount=r["amount"],
            fee=r["fee"],
        )
        if fmt == "generic":
            row = common
            if split:
                row.pop("datetime")
                row = dict(date=date, time=time, **row)
        elif fmt == "futu":
            row = dict(
                Date=date,
                Time=time,
                Symbol=r["symbol"],
                Name=r["id"],
                Side=r["side"],
                Quantity=r["quantity"],
                Price=r["price"],
                Amount=r["amount"],
                Commission=r["fee"],
                **{"Platform Fee": "0"},
            )
        elif fmt == "tonghuashun":
            row = dict(
                成交时间=r["datetime"],
                证券代码="600519",
                证券名称=r["id"],
                操作="买入" if r["side"] == "buy" else "卖出",
                成交数量=r["quantity"],
                成交价格=r["price"],
                成交金额=r["amount"],
                手续费=r["fee"],
                印花税="0",
                过户费="0",
            )
        else:
            row = dict(
                成交日期=date.replace("-", ""),
                成交时间=time,
                股票代码="600519",
                股票名称=r["id"],
                买卖标志="B" if r["side"] == "buy" else "S",
                成交数量=r["quantity"],
                成交均价=r["price"],
                成交金额=r["amount"],
                佣金=r["fee"],
                印花税="0",
            )
        data.append(row)
    path = FILES / (name + (".xlsx" if excel else ".csv"))
    if excel:
        pd.DataFrame(data).to_excel(path, index=False)
    else:
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(data[0]))
            writer.writeheader()
            writer.writerows(data)
    return path


def expected_ledger(records, links):
    """Calculate explicit hand-linked pairs; never discover matches here."""
    consumed = defaultdict(Decimal)
    fees = defaultdict(Decimal)
    ledger = []
    cash = defaultdict(Decimal)
    positions = defaultdict(Decimal)
    for r in records:
        qty, price, fee = map(D, (r["quantity"], r["price"], r["fee"]))
        assert D(r["amount"]) == qty * price
        sign = D(1) if r["side"] == "sell" else D(-1)
        cash[r["currency"]] += sign * qty * price - fee
        positions[r["symbol"]] -= sign * qty
    assert all(
        v == 0 for v in positions.values()
    ), "Closed-book oracle requires flat positions"
    for opening, closing, qty in links:
        a, b = records[opening], records[closing]
        qty = D(qty)
        assert a["symbol"] == b["symbol"] and a["currency"] == b["currency"]
        assert a["side"] != b["side"]
        entry_fee = D(a["fee"]) * qty / D(a["quantity"])
        exit_fee = D(b["fee"]) * qty / D(b["quantity"])
        gross = (
            (D(b["price"]) - D(a["price"])) * qty * (1 if a["side"] == "buy" else -1)
        )
        for index, allocated in ((opening, entry_fee), (closing, exit_fee)):
            consumed[index] += qty
            fees[index] += allocated
        hold = (
            datetime.fromisoformat(b["datetime"])
            - datetime.fromisoformat(a["datetime"])
        ).total_seconds() / 86400
        ledger.append(
            dict(
                entry=a["id"],
                exit=b["id"],
                symbol=a["symbol"],
                currency=a["currency"],
                qty=float(qty),
                gross=Q(gross),
                entry_fee=Q(entry_fee),
                exit_fee=Q(exit_fee),
                pnl=Q(gross - entry_fee - exit_fee),
                side="long" if a["side"] == "buy" else "short",
                hold_days=round(hold, 2),
                buy_dt=a["datetime"],
                sell_dt=b["datetime"],
            )
        )
    for i, r in enumerate(records):
        assert consumed[i] == D(r["quantity"]), f'Uncovered quantity in {r["id"]}'
        assert fees[i] == D(r["fee"]), f'Fee not allocated once: {r["id"]}'
    grouped = defaultdict(list)
    for pair in ledger:
        grouped[pair["currency"]].append(D(pair["pnl"]))
    totals, ratios = {}, {}
    for currency, pnls in grouped.items():
        totals[currency] = Q(sum(pnls))
        assert D(totals[currency]) == cash[currency]
        wins, losses = [v for v in pnls if v > 0], [v for v in pnls if v < 0]
        ratios[currency] = (
            Q(
                (sum(wins) / len(wins) if wins else D(0))
                / abs(sum(losses) / len(losses))
            )
            if losses
            else None if wins else 0.0
        )
    return ledger, totals, ratios


def strict_json(raw):
    def reject(value):
        raise ValueError("Non-standard JSON constant: " + value)

    return json.loads(raw, parse_constant=reject)


def check(name, records, links, fmt="generic", excel=False, split=False):
    path = write_fixture(name, records, fmt, excel, split)
    ledger, totals, ratios = expected_ledger(records, links)
    detected, parsed = parse_file(path)
    assert detected == fmt
    assert len(parsed) == len(records)
    for source, record in zip(records, parsed):
        assert record.name == source["id"]
        assert record.symbol == source["symbol"]
        assert record.side == source["side"]
        assert record.datetime == source["datetime"]
        for key in ("quantity", "price", "amount", "fee"):
            assert D(getattr(record, key)) == D(source[key]), (name, source["id"], key)
    frame = records_to_dataframe(parsed)
    actual = pair_trades_fifo(frame)
    assert len(actual) == len(ledger)
    for expected, result in zip(ledger, actual):
        for key in ("symbol", "currency", "qty", "pnl", "side", "hold_days"):
            assert result[key] == expected[key], (name, key, result, expected)
        for key in ("buy_dt", "sell_dt"):
            assert pd.Timestamp(result[key]) == pd.Timestamp(expected[key])
    raw = analyze_trade_journal(str(path), "full")
    (OUTPUTS / (name + ".json")).write_text(raw, encoding="utf-8")
    result = strict_json(raw)
    assert result["status"] == "ok"
    assert result["total_records"] == len(records)
    dates = [r["datetime"][:10] for r in records]
    assert result["date_range"] == min(dates) + " ~ " + max(dates)
    assert result["symbols_count"] == len({r["symbol"] for r in records})
    p = result["profile"]
    assert p["total_trades"] == len(records)
    for pair, sample in zip(ledger[:5], p["roundtrips_sample"]):
        for key in ("symbol", "currency", "qty", "pnl", "side", "hold_days"):
            assert sample[key] == pair[key]
        for key in ("buy_dt", "sell_dt"):
            assert pd.Timestamp(sample[key]) == pd.Timestamp(pair[key])
    assert len(p["roundtrips_sample"]) == min(5, len(ledger))
    expected_gross = defaultdict(Decimal)
    for r in records:
        expected_gross[r["symbol"]] += D(r["amount"])
    assert {v["symbol"]: D(v["total_amount"]) for v in p["top_symbols"]} == dict(
        expected_gross
    )

    def scalar(data):
        return next(iter(data.values())) if len(data) == 1 else data

    assert p["total_pnl"] == scalar(totals)
    assert p["profit_loss_ratio"] == scalar(ratios)
    assert p["win_rate"] == round(sum(v["pnl"] > 0 for v in ledger) / len(ledger), 4)
    assert p["total_roundtrips"] == len(ledger)
    assert p["avg_holding_days"] == round(
        sum(v["hold_days"] for v in ledger) / len(ledger), 2
    )
    assert p["total_dividends"] == scalar({cur: 0.0 for cur in totals})
    # Independently derive monetary drawdown from the hand-linked closes.
    drawdowns = {}
    for currency in totals:
        cumulative = peak = worst = D(0)
        for pair in sorted(
            [v for v in ledger if v["currency"] == currency], key=lambda v: v["sell_dt"]
        ):
            cumulative += D(pair["pnl"])
            peak = max(peak, cumulative)
            worst = min(worst, cumulative - peak)
        drawdowns[currency] = Q(worst)
    assert p["max_drawdown"] == scalar(drawdowns)
    assert "behavior" in result
    assert result["strategy_features"]["status"] == "pending"
    (OUTPUTS / (name + "_ledger.json")).write_text(
        json.dumps(ledger, indent=2), encoding="utf-8"
    )
    return dict(
        name=name,
        status="PASS",
        format=detected,
        rows=len(records),
        pairs=len(ledger),
        fee_by_currency={
            cur: Q(sum(D(r["fee"]) for r in records if r["currency"] == cur))
            for cur in totals
        },
        pnl=p["total_pnl"],
        win_rate=p["win_rate"],
        ratio=p["profit_loss_ratio"],
        file=str(path.relative_to(ROOT)),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def guard(name, text):
    path = FILES / (name + ".csv")
    path.write_text(text, encoding="utf-8")
    try:
        result = strict_json(analyze_trade_journal(str(path), "full"))
    except Exception as exc:
        result = {"uncaught_exception": type(exc).__name__, "message": str(exc)}
    (OUTPUTS / (name + ".json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    profile = result.get("profile", {})
    return dict(
        name=name,
        status=result.get("status", "exception"),
        pnl=profile.get("total_pnl"),
        win_rate=profile.get("win_rate"),
        pairs=profile.get("total_roundtrips"),
        hold_days=profile.get("avg_holding_days"),
        date_range=result.get("date_range"),
        error=result.get("error") or result.get("message"),
        file=str(path.relative_to(ROOT)),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def main():
    scenarios = []
    basic = [
        ("buy", 2, 100, 1),
        ("sell", 2, 105, 1),
        ("buy", 1, 100, 1),
        ("sell", 1, 95, 1),
        ("buy", 1, 100, 1),
        ("sell", 1, 102, 1),
    ]
    for fmt, excel in [
        ("generic", False),
        ("generic", True),
        ("futu", False),
        ("tonghuashun", False),
        ("eastmoney", False),
    ]:
        cn = fmt in ("tonghuashun", "eastmoney")
        records = legs(basic, "600519.SH" if cn else "AAPL", "CNY" if cn else "USD")
        scenarios.append(
            (
                f"{fmt}_" + ("xlsx" if excel else "csv"),
                records,
                [(0, 1, 2), (2, 3, 1), (4, 5, 1)],
                fmt,
                excel,
                False,
            )
        )
    scenarios += [
        (
            "partial_multilot",
            legs(
                [
                    ("buy", 4, 100, 0.8),
                    ("buy", 6, 110, 1.2),
                    ("sell", 7, 120, 1.4),
                    ("sell", 3, 105, 0.6),
                ]
            ),
            [(0, 2, 4), (1, 2, 3), (1, 3, 3)],
            "generic",
            False,
            False,
        ),
        (
            "long_reversal",
            legs([("buy", 10, 100, 2), ("sell", 15, 110, 3), ("buy", 5, 105, 1)]),
            [(0, 1, 10), (1, 2, 5)],
            "generic",
            False,
            False,
        ),
        (
            "short_reversal",
            legs([("sell", 10, 100, 2), ("buy", 15, 90, 3), ("sell", 5, 95, 1)]),
            [(0, 1, 10), (1, 2, 5)],
            "generic",
            False,
            False,
        ),
        (
            "rebates_zero",
            legs(
                [
                    ("buy", 1, 100, -1),
                    ("sell", 1, 99, 0),
                    ("buy", 1, 100, 0),
                    ("sell", 1, 99, -2),
                ]
            ),
            [(0, 1, 1), (2, 3, 1)],
            "generic",
            False,
            False,
        ),
    ]
    cross = legs(
        [
            ("buy", 1, 100, 1),
            ("sell", 1, 105, 1),
            ("buy", 1, 110, 1),
            ("sell", 1, 120, 1),
            ("buy", 1, 50, 1),
            ("sell", 1, 48, 1),
        ]
    )
    for r, clock in zip(
        cross, ["15:00:00", "09:00:00", "10:00:00", "11:00:00", "12:00:00", "13:00:00"]
    ):
        r["datetime"] = r["datetime"][:10] + " " + clock
    for r in cross[-2:]:
        r["symbol"] = "MSFT"
    scenarios.append(
        (
            "crossday_split",
            cross,
            [(0, 1, 1), (2, 3, 1), (4, 5, 1)],
            "generic",
            False,
            True,
        )
    )
    tied = legs(
        [
            ("buy", 1, 100, 1),
            ("sell", 1, 105, 1),
            ("buy", 1, 110, 1),
            ("sell", 1, 120, 1),
        ]
        * 5,
        same_time=True,
    )
    scenarios.append(
        (
            "timestamp_ties",
            tied,
            [(i, i + 1, 1) for i in range(0, 20, 2)],
            "generic",
            False,
            False,
        )
    )
    mixed = legs([("buy", 1, 100, 1), ("sell", 1, 110, 1)]) + legs(
        [("buy", 10, 100, 1), ("sell", 10, 99, 1)], "00700.HK", "HKD"
    )
    for i, r in enumerate(mixed):
        r["id"] = f"F{i+1:02}"
    scenarios.append(
        ("currency_pools", mixed, [(0, 1, 1), (2, 3, 10)], "generic", False, False)
    )
    checks = []
    for args in scenarios:
        try:
            checks.append(check(*args))
        except Exception as exc:
            checks.append(
                dict(
                    name=args[0],
                    status="FAIL",
                    error=repr(exc),
                    traceback=traceback.format_exc(),
                )
            )
    header = "datetime,symbol,side,quantity,price,fee\n"
    guards = [
        guard(
            "invalid_date",
            header
            + "not-a-date,AAPL,buy,1,100,1\n2026-01-02 10:00:00,AAPL,sell,1,110,1\n",
        ),
        guard(
            "all_invalid_dates",
            header + "bad,AAPL,buy,1,100,1\nbad,AAPL,sell,1,110,1\n",
        ),
        guard(
            "missing_fee",
            header
            + "2026-01-01 10:00:00,AAPL,buy,1,100,\n2026-01-02 10:00:00,AAPL,sell,1,110,1\n",
        ),
        guard(
            "duplicate_pair",
            header
            + (
                "2026-01-01 10:00:00,AAPL,buy,1,100,1\n2026-01-02 10:00:00,AAPL,sell,1,110,1\n"
            )
            * 2,
        ),
        guard("invalid_quantity", header + "2026-01-01 10:00:00,AAPL,buy,0,100,1\n"),
        guard("invalid_price", header + "2026-01-01 10:00:00,AAPL,buy,1,broken,1\n"),
        guard("invalid_fee", header + "2026-01-01 10:00:00,AAPL,buy,1,100,broken\n"),
        guard("unknown_side", header + "2026-01-01 10:00:00,AAPL,hold,1,100,1\n"),
        guard("open_position", header + "2026-01-01 10:00:00,AAPL,buy,1,100,1\n"),
    ]
    required = {
        "invalid_date",
        "all_invalid_dates",
        "invalid_quantity",
        "invalid_price",
        "invalid_fee",
        "unknown_side",
    }
    guards_pass = all(v["status"] == "error" for v in guards if v["name"] in required)
    manifest = dict(
        synthetic=True,
        real_data_validation_performed=False,
        scope="file -> parser -> normalized rows -> manual linked Decimal ledger -> FIFO -> full JSON",
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        head=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        source_sha256={
            str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (
                REPO / "agent/src/tools/trade_journal_parsers.py",
                REPO / "agent/src/tools/trade_journal_numeric.py",
                REPO / "agent/src/tools/trade_journal_tool.py",
            )
        },
        python=platform.python_version(),
        pandas=pd.__version__,
        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        acceptance={
            "valid_closed_book_pipeline": (
                "PASS" if all(v["status"] == "PASS" for v in checks) else "FAIL"
            ),
            "invalid_datetime_handling": (
                "PASS"
                if all(
                    v["status"] == "error"
                    for v in guards
                    if v["name"] in ("invalid_date", "all_invalid_dates")
                )
                else "FAIL: invalid dates accepted"
            ),
            "unsupported_side_handling": (
                "PASS"
                if next(v for v in guards if v["name"] == "unknown_side")["status"]
                == "error"
                else "FAIL"
            ),
            "input_guard_suite": "PASS" if guards_pass else "FAIL",
            "missing_fee_semantics": "UNRESOLVED: blank is treated as zero; source contract required",
            "duplicate_identity": "UNVERIFIED: no execution IDs; identical rows may be distinct real fills",
            "open_position_scope": "LIMIT: unmatched positions excluded; realized PnL is not account PnL",
            "real_data_validation": "NOT_PERFORMED: this runner generates synthetic inputs only",
            "overall": (
                "NEEDS_REVISION: an input guard failed"
                if not guards_pass
                else "CONDITIONAL: real data and source contracts pending"
            ),
        },
        core=checks,
        guards=guards,
        passed=sum(v["status"] == "PASS" for v in checks),
        total=len(checks),
    )
    (ROOT / "results.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for v in checks:
        print(
            v["status"],
            v["name"],
            v.get("pnl"),
            v.get("win_rate"),
            v.get("ratio"),
            v.get("error", ""),
        )
    print("GUARDS", json.dumps(guards, ensure_ascii=False))
    print("CORE", manifest["passed"], "/", manifest["total"])
    assert (
        manifest["passed"] == manifest["total"]
    ), "Core scenario failed; inspect results.json"
    assert guards_pass, "Input guard failed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output directory for generated synthetic fixtures and results",
    )
    args = parser.parse_args()
    disable_socket()
    ROOT = args.output.resolve()
    FILES, OUTPUTS = ROOT / "fixtures", ROOT / "outputs"
    for directory in (FILES, OUTPUTS):
        directory.mkdir(parents=True, exist_ok=True)
    previous = os.environ.get("VIBE_TRADING_ALLOWED_FILE_ROOTS")
    os.environ["VIBE_TRADING_ALLOWED_FILE_ROOTS"] = str(FILES)
    try:
        main()
    finally:
        if previous is None:
            os.environ.pop("VIBE_TRADING_ALLOWED_FILE_ROOTS", None)
        else:
            os.environ["VIBE_TRADING_ALLOWED_FILE_ROOTS"] = previous
