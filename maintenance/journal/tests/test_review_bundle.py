"""Verify portable ZIP bundles against the actual synthetic report producer."""

from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from maintenance.journal import review_bundle, review_receipt

ROOT = Path(__file__).resolve().parents[3]
JOURNAL = ROOT / "maintenance/journal"
FIXTURE = JOURNAL / "examples/synthetic_review_trades.csv"


@pytest.fixture(scope="module")
def evidence(tmp_path_factory):
    report = tmp_path_factory.mktemp("csv-report") / "report.json"
    subprocess.run(
        [sys.executable, "-m", "maintenance.journal.review_trades", str(FIXTURE), "--output", str(report)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "agent")},
        check=True,
        capture_output=True,
    )
    return FIXTURE.read_bytes(), report.read_bytes()


def make_archive(entries, *, compression=0):
    out = BytesIO()
    with ZipFile(out, "w", compression=compression) as archive:
        for name, payload in entries:
            archive.writestr(name, payload)
    return out.getvalue()


def test_build_and_verify_transfer_identical_bytes(evidence, tmp_path):
    source, report = evidence
    first, repeat = tmp_path / "first.zip", tmp_path / "repeat.zip"
    built = review_bundle.build_bundle(source, report, first)
    review_bundle.build_bundle(source, report, repeat, verify_fifo=True)
    assert first.read_bytes() == repeat.read_bytes()
    assert built["bundle_sha256"] == sha256(first.read_bytes()).hexdigest()
    assert built["cash_check"]["net_trade_cash"] == "1.61"
    assert review_bundle.verify_bundle(first.read_bytes(), expected_sha256=built["bundle_sha256"]) == built
    with ZipFile(first) as zf:
        assert zf.namelist() == ["trades.csv", "report.json"]
        assert (zf.read("trades.csv"), zf.read("report.json")) == evidence
    with pytest.raises(FileExistsError):
        review_bundle.build_bundle(source, report, first)
    assert first.read_bytes() == repeat.read_bytes()


def test_build_refuses_inconsistent_source_and_report(evidence, tmp_path):
    source, report = evidence
    target = tmp_path / "unpublished.zip"
    with pytest.raises(ValueError, match="source CSV SHA256"):
        review_bundle.build_bundle(source + b"\n", report, target)
    assert not target.exists()


@pytest.mark.parametrize("wrong_hash", ["0" * 64, "too-short"])
def test_verification_requires_the_expected_hash_if_supplied(evidence, wrong_hash):
    payload = make_archive(zip(review_bundle.MEMBERS, evidence, strict=True))
    with pytest.raises(ValueError, match="SHA256"):
        review_bundle.verify_bundle(payload, expected_sha256=wrong_hash)


def test_duplicate_extra_and_non_regular_members_are_rejected(evidence):
    source, report = evidence
    entries = [("trades.csv", source), ("report.json", report)]
    with pytest.warns(UserWarning, match="Duplicate name"):
        duplicate = make_archive([*entries, ("trades.csv", source)])
    extra = make_archive([*entries, ("../ignored", b"extra")])
    link = ZipInfo("report.json")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    non_regular = make_archive([("trades.csv", source), (link, report)])
    for archive in (duplicate, extra):
        with pytest.raises(ValueError, match="exactly one"):
            review_bundle.verify_bundle(archive)
    with pytest.raises(ValueError, match="regular, unencrypted, uncompressed"):
        review_bundle.verify_bundle(non_regular)


def test_compressed_and_damaged_members_are_rejected(evidence):
    entries = list(zip(review_bundle.MEMBERS, evidence, strict=True))
    with pytest.raises(ValueError, match="uncompressed"):
        review_bundle.verify_bundle(make_archive(entries, compression=ZIP_DEFLATED))
    payload = make_archive(entries)
    corrupted = payload.replace(b"S001,", b"X001,", 1)
    assert corrupted != payload
    with pytest.raises(BadZipFile, match="CRC"):
        review_bundle.verify_bundle(corrupted)


def test_bundle_size_is_bounded(evidence, tmp_path, monkeypatch):
    source, report = evidence
    payload = make_archive(zip(review_bundle.MEMBERS, evidence, strict=True))
    monkeypatch.setattr(review_bundle, "MAX_BUNDLE_BYTES", len(source))
    output = tmp_path / "bundle.zip"
    with pytest.raises(ValueError, match="size limit"):
        review_bundle.build_bundle(source, report, output)
    with pytest.raises(ValueError, match="size limit"):
        review_bundle.verify_bundle(payload)
    assert not output.exists()


