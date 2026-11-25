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

from maintenance.journal import review_bundle

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
    review_bundle.build_bundle(source, report, repeat)
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


def test_only_the_trusted_checkout_modules_and_zip_are_needed(evidence, tmp_path):
    source, report = evidence
    end = tmp_path / "receiver"
    package = end / "maintenance/journal"
    package.mkdir(parents=True)
    for script in ("review_bundle.py", "review_trades.py"):
        shutil.copyfile(JOURNAL / script, package / script)
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    command = [sys.executable, "-S", "-m", "maintenance.journal.review_bundle"]
    (end / "source.csv").write_bytes(source)
    (end / "report.json").write_bytes(report)
    built = subprocess.check_output(
        [*command, "build", "source.csv", "report.json", "--output", "review.zip"],
        cwd=end,
        env=env,
        text=True,
    )
    (end / "source.csv").unlink()
    (end / "report.json").unlink()
    expected = json.loads(built)["bundle_sha256"]
    verified = subprocess.check_output(
        [*command, "verify", "review.zip", "--expected-sha256", expected],
        cwd=end,
        env=env,
        text=True,
    )
    assert json.loads(verified) == json.loads(built)
    assert {path.name for path in end.iterdir()} == {"maintenance", "review.zip"}
