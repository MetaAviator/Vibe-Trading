"""Synthetic acceptance for reproducible local research input bundles."""

import hashlib
import json
import stat
import struct
import zipfile
from pathlib import Path

import pytest

from maintenance.journal import research_bundle as bundle
from maintenance.journal import research_bundle_check as checker
from tests.module_os_helpers import patch_module_os

EXAMPLES = Path(__file__).resolve().parents[2] / "maintenance/journal/examples"


def setup_task(tmp_path, **changes):
    source = tmp_path / "stocks.csv"
    source.write_bytes((EXAMPLES / "synthetic_stocks.csv").read_bytes())
    task = json.loads((EXAMPLES / "synthetic_task.json").read_text())
    task["source_csv"] = source.name
    task.update(changes)
    path = tmp_path / "task.json"
    path.write_text(json.dumps(task))
    return path, source, task


def write_task(path, task):
    path.write_text(json.dumps(task))


def contents(path):
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in checker.MEMBER_NAMES}


def canonical(value):
    # Independently reconstruct the published canonical JSON contract.
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False)
        + "\n"
    ).encode()


def repack(path, members, *, reseal=False, compression=zipfile.ZIP_STORED):
    data = dict(members)
    if reseal:
        manifest = json.loads(data["manifest.json"])
        manifest["files"] = {
            name: {
                "bytes": len(data[name]),
                "sha256": hashlib.sha256(data[name]).hexdigest(),
            }
            for name in checker.CONTENT_NAMES
        }
        manifest.pop("package_id")
        manifest["package_id"] = hashlib.sha256(canonical(manifest)).hexdigest()
        data["manifest.json"] = canonical(manifest)
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        for name, payload in data.items():
            archive.writestr(name, payload)


def make_package(tmp_path):
    task, source, config = setup_task(tmp_path)
    output = tmp_path / "bundle.zip"
    summary = bundle.build_bundle(task, output)
    return task, source, config, output, summary


@pytest.mark.parametrize(
    "field,price,basis",
    [
        ("close", "10.12500", "split_adjusted"),
        ("closeadj", "9.375000", "split_dividend_spinoff_adjusted"),
        ("closeunadj", "20.2500", "unadjusted"),
    ],
)
def test_explicit_field_decimal_provenance_and_observed_coverage(
    tmp_path, field, price, basis
):
    task, source, _ = setup_task(tmp_path, price_field=field)
    original_source, original_task = source.read_bytes(), task.read_bytes()
    output = tmp_path / "bundle.zip"
    summary = bundle.build_bundle(task, output)
    assert summary["rows"] == 4
    assert summary["price_basis"] == basis
    data = contents(output)
    assert (
        data["prices.csv"].decode().splitlines()[1]
        == f"2026-01-02,AAA,{price},2026-01-06"
    )
    meta = json.loads(data["metadata.json"])
    assert meta["observed_coverage"] == {
        ticker: {"first_date": "2026-01-02", "last_date": "2026-01-05", "rows": 2}
        for ticker in ("AAA", "BBB")
    }
    assert meta["price_field"] == field and meta["price_basis"] == basis
    assert meta["extracted_at"] is None and meta["extracted_at_timezone"] == "unknown"
    assert b"Coverage is observed only" in data["REPORT.md"]
    assert str(tmp_path).encode() not in b"".join(data.values())
    assert source.read_bytes() == original_source and task.read_bytes() == original_task
    assert (
        checker.verify_bundle(output, expected_package_id=summary["package_id"])
        == summary
    )


def test_same_inputs_rebuild_to_identical_zip_bytes_and_normalized_requests(tmp_path):
    task, source, config = setup_task(tmp_path)
    first, second = tmp_path / "one.zip", tmp_path / "two.zip"
    a = bundle.build_bundle(task, first)
    config["tickers"] = [" bbb ", "AAA", "aaa"]
    config["expected_sha256"] = config["expected_sha256"].upper()
    write_task(task, config)
    b = bundle.build_bundle(task, second)
    assert a == b
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert all(
            info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()
        )
        assert all(
            info.compress_type == zipfile.ZIP_STORED for info in archive.infolist()
        )
    relocated = tmp_path / "moved"
    relocated.mkdir()
    renamed = relocated / "different-name.csv"
    renamed.write_bytes(source.read_bytes())
    config["source_csv"] = renamed.name
    moved_task = relocated / "recipe.json"
    write_task(moved_task, config)
    third = relocated / "three.zip"
    assert bundle.build_bundle(moved_task, third) == a
    assert third.read_bytes() == first.read_bytes()


