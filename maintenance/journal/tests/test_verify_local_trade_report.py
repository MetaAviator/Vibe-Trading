"""Portable cash verification for reports from the local trade journal."""

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from maintenance.journal.review_trades import build_report, verify_report

JOURNAL = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def evidence():
    source = (JOURNAL / "examples/synthetic_review_trades.csv").read_bytes()
    return source, json.dumps(build_report(source)).encode()


@pytest.mark.parametrize(
    "field, value, error",
    [
        ("currency", "HKD", "currency, date range or row count"),
        ("source_rows", True, "currency, date range or row count"),
        ("date_range", "2026-01-01 ~ 2026-01-05", "currency, date range or row count"),
        ("cash_check", {"net_trade_cash": "1.60"}, "cash amounts or FIFO difference"),
        ("profile", {"total_pnl": 1.62}, "cash amounts or FIFO difference"),
        ("profile", {"total_pnl": "1.61"}, "finite numeric FIFO total"),
        ("schema_version", True, "version 1"),
    ],
)
def test_report_fields_must_match_recalculation(evidence, field, value, error):
    source, report = evidence
    changed = json.loads(report)
    changed[field] = value
    with pytest.raises(ValueError, match=error):
        verify_report(source, json.dumps(changed).encode())


def test_rejects_different_source_bytes_and_ambiguous_json(evidence):
    source, report = evidence
    with pytest.raises(ValueError, match="SHA256"):
        verify_report(source.replace(b"\n", b"\r\n"), report)
    with pytest.raises(ValueError, match="duplicate fields"):
        verify_report(source, b'{"schema_version": 1, ' + report[1:])
    with pytest.raises(ValueError, match="non-finite number"):
        verify_report(source, report.replace(b'"total_pnl": 1.61', b'"total_pnl": NaN'))


def test_standard_library_cli_on_only_the_transferred_files(evidence, tmp_path):
    source, report = evidence
    module = tmp_path / "maintenance/journal"
    module.mkdir(parents=True)
    shutil.copyfile(JOURNAL / "review_trades.py", module / "review_trades.py")
    source_path = tmp_path / "trades.csv"
    report_path = tmp_path / "report.json"
    source_path.write_bytes(source)
    report_path.write_bytes(report)
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-m",
            "maintenance.journal.review_trades",
            str(source_path),
            "--verify-report",
            str(report_path),
        ],
        cwd=tmp_path,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        check=True,
        capture_output=True,
        text=True,
    )
    status = json.loads(result.stdout)
    assert status["source_sha256"] == sha256(source).hexdigest()
    assert status["report_sha256"] == sha256(report).hexdigest()
    assert status["cash_check"]["net_trade_cash"] == "1.61"
    assert status["cash_check"]["fees"] == "0.39"
    assert status["reported_fifo_pnl"] == "1.61"
    assert status["fifo_check"] == "difference_arithmetic_only"
    assert source_path.read_bytes() == source and report_path.read_bytes() == report
