"""Synthetic-only acceptance for the standalone local price adapter."""

import csv
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from tests.module_os_helpers import patch_module_os

MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "maintenance/journal/sharadar_prices.py"
)
SPEC = importlib.util.spec_from_file_location("journal_sharadar_prices", MODULE_PATH)
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)

COLUMNS = [
    "ticker",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "closeadj",
    "closeunadj",
    "lastupdated",
]


def bar(ticker="AAA", day="2026-01-02", **changes):
    row = dict(
        ticker=ticker,
        date=day,
        open="10",
        high="11",
        low="9",
        close="10.12500",
        volume="100",
        closeadj="9.375000",
        closeunadj="20.2500",
        lastupdated="2026-01-06",
    )
    row.update(changes)
    return row


def write(tmp_path, rows=None, columns=COLUMNS):
    path = tmp_path / "synthetic.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows if rows is not None else [bar()])
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def read(path, digest, **changes):
    args = dict(
        tickers=["AAA"],
        start="2026-01-01",
        end="2026-01-06",
        price_field="close",
        expected_sha256=digest,
    )
    return adapter.read_sharadar_prices(path, **{**args, **changes})


def manifest(tmp_path, path, digest, rows=1):
    facts = dict(
        sha256=digest,
        bytes=path.stat().st_size,
        lines_minus_header=rows,
        cols=COLUMNS,
        pulled_at="2026-01-07T10:20:30",
    )
    output = tmp_path / "manifest.json"
    output.write_text(json.dumps({"stocks": facts}), encoding="utf-8")
    return output, facts


@pytest.mark.parametrize(
    "field,basis,price",
    [
        ("close", "split_adjusted", "10.12500"),
        ("closeadj", "split_dividend_spinoff_adjusted", "9.375000"),
        ("closeunadj", "unadjusted", "20.2500"),
    ],
)
def test_explicit_basis_preserves_decimal_text_and_ignores_volume(
    tmp_path, field, basis, price
):
    path, digest = write(tmp_path, [bar(volume="")])
    original = path.read_bytes()
    result = read(path, digest, price_field=field)
    assert result["prices"] == [
        dict(ticker="AAA", date="2026-01-02", price=price, lastupdated="2026-01-06")
    ]
    metadata = result["metadata"]
    assert metadata["price_field"] == field
    assert metadata["price_basis"] == basis
    assert metadata["snapshot_sha256"] == digest
    assert metadata["input_bytes"] == len(original)
    assert metadata["currency"] == "USD"
    assert metadata["extracted_at"] is None
    assert metadata["extracted_at_timezone"] == "unknown"
    assert path.read_bytes() == original


def test_window_is_inclusive_sorted_and_coverage_is_observed(tmp_path):
    path, digest = write(
        tmp_path,
        [bar("BBB", "2026-01-05"), bar("AAA", "2026-01-02"), bar("AAA", "2025-12-31")],
    )
    result = read(
        path, digest, tickers=[" aaa ", "BBB"], start="2026-01-02", end="2026-01-05"
    )
    assert [(r["ticker"], r["date"]) for r in result["prices"]] == [
        ("AAA", "2026-01-02"),
        ("BBB", "2026-01-05"),
    ]
    assert result["metadata"]["filters"] == dict(
        tickers=["AAA", "BBB"], start="2026-01-02", end="2026-01-05"
    )
    assert result["metadata"]["observed_coverage"]["AAA"] == dict(
        first_date="2026-01-02", last_date="2026-01-02", rows=1
    )
    assert result["metadata"]["expected_calendar_coverage"] == "not_verified"


@pytest.mark.parametrize(
    "bad", ["", "not-price", "NaN", "sNaN", "Infinity", "-Infinity", "0", "-1"]
)
def test_bad_selected_prices_cannot_fall_back_to_other_fields(tmp_path, bad):
    path, digest = write(tmp_path, [bar(close=bad)])
    with pytest.raises(ValueError, match="finite and positive"):
        read(path, digest)