def test_source_is_read_once_and_verification_needs_no_source(tmp_path, monkeypatch):
    task, source, _ = setup_task(tmp_path)
    reader = bundle.read_sharadar_prices
    calls = []

    def counted(*args, **kwargs):
        calls.append(args[0])
        return reader(*args, **kwargs)

    monkeypatch.setattr(bundle, "read_sharadar_prices", counted)
    output = tmp_path / "bundle.zip"
    expected = bundle.build_bundle(task, output)
    assert len(calls) == 1
    source.unlink()
    task.unlink()
    assert checker.verify_bundle(output) == expected


def test_manifest_time_and_exact_manifest_identity_are_retained(tmp_path):
    task, source, config = setup_task(tmp_path)
    manifest = tmp_path / "pull_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "stocks": {
                    "sha256": config["expected_sha256"],
                    "bytes": source.stat().st_size,
                    "lines_minus_header": 4,
                    "cols": source.read_text().splitlines()[0].split(","),
                    "pulled_at": "2026-01-07T10:20:30",
                }
            }
        )
    )
    config["manifest"] = manifest.name
    write_task(task, config)
    output = tmp_path / "bundle.zip"
    bundle.build_bundle(task, output)
    data = contents(output)
    metadata, request = json.loads(data["metadata.json"]), json.loads(
        data["request.json"]
    )
    assert (
        request["source_manifest_sha256"]
        == hashlib.sha256(manifest.read_bytes()).hexdigest()
    )
    assert metadata["extracted_at"] == "2026-01-07T10:20:30"
    assert metadata["extracted_at_timezone"] == "unknown"


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"schema_version": 2},
        {"unknown": "typo"},
        {"source_csv": ""},
        {"source_csv": "https://example.invalid/stocks.csv"},
        {"expected_sha256": "bad"},
        {"expected_sha256": None},
        {"tickers": "AAA"},
        {"tickers": []},
        {"tickers": [None]},
        {"tickers": [" "]},
        {"start": "2026-1-02"},
        {"start": "2026-02-30"},
        {"end": "2026-01-01"},
        {"price_field": "unknown"},
        {"price_field": None},
        {"max_rows": True},
        {"max_rows": 0},
        {"manifest": None},
    ],
)
def test_bad_config_never_creates_a_result(tmp_path, changes):
    task, _, _ = setup_task(tmp_path, **changes)
    output = tmp_path / "new" / "bundle.zip"
    with pytest.raises(ValueError):
        bundle.build_bundle(task, output)
    assert not output.exists()
    assert not output.parent.exists()


@pytest.mark.parametrize(
    "content",
    [
        '{"schema_version":1,"schema_version":1}',
        '{"schema_version":NaN}',
        "[]",
        "{",
    ],
)
def test_duplicate_nonfinite_and_malformed_json_fail(tmp_path, content):
    task = tmp_path / "task.json"
    task.write_text(content)
    with pytest.raises(ValueError):
        bundle.build_bundle(task, tmp_path / "result.zip")
    assert not (tmp_path / "result.zip").exists()


@pytest.mark.parametrize("key", ["price_field", "source_csv", "expected_sha256"])
def test_required_config_fields_are_not_guessed(tmp_path, key):
    task, _, config = setup_task(tmp_path)
    config.pop(key)
    write_task(task, config)
    with pytest.raises(ValueError, match="missing or unknown"):
        bundle.build_bundle(task, tmp_path / "result.zip")


