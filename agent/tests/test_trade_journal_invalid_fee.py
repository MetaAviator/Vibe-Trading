"""Unreadable recorded fees must not become zero-cost public journal fills."""

from __future__ import annotations

from decimal import Decimal
import json

import pandas as pd
import pytest

from src.tools.trade_journal_parsers import parse_file, parse_generic
from src.tools.trade_journal_tool import analyze_trade_journal


def _rows():
    return [
        dict(
            datetime="2026-01-01",
            symbol="AAPL",
            side="buy",
            quantity=1,
            price=100,
            fee="0.75",
        ),
        dict(
            datetime="2026-01-02",
            symbol="AAPL",
            side="sell",
            quantity=1,
            price=101,
            fee="0.75",
        ),
    ]


def _write(tmp_path, monkeypatch, rows, excel=False):
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_FILE_ROOTS", str(tmp_path))
    path = tmp_path / ("journal.xlsx" if excel else "journal.csv")
    frame = pd.DataFrame(rows)
    if excel:
        frame.to_excel(path, index=False)
    else:
        frame.to_csv(path, index=False)
    return path


def _analyze(tmp_path, monkeypatch, rows, mode="full", expr="", excel=False):
    path = _write(tmp_path, monkeypatch, rows, excel)
    return json.loads(analyze_trade_journal(str(path), mode, expr))


def _assert_fee_error(result, tmp_path):
    assert set(result) == {"status", "error"}
    assert result["status"] == "error"
    assert "fee" in result["error"].lower()
    assert "correct" in result["error"].lower()
    assert "private-bad-fee" not in result["error"]
    assert str(tmp_path) not in result["error"]


@pytest.mark.parametrize("bad", ["private-bad-fee", "Infinity", "-Infinity"])
@pytest.mark.parametrize("index", [0, 1])
def test_ac2_bad_fee_cannot_turn_a_net_loser_into_a_winner(
    tmp_path, monkeypatch, bad, index
):
    rows = _rows()
    rows[index]["fee"] = bad
    _assert_fee_error(_analyze(tmp_path, monkeypatch, rows), tmp_path)


@pytest.mark.parametrize("mode", ["full", "profile", "behavior", "strategy"])
def test_ac2_all_modes_validate_recorded_fees(tmp_path, monkeypatch, mode):
    rows = _rows()
    rows[0]["fee"] = "private-bad-fee"
    _assert_fee_error(_analyze(tmp_path, monkeypatch, rows, mode=mode), tmp_path)


@pytest.mark.parametrize(
    "expr", ["2026-01 to 2026-01", "symbol=AAPL", "market=us", "symbol=NONE"]
)
def test_ac2_filters_cannot_hide_bad_fees(tmp_path, monkeypatch, expr):
    rows = _rows()
    rows[1].update(datetime="2026-02-01", symbol="00700.HK", fee="private-bad-fee")
    _assert_fee_error(_analyze(tmp_path, monkeypatch, rows, expr=expr), tmp_path)


def test_ac3_excel_rejects_bad_fees(tmp_path, monkeypatch):
    rows = _rows()
    rows[0]["fee"] = "private-bad-fee"
    _assert_fee_error(_analyze(tmp_path, monkeypatch, rows, excel=True), tmp_path)


_BROKER_FEES = (
    [("tonghuashun", field) for field in ("手续费", "印花税", "过户费")]
    + [("eastmoney", field) for field in ("佣金", "印花税")]
    + [("futu", field) for field in ("Commission", "Platform Fee")]
)


def _broker_rows(fmt):
    if fmt == "tonghuashun":
        return [
            dict(
                成交时间=f"2026-01-0{i}",
                证券代码="600519",
                操作=side,
                成交数量=1,
                成交价格=price,
                手续费="0.75",
                印花税="0.25",
                过户费="0.10",
            )
            for i, side, price in [(1, "买入", 100), (2, "卖出", 101)]
        ]
    if fmt == "eastmoney":
        return [
            dict(
                成交日期=f"2026010{i}",
                成交时间="10:00:00",
                股票代码="600519",
                买卖标志=side,
                成交数量=1,
                成交均价=price,
                佣金="0.75",
                印花税="0.25",
            )
            for i, side, price in [(1, "B", 100), (2, "S", 101)]
        ]
    return [
        dict(
            Date=f"2026-01-0{i}",
            Symbol="AAPL",
            Side=side,
            Quantity=1,
            Price=price,
            Commission="0.75",
            **{"Platform Fee": "0.25"},
        )
        for i, side, price in [(1, "buy", 100), (2, "sell", 101)]
    ]


