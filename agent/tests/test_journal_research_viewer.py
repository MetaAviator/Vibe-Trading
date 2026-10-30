"""Synthetic acceptance for the offline, read-only research bundle viewer."""

import base64
import csv
import hashlib
import io
import json
from html.parser import HTMLParser

import pytest

from maintenance.journal import research_bundle as bundle
from maintenance.journal import research_viewer as viewer
from tests.module_os_helpers import patch_module_os
from tests.test_journal_research_bundle import (
    canonical,
    contents,
    make_package,
    repack,
    setup_task,
    write_task,
)


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.blocks = {}
        self.active = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag in {"script", "style"}:
            self.active = attrs.get("id", tag)
            self.blocks[self.active] = ""

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.active = None

    def handle_data(self, value):
        if self.active:
            self.blocks[self.active] += value


@pytest.mark.parametrize(
    "field,price,basis",
    [
        ("close", "10.12500", "split_adjusted"),
        ("closeadj", "9.375000", "split_dividend_spinoff_adjusted"),
        ("closeunadj", "20.2500", "unadjusted"),
    ],
)
def test_ac1_ac3_exact_prices_basis_identity_without_source(
    tmp_path, field, price, basis
):
    task, source, _ = setup_task(tmp_path, price_field=field)
    archive = tmp_path / "bundle.zip"
    summary = bundle.build_bundle(task, archive)
    original = archive.read_bytes()
    expected = list(csv.reader(io.StringIO(contents(archive)["prices.csv"].decode())))[
        1:
    ]
    task.unlink()
    source.unlink()
    output = tmp_path / "view.html"
    result = viewer.render_viewer(
        archive, output, expected_package_id=summary["package_id"]
    )
    text = output.read_text()
    model = json.loads(Page(text).blocks["prices-data"])
    actual = sorted(
        [day, ticker, value, updated]
        for ticker, rows in model["series"].items()
        for day, value, updated in rows
    )
    assert actual == expected
    assert model["series"]["AAA"][0][1] == price
    assert field in text and basis in text and "USD/share" in text
    assert summary["package_id"] in text and summary["snapshot_sha256"] in text
    assert "预先记录 ID 已匹配" in text
    assert "unknown" in text and "未验证" in text and "观测覆盖" in text
    assert "2026-01-02" in text and "2026-01-05" in text
    assert str(tmp_path) not in text
    assert archive.read_bytes() == original
    assert result["rows"] == 4
    assert result["viewer_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_ac4_text_escaping_and_self_contained_csp(tmp_path):
    malicious = '</SCRIPT><IMG SRC=X ONERROR=ALERT(1)>&"'
    task, source, config = setup_task(tmp_path, tickers=[malicious, "BBB"])
    # Use a real CSV writer so the embedded quote remains valid source data.
    rows = list(csv.reader(io.StringIO(source.read_text())))
    for row in rows[1:]:
        if row[0] == "AAA":
            row[0] = malicious
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(rows)
    source.write_text(stream.getvalue())
    config["expected_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    write_task(task, config)
    archive = tmp_path / "bundle.zip"
    bundle.build_bundle(task, archive)
    output = tmp_path / "view.html"
    viewer.render_viewer(archive, output)
    text = output.read_text()
    page = Page(text)
    assert malicious in json.loads(page.blocks["prices-data"])["series"]
    assert not any(tag in {"img", "iframe", "object", "link"} for tag, _ in page.tags)
    assert all("src" not in attrs and "href" not in attrs for _, attrs in page.tags)
    assert "&lt;/SCRIPT&gt;" in text
    assert "未提供外部 ID" in text
    policy = next(
        a["content"]
        for t, a in page.tags
        if t == "meta" and a.get("http-equiv") == "Content-Security-Policy"
    )
    for name in ("script", "style"):
        digest = base64.b64encode(
            hashlib.sha256(page.blocks[name].encode()).digest()
        ).decode()
        assert "'sha256-" + digest + "'" in policy
    assert "connect-src 'none'" in policy and "default-src 'none'" in policy


def test_ac1_ac4_ticker_attribute_preserves_internal_whitespace(tmp_path):
    ticker = "A  B\r\nC\tD"
    task, source, config = setup_task(tmp_path, tickers=[ticker, "BBB"])
    rows = list(csv.reader(io.StringIO(source.read_text())))
    for row in rows[1:]:
        if row[0] == "AAA":
            row[0] = ticker
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(rows)
    source.write_bytes(stream.getvalue().encode())
    config["expected_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    write_task(task, config)
    archive = tmp_path / "bundle.zip"
    bundle.build_bundle(task, archive)
    output = tmp_path / "view.html"
    viewer.render_viewer(archive, output)
    page = Page(output.read_text())
    assert ticker in [a["value"] for t, a in page.tags if t == "option"]


@pytest.mark.parametrize("stamp", ["2026-01-06T12:00:00", "2026-01-06T12:00:00+08:00"])
def test_ac1_recorded_extraction_and_timezone_are_preserved(tmp_path, stamp):
    _, _, _, archive, _ = make_package(tmp_path)
    members = contents(archive)
    meta = json.loads(members["metadata.json"])
    meta.update(extracted_at=stamp, extracted_at_evidence="local_manifest_record")
    meta["extracted_at_timezone"] = (
        "recorded_in_timestamp" if "+" in stamp else "unknown"
    )
    members["metadata.json"] = canonical(meta)
    from maintenance.journal.research_bundle_check import _render_report

    members["REPORT.md"] = _render_report(meta, json.loads(members["request.json"]))
    repack(archive, members, reseal=True)
    output = tmp_path / "view.html"
    viewer.render_viewer(archive, output)
    text = output.read_text()
    assert stamp in text and meta["extracted_at_timezone"] in text


@pytest.mark.parametrize("kind", ["zip", "checksum", "semantic", "id"])
def test_ac2_invalid_packages_do_not_create_output(tmp_path, kind):
    _, _, _, archive, _ = make_package(tmp_path)
    kwargs = {}
    if kind == "zip":
        archive.write_bytes(b"not a zip")
    elif kind in {"checksum", "semantic"}:
        members = contents(archive)
        meta = json.loads(members["metadata.json"])
        meta["currency"] = "EUR"
        members["metadata.json"] = canonical(meta)
        repack(archive, members, reseal=kind == "semantic")
    else:
        kwargs["expected_package_id"] = "0" * 64
    output = tmp_path / "view.html"
    with pytest.raises(ValueError):
        viewer.render_viewer(archive, output, **kwargs)
    assert not output.exists()


def test_ac2_changed_input_is_rejected(tmp_path, monkeypatch):
    _, _, _, archive, _ = make_package(tmp_path)
    verify = viewer.verify_bundle

    def replace_before_verify(path, **kwargs):
        members = contents(path)
        meta = json.loads(members["metadata.json"])
        meta["input_bytes"] += 1
        members["metadata.json"] = canonical(meta)
        repack(path, members, reseal=True)
        return verify(path, **kwargs)

    monkeypatch.setattr(viewer, "verify_bundle", replace_before_verify)
    with pytest.raises(ValueError, match="changed"):
        viewer.render_viewer(archive, tmp_path / "view.html")
    assert not (tmp_path / "view.html").exists()


def test_ac2_verified_bytes_used_after_path_replacement(tmp_path, monkeypatch):
    _, _, _, archive, _ = make_package(tmp_path)
    verify = viewer.verify_bundle

    def replace_after_verify(path, **kwargs):
        result = verify(path, **kwargs)
        archive.write_bytes(b"unverified replacement")
        return result

    monkeypatch.setattr(viewer, "verify_bundle", replace_after_verify)
    output = tmp_path / "view.html"
    viewer.render_viewer(archive, output)
    assert (
        json.loads(Page(output.read_text()).blocks["prices-data"])["series"]["AAA"][0][
            1
        ]
        == "9.375000"
    )


@pytest.mark.parametrize("limit", ["MAX_BUNDLE_BYTES", "MAX_VIEW_ROWS"])
def test_ac2_bounded_input_without_truncation(tmp_path, monkeypatch, limit):
    _, _, _, archive, _ = make_package(tmp_path)
    monkeypatch.setattr(viewer, limit, 1)
    with pytest.raises(ValueError, match="limit"):
        viewer.render_viewer(archive, tmp_path / "view.html")
    assert not (tmp_path / "view.html").exists()


def test_ac4_existing_symlink_and_input_path_preserved(tmp_path):
    _, _, _, archive, _ = make_package(tmp_path)
    output = tmp_path / "view.html"
    output.write_text("existing")
    with pytest.raises(ValueError, match="new file"):
        viewer.render_viewer(archive, output)
    assert output.read_text() == "existing"
    output.unlink()
    output.symlink_to(tmp_path / "absent")
    with pytest.raises(ValueError, match="new file"):
        viewer.render_viewer(archive, output)
    assert output.is_symlink() and not output.exists()
    original = archive.read_bytes()
    with pytest.raises(ValueError, match="new file"):
        viewer.render_viewer(archive, archive)
    assert archive.read_bytes() == original


@pytest.mark.parametrize("concurrent", [False, True])
def test_ac4_publish_failure_cleans_temporary_output(tmp_path, monkeypatch, concurrent):
    _, _, _, archive, _ = make_package(tmp_path)
    before = set(tmp_path.iterdir())
    output = tmp_path / "view.html"
    link = viewer.os.link

    def fail_or_compete(source, destination):
        if concurrent:
            output.write_text("concurrent")
            return link(source, destination)
        raise OSError("synthetic publish failure")

    patch_module_os(monkeypatch, viewer, link=fail_or_compete)
    with pytest.raises(OSError):
        viewer.render_viewer(archive, output)
    assert set(tmp_path.iterdir()) == before | ({output} if concurrent else set())
    if concurrent:
        assert output.read_text() == "concurrent"


def test_ac1_ac3_cli_success_and_errors(tmp_path, capsys):
    _, _, _, archive, summary = make_package(tmp_path)
    output = tmp_path / "nested/view.html"
    assert (
        viewer.main(
            [
                str(archive),
                "--output",
                str(output),
                "--expected-id",
                summary["package_id"],
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["rows"] == 4
    assert viewer.main([str(archive), "--output", str(output)]) == 2
    assert "error:" in capsys.readouterr().err
    assert (
        viewer.main(
            [str(tmp_path / "absent.zip"), "--output", str(tmp_path / "absent.html")]
        )
        == 2
    )
    assert not (tmp_path / "absent.html").exists()