@pytest.mark.parametrize("second", [bar(), bar(ticker=" aaa ", close="12")])
def test_duplicate_keys_reject_identical_and_conflicting_rows(tmp_path, second):
    path, digest = write(tmp_path, [bar(), second])
    with pytest.raises(ValueError, match="duplicate"):
        read(path, digest)


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"date": "2026-02-30"}, "valid date"),
        ({"date": "2026-1-02"}, "ISO"),
        ({"lastupdated": "bad"}, "ISO"),
        ({"lastupdated": "2025-12-31"}, "precedes"),
        ({"ticker": ""}, "empty ticker"),
    ],
)
def test_invalid_identity_dates_and_updates_fail(tmp_path, changes, match):
    path, digest = write(tmp_path, [bar(**changes)])
    with pytest.raises(ValueError, match=match):
        read(path, digest)


def test_invalid_requested_ticker_date_cannot_hide_in_window_filter(tmp_path):
    path, digest = write(tmp_path, [bar(), bar(day="invalid")])
    with pytest.raises(ValueError, match="requested ticker date"):
        read(path, digest)


def test_nonselected_values_are_outside_the_declared_validation_scope(tmp_path):
    path, digest = write(
        tmp_path, [bar(), bar(ticker="OTHER", day="invalid", close="bad")]
    )
    result = read(path, digest)
    assert len(result["prices"]) == 1
    assert result["metadata"]["source_rows_scanned"] == 2
    assert result["metadata"]["validation_scope"]["selected_window"] == [
        "unique keys",
        "positive finite price",
        "valid lastupdated",
    ]


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"price_field": "adjusted_close"}, "price_field"),
        ({"start": "2026-01-07"}, "later"),
        ({"end": "2026-02-30"}, "valid date"),
        ({"tickers": []}, "at least one"),
        ({"tickers": [""]}, "nonempty"),
        ({"tickers": "AAA"}, "collection"),
        ({"expected_sha256": "short"}, "SHA256"),
        ({"max_rows": 0}, "positive integer"),
        ({"max_rows": True}, "positive integer"),
    ],
)
def test_invalid_requests_fail(tmp_path, changes, match):
    path, digest = write(tmp_path)
    with pytest.raises(ValueError, match=match):
        read(path, digest, **changes)


def test_missing_field_duplicate_header_and_wrong_row_width(tmp_path):
    for text in (
        "ticker,date,close\nAAA,2026-01-02,10\n",
        "ticker,date,close,lastupdated,close\n",
        "ticker,date,close,lastupdated\nAAA,2026-01-02,10\n",
    ):
        path = tmp_path / "bad.csv"
        path.write_text(text)
        with pytest.raises(ValueError, match="columns|column count"):
            read(path, hashlib.sha256(path.read_bytes()).hexdigest())


def test_changed_unselected_data_still_invalidates_snapshot_hash(tmp_path):
    path, digest = write(tmp_path, [bar(), bar(ticker="OTHER")])
    path.write_bytes(path.read_bytes().replace(b"OTHER", b"CHANGED"))
    with pytest.raises(ValueError, match="SHA256 does not match"):
        read(path, digest)


def test_input_stat_change_rejects_result(tmp_path, monkeypatch):
    path, digest = write(tmp_path)
    states = iter([("before",), ("after",)])
    monkeypatch.setattr(adapter, "_file_state", lambda path: next(states))
    with pytest.raises(ValueError, match="changed during"):
        read(path, digest)


