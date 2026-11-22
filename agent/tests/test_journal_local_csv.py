"""Exercise the public local journal CLI with synthetic source bytes."""

from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from maintenance.journal.review_trades import build_report

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "maintenance/journal/examples/synthetic_review_trades.csv"


def test_review_trades_reconciles_source_cash_and_fifo() -> None:
    source = FIXTURE.read_bytes()
    report = build_report(source)
    assert report["source_sha256"] == sha256(source).hexdigest()
    assert report["source_rows"] == 5
    assert report["currency"] == "USD"
    assert report["cash_check"] == {
        "buy_gross": "140.00",
        "sell_gross": "142.00",
        "fees": "0.39",
        "net_trade_cash": "1.61",
        "fifo_minus_cash": "0.00",
    }
    assert report["profile"]["total_roundtrips"] == 3
    assert Decimal(str(report["profile"]["total_pnl"])) == Decimal("1.61")
    assert [item["sell_price"] for item in report["profile"]["roundtrips_sample"][:2]] == [22, 23]


def test_review_keeps_fifo_rounding_difference_visible() -> None:
    source = (
        "execution_id,datetime,symbol,side,quantity,price,amount,fee,currency\n"
        "B,2026-01-02 10:00:00,AAA,buy,3,1.00,3.00,0.01,USD\n"
        "S1,2026-01-02 11:00:00,AAA,sell,1,2.00,2.00,0,USD\n"
        "S2,2026-01-02 11:00:00,AAA,sell,1,2.00,2.00,0,USD\n"
        "S3,2026-01-02 11:00:00,AAA,sell,1,2.00,2.00,0,USD\n"
    )
    report = build_report(source.encode())
    assert report["cash_check"]["net_trade_cash"] == "2.99"
    assert report["profile"]["total_pnl"] == 3
    assert report["cash_check"]["fifo_minus_cash"] == "0.01"


@pytest.mark.parametrize(
    "old,new,reason",
    [
        (b",0.24,USD", b",,USD", "confirmed zero fee"),
        (b",0.24,USD", b",unknown,USD", "fee must contain"),
        (b"S002", b"S001", "duplicate execution_id"),
        (b"10:00:00", b"10:00:00+00:00", "source local time"),
        (b",40.00,0.24", b",39.00,0.24", "gross amount"),
        (b",0.24,USD", b",0.24,HKD", "expected a US equity symbol"),
        (b",AAA,buy,", b",AAA,sell,", "exceeds recorded long holdings"),
        (b"BBB,sell,1,97.00,97.00", b"BBB,sell,0.5,97.00,48.50", "must be closed"),
    ],
)
def test_review_trades_rejects_incomplete_or_inconsistent_input(old, new, reason) -> None:
    with pytest.raises(ValueError, match=reason):
        build_report(FIXTURE.read_bytes().replace(old, new, 1))


def test_cli_creates_report_once_without_exposing_source_path(tmp_path) -> None:
    source = tmp_path / "previous private export.csv"
    original = b"\xef\xbb\xbf" + FIXTURE.read_bytes().replace(b"\n", b"\r\n")
    source.write_bytes(original)
    output = tmp_path / "my evidence" / "report.json"
    command = [
        sys.executable,
        "-m",
        "maintenance.journal.review_trades",
        str(source),
        "--output",
        str(output),
    ]
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "agent")}
    first = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, check=True)
    assert "Wrote " in first.stdout
    saved = output.read_bytes()
    assert json.loads(saved)["source_sha256"] == sha256(original).hexdigest()
    assert source.name.encode() not in saved
    second = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True)
    assert second.returncode == 2
    assert output.read_bytes() == saved
    assert source.read_bytes() == original