@pytest.mark.parametrize("fmt,field", _BROKER_FEES)
@pytest.mark.parametrize("index", [0, 1])
def test_ac3_each_broker_component_is_checked_before_summing(
    tmp_path, monkeypatch, fmt, field, index
):
    rows = _broker_rows(fmt)
    rows[index][field] = "private-bad-fee"
    path = _write(tmp_path, monkeypatch, rows)
    # Legacy consumers still parse, even when other components make the sum finite.
    assert parse_file(path)[0] == fmt
    _assert_fee_error(json.loads(analyze_trade_journal(str(path))), tmp_path)


@pytest.mark.parametrize("alias", ["fee", "commission", "fees"])
def test_ac3_generic_fee_aliases(tmp_path, monkeypatch, alias):
    rows = _rows()
    for row in rows:
        row[alias] = row.pop("fee")
    rows[0][alias] = "private-bad-fee"
    _assert_fee_error(_analyze(tmp_path, monkeypatch, rows), tmp_path)


@pytest.mark.parametrize(
    "raw,parsed",
    [
        ("0.75", "0.75"),
        ("", "0"),
        (" ", "0"),
        ("NaN", "0"),
        ("N/A", "0"),
        (None, "0"),
        ("0", "0"),
        ("-0.25", "-0.25"),
        ("(0.25)", "-0.25"),
        ("$0.75", "0.75"),
        ("1.000,75", "1000.75"),
    ],
)
def test_ac3_legacy_missing_values_and_finite_fee_semantics(
    tmp_path, monkeypatch, raw, parsed
):
    rows = _rows()
    rows[0]["fee"] = raw
    result = _analyze(tmp_path, monkeypatch, rows)
    assert result["status"] == "ok"
    expected = Decimal("101") - Decimal("100") - Decimal(parsed) - Decimal("0.75")
    assert result["profile"]["total_pnl"] == float(expected)
    assert result["profile"]["win_rate"] == float(expected > 0)


def test_ac3_missing_fee_columns_retain_legacy_zero(tmp_path, monkeypatch):
    rows = _rows()
    for row in rows:
        row.pop("fee")
    assert _analyze(tmp_path, monkeypatch, rows)["profile"]["total_pnl"] == 1


def test_ac3_generic_alias_precedence_is_unchanged(tmp_path, monkeypatch):
    rows = _rows()
    for row in rows:
        row["commission"] = "private-bad-fee"
    # The selected fee column remains "fee"; unused aliases are not added or validated.
    assert _analyze(tmp_path, monkeypatch, rows)["profile"]["total_pnl"] == -0.5


@pytest.mark.parametrize("bad", ["private-bad-fee", "Infinity", "-Infinity"])
def test_ac4_default_parser_retains_legacy_fallback(tmp_path, monkeypatch, bad):
    rows = _rows()
    rows[0]["fee"] = bad
    path = _write(tmp_path, monkeypatch, rows)
    assert parse_file(path)[1][0].fee == 0
    assert parse_generic(pd.DataFrame(rows))[0].fee == 0


@pytest.mark.parametrize("raw", ["private-bad-fee", "NaN", float("inf"), float("-inf")])
def test_ac4_opt_in_adapter_rejects_nonmissing_invalid_fee(raw):
    rows = _rows()
    rows[0]["fee"] = raw
    with pytest.raises(ValueError, match="fees"):
        parse_generic(pd.DataFrame(rows), require_readable_fees=True)


@pytest.mark.parametrize("raw", [None, pd.NA, float("nan"), " "])
def test_ac4_opt_in_adapter_preserves_missing_values(raw):
    rows = _rows()
    rows[0]["fee"] = raw
    records = parse_generic(pd.DataFrame(rows), require_readable_fees=True)
    assert records[0].fee == 0


@pytest.mark.parametrize("only_cash", [False, True])
def test_ac4_dividend_fee_handling_is_unchanged(tmp_path, monkeypatch, only_cash):
    cash = dict(
        datetime="2026-01-03",
        symbol="AAPL",
        side="dividend",
        quantity=0,
        price=0,
        amount=5,
        fee="private-bad-fee",
    )
    rows = [] if only_cash else _rows()
    result = _analyze(tmp_path, monkeypatch, rows + [cash])
    assert result["status"] == "ok"
    assert result["profile"]["total_pnl"] == (5 if only_cash else 4.5)


def test_ac4_skipped_rows_do_not_require_readable_fees(tmp_path, monkeypatch):
    rows = _rows() + [
        dict(
            datetime="2026-01-03", symbol="AAPL", side="interest", fee="private-bad-fee"
        ),
        dict(datetime="2026-01-03", symbol="", side="buy", fee="private-bad-fee"),
    ]
    result = _analyze(tmp_path, monkeypatch, rows)
    assert result["status"] == "ok"
    assert result["total_records"] == 2
    assert result["profile"]["total_pnl"] == -0.5