def test_manifest_matches_and_naive_time_is_not_assigned_a_timezone(tmp_path):
    path, digest = write(tmp_path)
    mp, _ = manifest(tmp_path, path, digest)
    result = read(path, digest, manifest_path=mp)
    assert result["metadata"]["extracted_at"] == "2026-01-07T10:20:30"
    assert result["metadata"]["extracted_at_timezone"] == "unknown"
    assert (
        result["metadata"]["manifest_sha256"]
        == hashlib.sha256(mp.read_bytes()).hexdigest()
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("sha256", "wrong"),
        ("bytes", 0),
        ("lines_minus_header", 9),
        ("cols", ["ticker"]),
    ],
)
def test_manifest_mismatches_fail(tmp_path, key, value):
    path, digest = write(tmp_path)
    mp, facts = manifest(tmp_path, path, digest)
    facts[key] = value
    mp.write_text(json.dumps({"stocks": facts}))
    with pytest.raises(ValueError, match="manifest does not match"):
        read(path, digest, manifest_path=mp)


def test_empty_missing_ticker_and_row_limit_do_not_return_partial_results(tmp_path):
    path, digest = write(tmp_path, [])
    with pytest.raises(ValueError, match="no prices"):
        read(path, digest)
    path, digest = write(tmp_path)
    with pytest.raises(ValueError, match="no prices"):
        read(path, digest, tickers=["AAA", "MISSING"])
    path, digest = write(tmp_path, [bar(), bar(day="2026-01-05")])
    with pytest.raises(ValueError, match="max_rows"):
        read(path, digest, max_rows=1)


def cli_args(path, digest, output):
    return [
        str(path),
        "--tickers",
        "AAA",
        "--start",
        "2026-01-01",
        "--end",
        "2026-01-06",
        "--price-field",
        "close",
        "--expected-sha256",
        digest,
        "--output",
        str(output),
    ]


def test_cli_success_and_existing_output_is_preserved(tmp_path):
    path, digest = write(tmp_path)
    output = tmp_path / "private" / "result.json"
    args = cli_args(path, digest, output)
    assert adapter.main(args) == 0
    assert json.loads(output.read_text())["prices"][0]["price"] == "10.12500"
    previous = output.read_bytes()
    assert adapter.main(args) == 2
    assert output.read_bytes() == previous
    assert list(output.parent.iterdir()) == [output]


def test_cli_hash_failure_creates_no_output_and_source_is_preserved(tmp_path):
    path, _ = write(tmp_path)
    original = path.read_bytes()
    output = tmp_path / "result.json"
    assert adapter.main(cli_args(path, "0" * 64, output)) == 2
    assert not output.exists()
    assert path.read_bytes() == original
    assert adapter.main(cli_args(path, "0" * 64, path)) == 2
    assert path.read_bytes() == original


def test_cli_never_overwrites_a_concurrently_created_output(tmp_path, monkeypatch):
    path, digest = write(tmp_path)
    output = tmp_path / "result.json"
    original_os = adapter.os
    link = original_os.link

    def competing_writer(source, destination):
        output.write_text("other writer")
        link(source, destination)

    patch_module_os(monkeypatch, adapter, link=competing_writer)
    assert original_os.link is link
    assert adapter.main(cli_args(path, digest, output)) == 2
    assert output.read_text() == "other writer"
    assert {p.name for p in tmp_path.iterdir()} == {path.name, output.name}


def test_cli_requires_an_explicit_price_field(tmp_path):
    path, digest = write(tmp_path)
    output = tmp_path / "result.json"
    args = cli_args(path, digest, output)
    index = args.index("--price-field")
    del args[index : index + 2]
    with pytest.raises(SystemExit) as error:
        adapter.main(args)
    assert error.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize(
    "content",
    [
        '"ticker,date,close,lastupdated\n',
        'ticker,date,close,lastupdated\n"AAA,2026-01-02,10,2026-01-06\n',
    ],
)
def test_cli_malformed_csv_syntax_returns_error_without_output(tmp_path, content):
    path = tmp_path / "malformed.csv"
    path.write_text(content)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    output = tmp_path / "result.json"
    assert adapter.main(cli_args(path, digest, output)) == 2
    assert not output.exists()