@pytest.mark.parametrize("fifo", [False, True])
def test_only_the_trusted_checkout_modules_and_zip_are_needed(evidence, tmp_path, fifo):
    source, report = evidence
    end = tmp_path / "receiver"
    package = end / "maintenance/journal"
    package.mkdir(parents=True)
    for script in ("review_bundle.py", "review_trades.py"):
        shutil.copyfile(JOURNAL / script, package / script)
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    command = [sys.executable, "-S", "-m", "maintenance.journal.review_bundle"]
    flags = ["--verify-fifo"] if fifo else []
    (end / "source.csv").write_bytes(source)
    (end / "report.json").write_bytes(report)
    built = subprocess.check_output(
        [*command, "build", "source.csv", "report.json", "--output", "review.zip", *flags],
        cwd=end,
        env=env,
        text=True,
    )
    (end / "source.csv").unlink()
    (end / "report.json").unlink()
    expected = json.loads(built)["bundle_sha256"]
    verified = subprocess.check_output(
        [*command, "verify", "review.zip", "--expected-sha256", expected, *flags],
        cwd=end,
        env=env,
        text=True,
    )
    result = json.loads(verified)
    assert result == json.loads(built)
    if fifo:
        assert result["fifo_match_count"] == 3
        assert result["recomputed_fifo_pnl"] == "1.61"
        assert result["fifo_matches"][0]["buy_execution_id"] == "S001"
    assert {path.name for path in end.iterdir()} == {"maintenance", "review.zip"}


def test_strict_fifo_rejects_a_matching_cash_check_with_changed_fifo(evidence, tmp_path):
    source, report = evidence
    changed = json.loads(report)
    changed["profile"]["total_pnl"] = 1.62
    changed["cash_check"]["fifo_minus_cash"] = "0.01"
    changed_bytes = json.dumps(changed).encode()
    old = tmp_path / "old.zip"
    review_bundle.build_bundle(source, changed_bytes, old)
    assert review_bundle.verify_bundle(old.read_bytes())["status"] == "ok"
    with pytest.raises(ValueError, match="FIFO total differs"):
        review_bundle.verify_bundle(old.read_bytes(), verify_fifo=True)
    strict = tmp_path / "unpublished.zip"
    with pytest.raises(ValueError, match="FIFO total differs"):
        review_bundle.build_bundle(source, changed_bytes, strict, verify_fifo=True)
    assert not strict.exists()


def test_html_receipt_with_only_the_trusted_modules_and_zip(evidence, tmp_path):
    package = tmp_path / "maintenance/journal"
    package.mkdir(parents=True)
    for script in ("review_bundle.py", "review_trades.py", "review_receipt.py"):
        shutil.copyfile(JOURNAL / script, package / script)
    bundle = tmp_path / "review.zip"
    built = review_bundle.build_bundle(*evidence, bundle, verify_fifo=True)
    command = [sys.executable, "-S", "-m", "maintenance.journal.review_bundle", "verify", "review.zip"]
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}

    def run(expected, output):
        return subprocess.run(
            [*command, "--expected-sha256", expected, "--verify-fifo", "--output-html", output],
            cwd=tmp_path,
            env=env,
            capture_output=True,
        )

    output = tmp_path / "receipt.html"
    failed = run("0" * 64, output.name)
    assert failed.returncode == 2
    assert not output.exists()
    result = run(built["bundle_sha256"], output.name)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == built
    html = output.read_text(encoding="utf-8")
    assert "已匹配预先记录的 SHA256" in html
    assert "1.61 USD" in html
    assert "独立复算 3 段" in html
    for field in ("bundle_sha256", "source_sha256", "report_sha256"):
        assert built[field] in html
    assert "S001" in html
    repeated = run(built["bundle_sha256"], output.name)
    assert repeated.returncode == 2
    assert output.read_text(encoding="utf-8") == html


def test_html_distinguishes_the_verification_scope_and_escapes_ids(evidence, tmp_path):
    bundle = tmp_path / "review.zip"
    basic = review_bundle.build_bundle(*evidence, bundle)
    html = review_receipt.render_receipt(basic, matched_expected_sha256=False).decode()
    assert "核验范围为包内文件的一致性" in html
    assert "报告所列 FIFO 盈亏" in html
    assert "<table>" not in html

    strict = review_bundle.verify_bundle(bundle.read_bytes(), verify_fifo=True)
    strict["fifo_matches"][0]["buy_execution_id"] = '<img src="example.invalid">'
    html = review_receipt.render_receipt(strict, matched_expected_sha256=False).decode()
    assert "&lt;img src=&quot;example.invalid&quot;&gt;" in html
    assert "<img" not in html
