"""Independent FIFO checks use only synthetic normalized trade rows."""

from fractions import Fraction
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from maintenance.journal.review_trades import build_report, verify_report

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "maintenance/journal/examples/synthetic_review_trades.csv"
HEADER = b"execution_id,datetime,symbol,side,quantity,price,amount,fee,currency\n"


def test_fifo_recomputes_split_fees_and_rebate_from_only_csv(tmp_path):
    source = SOURCE.read_bytes()
    report = json.dumps(build_report(source)).encode()
    script = ROOT / "maintenance/journal/review_trades.py"
    (tmp_path / "verify.py").write_bytes(script.read_bytes())
    (tmp_path / "trades.csv").write_bytes(source)
    (tmp_path / "report.json").write_bytes(report)
    run = subprocess.run(
        [sys.executable, "-S", "verify.py", "trades.csv", "--verify-report", "report.json", "--verify-fifo"],
        cwd=tmp_path,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        check=True,
        capture_output=True,
        text=True,
    )
    proof = json.loads(run.stdout)
    assert proof["fifo_check"] == "independent_total_and_reported_sample"
    assert proof["recomputed_fifo_pnl"] == "1.61"
    assert proof["fifo_samples_checked"] == proof["fifo_match_count"] == 3
    assert [
        (pair["buy_execution_id"], pair["sell_execution_id"], pair["net_pnl_usd"]) for pair in proof["fifo_matches"]
    ] == [("S001", "S003", "1.78"), ("S001", "S004", "2.78"), ("S002", "S005", "-2.95")]
    assert [Fraction(pair["buy_fee_usd_exact"]) for pair in proof["fifo_matches"]] == [
        Fraction("0.12"),
        Fraction("0.12"),
        Fraction(0),
    ]
    assert Fraction(proof["fifo_matches"][-1]["sell_fee_usd_exact"]) == Fraction("-0.05")


def test_fifo_recomputes_later_matches_outside_the_report_sample():
    source = (
        HEADER
        + (
            "B,2026-01-01 00:00:00,AAA,buy,3,4,12,1,USD\n"
            + "".join(f"S{i},2026-01-02 00:00:00,AAA,sell,0.5,6,3,0,USD\n" for i in range(1, 7))
        ).encode()
    )
    report = json.dumps(build_report(source)).encode()
    result = verify_report(source, report, verify_fifo=True)
    assert result["fifo_match_count"] == 6
    assert result["fifo_samples_checked"] == 5
    assert result["recomputed_fifo_pnl"] == "4.98"
    assert result["cash_check"]["net_trade_cash"] == "5"
    assert result["fifo_matches"][-1]["sell_execution_id"] == "S6"
    assert Fraction(result["fifo_matches"][-1]["buy_fee_usd_exact"]) == Fraction(1, 6)


@pytest.mark.parametrize("change", ["swap_samples", "total_and_difference", "count", "price", "quantity"])
def test_fifo_rejects_report_edits_that_leave_cash_arithmetic_consistent(change):
    source = SOURCE.read_bytes()
    report = build_report(source)
    profile = report["profile"]
    samples = profile["roundtrips_sample"]
    if change == "swap_samples":
        samples[0], samples[1] = samples[1], samples[0]
    elif change == "total_and_difference":
        profile["total_pnl"] = 1.62
        report["cash_check"]["fifo_minus_cash"] = "0.01"
    elif change == "count":
        profile["total_roundtrips"] += 1
    elif change == "price":
        samples[0]["buy_price"] = 19.01
    else:
        samples[0]["qty"] = 0.5
    payload = json.dumps(report).encode()
    assert verify_report(source, payload)["status"] == "ok"
    with pytest.raises(ValueError, match="FIFO .*differ"):
        verify_report(source, payload, verify_fifo=True)


def test_one_sale_spans_multiple_buy_lots_in_source_order():
    source = HEADER + (
        b"B1,2026-01-01 10:00:00,AAA,buy,2,10,20,0.20,USD\n"
        b"B2,2026-01-01 10:00:00,AAA,buy,1,12,12,0.08,USD\n"
        b"S1,2026-01-01 11:00:00,AAA,sell,2.5,15,37.5,0.25,USD\n"
        b"S2,2026-01-01 11:00:00,AAA,sell,0.5,16,8,0.04,USD\n"
    )
    review = verify_report(source, json.dumps(build_report(source)).encode(), verify_fifo=True)
    assert review["recomputed_fifo_pnl"] == "12.93"
    assert [
        (item["buy_execution_id"], item["sell_execution_id"], item["sell_fee_usd_exact"])
        for item in review["fifo_matches"]
    ] == [("B1", "S1", "1/5"), ("B2", "S1", "1/20"), ("B2", "S2", "1/25")]


def test_float_rounding_difference_is_reported_as_a_mismatch():
    source = HEADER + (
        b"B,2026-01-01 00:00:00,AAA,buy,1,1,1,0,USD\nS,2026-01-02 00:00:00,AAA,sell,1,3.675,3.675,0,USD\n"
    )
    report = build_report(source)
    assert report["profile"]["total_pnl"] == 2.67
    payload = json.dumps(report).encode()
    with pytest.raises(ValueError, match=r"report=2\.67, independent=2\.68"):
        verify_report(source, payload, verify_fifo=True)