def test_changed_snapshot_and_selected_invalid_price_fail_without_output(tmp_path):
    task, source, config = setup_task(tmp_path)
    source.write_bytes(source.read_bytes().replace(b"9.375000", b"NaN"))
    config["expected_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    write_task(task, config)
    output = tmp_path / "result.zip"
    with pytest.raises(ValueError, match="finite and positive"):
        bundle.build_bundle(task, output)
    assert not output.exists()
    source.write_bytes((EXAMPLES / "synthetic_stocks.csv").read_bytes())
    with pytest.raises(ValueError, match="SHA256 does not match"):
        bundle.build_bundle(task, output)
    assert not output.exists()


def test_existing_and_concurrent_outputs_are_preserved_and_temporary_file_removed(
    tmp_path, monkeypatch
):
    task, _, _ = setup_task(tmp_path)
    output = tmp_path / "result.zip"
    output.write_bytes(b"existing")
    with pytest.raises(ValueError, match="new file"):
        bundle.build_bundle(task, output)
    assert output.read_bytes() == b"existing"
    output.unlink()
    original_os = bundle.os
    link = original_os.link

    def competing_writer(source, destination):
        output.write_bytes(b"concurrent")
        link(source, destination)

    patch_module_os(monkeypatch, bundle, link=competing_writer)
    assert original_os.link is link
    with pytest.raises(FileExistsError):
        bundle.build_bundle(task, output)
    assert output.read_bytes() == b"concurrent"
    assert {p.name for p in tmp_path.iterdir()} == {
        "task.json",
        "stocks.csv",
        "result.zip",
    }


def test_publish_failure_cleans_temporary_file(tmp_path, monkeypatch):
    task, _, _ = setup_task(tmp_path)

    def fail(*args):
        raise OSError("synthetic publish failure")

    patch_module_os(monkeypatch, bundle, link=fail)
    with pytest.raises(OSError, match="synthetic publish failure"):
        bundle.build_bundle(task, tmp_path / "result.zip")
    assert {p.name for p in tmp_path.iterdir()} == {"task.json", "stocks.csv"}


@pytest.mark.parametrize("name", checker.CONTENT_NAMES)
def test_modified_content_without_matching_checksum_is_rejected(tmp_path, name):
    _, _, _, original, _ = make_package(tmp_path)
    data = contents(original)
    data[name] += b"changed"
    edited = tmp_path / "edited.zip"
    repack(edited, data)
    with pytest.raises(ValueError, match="checksum mismatch"):
        checker.verify_bundle(edited)


@pytest.mark.parametrize(
    "key,value",
    [
        ("price_basis", "unadjusted"),
        ("currency", "EUR"),
        ("expected_calendar_coverage", "verified"),
        ("extracted_at_timezone", "UTC"),
        (
            "observed_coverage",
            {
                "AAA": {
                    "first_date": "2026-01-02",
                    "last_date": "2026-01-05",
                    "rows": 999,
                }
            },
        ),
    ],
)
def test_rehashed_metadata_conflicts_still_fail(tmp_path, key, value):
    _, _, _, original, _ = make_package(tmp_path)
    data = contents(original)
    meta = json.loads(data["metadata.json"])
    meta[key] = value
    data["metadata.json"] = canonical(meta)
    edited = tmp_path / "edited.zip"
    repack(edited, data, reseal=True)
    with pytest.raises(ValueError):
        checker.verify_bundle(edited)


def test_report_and_request_semantic_conflicts_fail_even_after_rehash(tmp_path):
    _, _, _, original, _ = make_package(tmp_path)
    base = contents(original)
    edited = tmp_path / "edited.zip"
    data = dict(base)
    data["REPORT.md"] += b"Coverage verified."
    repack(edited, data, reseal=True)
    with pytest.raises(ValueError, match="report conflicts"):
        checker.verify_bundle(edited)
    data = dict(base)
    request = json.loads(data["request.json"])
    request["start"] = "2026-01-05"
    data["request.json"] = canonical(request)
    repack(edited, data, reseal=True)
    with pytest.raises(ValueError, match="outside"):
        checker.verify_bundle(edited)


def test_recorded_package_id_rejects_a_self_consistently_rehashed_price_edit(tmp_path):
    _, _, _, original, summary = make_package(tmp_path)
    data = contents(original)
    data["prices.csv"] = data["prices.csv"].replace(b"9.375000", b"999.0000")
    edited = tmp_path / "edited.zip"
    repack(edited, data, reseal=True)
    # Without an external identity pin this is internally consistent, not authenticated.
    assert checker.verify_bundle(edited)["package_id"] != summary["package_id"]
    with pytest.raises(ValueError, match="package_id mismatch"):
        checker.verify_bundle(edited, expected_package_id=summary["package_id"])


@pytest.mark.parametrize(
    "kind",
    [
        "extra",
        "duplicate",
        "traversal",
        "symlink",
        "compressed",
        "encrypted",
        "oversized",
    ],
)
def test_unsafe_or_unsupported_zip_members_are_rejected(tmp_path, kind):
    _, _, _, original, _ = make_package(tmp_path)
    data = contents(original)
    edited = tmp_path / "edited.zip"
    if kind in ("extra", "traversal"):
        data["extra.txt" if kind == "extra" else "../REPORT.md"] = b"unexpected"
    repack(
        edited,
        data,
        compression=(
            zipfile.ZIP_DEFLATED if kind == "compressed" else zipfile.ZIP_STORED
        ),
    )
    if kind == "duplicate":
        with zipfile.ZipFile(edited, "a") as archive:
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr("prices.csv", data["prices.csv"])
    if kind == "symlink":
        with zipfile.ZipFile(edited, "w") as archive:
            for name, payload in data.items():
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, payload)
    if kind in ("encrypted", "oversized"):
        raw = bytearray(edited.read_bytes())
        central = raw.index(b"PK\x01\x02")
        if kind == "encrypted":
            struct.pack_into(
                "<H",
                raw,
                central + 8,
                struct.unpack_from("<H", raw, central + 8)[0] | 1,
            )
        else:
            struct.pack_into("<I", raw, central + 24, checker.MAX_BUNDLE_BYTES + 1)
        edited.write_bytes(raw)
    with pytest.raises(ValueError):
        checker.verify_bundle(edited)
    assert not (tmp_path.parent / "REPORT.md").exists()


