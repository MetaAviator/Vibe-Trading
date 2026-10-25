"""Malformed prices must not become zero-cost fills in public journal analysis."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from src.tools.trade_journal_parsers import parse_file
from src.tools.trade_journal_tool import analyze_trade_journal


def _row(side="buy", price=100, day=1):
    return dict(
        datetime=f"2026-01-{day:02d}",
        symbol="AAPL",
        side=side,
        quantity=1,
        price=price,
        fee=1,
    )


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


def _assert_error(result, tmp_path):
    assert set(result) == {"status", "error"}
    assert result["status"] == "error"
    assert "price" in result["error"].lower()
    assert "correct" in result["error"].lower()
    assert "private-bad-price" not in result["error"]
    assert str(tmp_path) not in result["error"]


@pytest.mark.parametrize("bad", ["", "private-bad-price", "NaN", "Infinity"])
@pytest.mark.parametrize("side", ["buy", "sell"])
def test_ac2_bad_price_is_not_a_zero_cost_fill(tmp_path, monkeypatch, bad, side):
    rows = [_row(), _row("sell", 110, 2)]
    rows[0 if side == "buy" else 1]["price"] = bad
    _assert_error(_analyze(tmp_path, monkeypatch, rows), tmp_path)


def test_ac2_missing_price_column(tmp_path, monkeypatch):
    row = _row()
    row.pop("price")
    _assert_error(_analyze(tmp_path, monkeypatch, [row]), tmp_path)


@pytest.mark.parametrize("mode", ["full", "profile", "behavior", "strategy"])
def test_ac2_all_modes_validate_prices(tmp_path, monkeypatch, mode):
    _assert_error(_analyze(tmp_path, monkeypatch, [_row(price="bad")], mode), tmp_path)


@pytest.mark.parametrize(
    "expr", ["2026-01 to 2026-01", "symbol=AAPL", "market=us", "symbol=NONE"]
)
def test_ac2_filters_cannot_hide_bad_prices(tmp_path, monkeypatch, expr):
    bad = _row(price="bad")
    bad.update(datetime="2026-02-01", symbol="00700.HK")
    _assert_error(_analyze(tmp_path, monkeypatch, [_row(), bad], expr=expr), tmp_path)


def test_ac3_excel_price_validation(tmp_path, monkeypatch):
    _assert_error(
        _analyze(tmp_path, monkeypatch, [_row(price="bad")], excel=True), tmp_path
    )


@pytest.mark.parametrize("fmt", ["tonghuashun", "eastmoney", "futu"])
def test_ac3_broker_prices(tmp_path, monkeypatch, fmt):
    if fmt == "tonghuashun":
        row = {
            "成交时间": "2026-01-01",
            "证券代码": "600519",
            "操作": "买入",
            "成交数量": 1,
            "成交价格": "bad",
        }
    elif fmt == "eastmoney":
        row = {
            "成交日期": "20260101",
            "成交时间": "10:00:00",
            "股票代码": "600519",
            "买卖标志": "B",
            "成交数量": 1,
            "成交均价": "bad",
        }
    else:
        row = dict(
            Date="2026-01-01", Symbol="AAPL", Side="buy", Quantity=1, Price="bad"
        )
    path = _write(tmp_path, monkeypatch, [row])
    # Existing consumers keep the original zero fallback unless they opt in.
    assert parse_file(path)[1][0].price == 0
    _assert_error(json.loads(analyze_trade_journal(str(path))), tmp_path)


@pytest.mark.parametrize(
    "price,expected",
    [(100, 8), (0, 108), (-5, 113), ("$100.00", 8), ("1.000,00", -892)],
)
def test_ac3_finite_price_semantics_are_preserved(
    tmp_path, monkeypatch, price, expected
):
    result = _analyze(tmp_path, monkeypatch, [_row(price=price), _row("sell", 110, 2)])
    assert result["status"] == "ok"
    assert result["profile"]["total_pnl"] == expected


@pytest.mark.parametrize("bad", ["", "bad", "NaN", "Infinity"])
def test_ac3_default_generic_parser_retains_legacy_fallback(tmp_path, monkeypatch, bad):
    path = _write(tmp_path, monkeypatch, [_row(price=bad)])
    assert parse_file(path)[1][0].price == 0


@pytest.mark.parametrize("only_cash", [False, True])
def test_ac4_dividend_need_not_have_a_price(tmp_path, monkeypatch, only_cash):
    cash = _row("dividend", "", 3)
    cash.update(quantity=0, amount=5, fee=0)
    rows = [] if only_cash else [_row(), _row("sell", 110, 2)]
    result = _analyze(tmp_path, monkeypatch, rows + [cash])
    assert result["status"] == "ok"
    assert result["profile"]["total_pnl"] == (5 if only_cash else 13)


def test_ac4_skipped_nontrade_price_is_ignored(tmp_path, monkeypatch):
    rows = [_row(), _row("sell", 110, 2), _row("interest", "bad", 3)]
    result = _analyze(tmp_path, monkeypatch, rows)
    assert result["status"] == "ok"
    assert result["total_records"] == 2
    assert result["profile"]["total_pnl"] == 8