def test_cli_build_verify_errors_and_public_example(tmp_path, capsys):
    output = tmp_path / "demo.zip"
    assert (
        bundle.main(
            ["build", str(EXAMPLES / "synthetic_task.json"), "--output", str(output)]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["rows"] == 4
    assert (
        bundle.main(["verify", str(output), "--expected-id", summary["package_id"]])
        == 0
    )
    assert json.loads(capsys.readouterr().out) == summary
    assert bundle.main(["verify", str(output), "--expected-id", "0" * 64]) == 2
    assert "package_id mismatch" in capsys.readouterr().err
    assert (
        bundle.main(
            ["build", str(EXAMPLES / "synthetic_task.json"), "--output", str(output)]
        )
        == 2
    )
    assert output.exists()


def test_non_zip_and_invalid_recorded_identity_are_clear_errors(tmp_path):
    output = tmp_path / "not-zip"
    output.write_bytes(b"not a zip")
    with pytest.raises(ValueError, match="invalid bundle ZIP"):
        checker.verify_bundle(output)
    with pytest.raises(ValueError, match="SHA256"):
        checker.verify_bundle(output, expected_package_id="bad")


def test_task_and_archive_byte_limits_are_enforced(tmp_path, monkeypatch):
    task = tmp_path / "oversized.json"
    task.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="1 MiB"):
        bundle.build_bundle(task, tmp_path / "result.zip")
    _, _, _, original, _ = make_package(tmp_path)
    monkeypatch.setattr(checker, "MAX_BUNDLE_BYTES", 32)
    with pytest.raises(ValueError, match="size limit"):
        checker.verify_bundle(original)


def test_corrupt_zip_crc_is_rejected_without_extracting(tmp_path):
    _, _, _, original, _ = make_package(tmp_path)
    raw = bytearray(original.read_bytes())
    with zipfile.ZipFile(original) as archive:
        item = archive.getinfo("prices.csv")
        header = item.header_offset
    name_length, extra_length = struct.unpack_from("<HH", raw, header + 26)
    raw[header + 30 + name_length + extra_length] ^= 1
    damaged = tmp_path / "damaged.zip"
    damaged.write_bytes(raw)
    with pytest.raises(ValueError, match="invalid bundle ZIP"):
        checker.verify_bundle(damaged)
    assert not (tmp_path / "prices.csv").exists()
