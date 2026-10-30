"""Create a standalone offline HTML viewer from a verified research ZIP."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import io
import json
import os
import sys
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path
from string import Template
from typing import Any

from .research_bundle_check import MAX_BUNDLE_BYTES, verify_bundle

MAX_VIEW_ROWS = 100_000
BASIS_LABELS = {
    "split_adjusted": "拆股复权",
    "split_dividend_spinoff_adjusted": "拆股、股息与分拆复权",
    "unadjusted": "未复权",
}


def _escape(value: Any) -> str:
    return (
        html.escape(str(value), quote=True)
        .replace("\r", "&#13;")
        .replace("\n", "&#10;")
        .replace("\t", "&#9;")
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _policy_hash(text: str) -> str:
    digest = base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode()
    return "'sha256-" + digest + "'"


def _page(data: bytes, summary: dict[str, Any], external_id: bool) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        request = json.loads(archive.read("request.json"))
        metadata = json.loads(archive.read("metadata.json"))
        prices = archive.read("prices.csv").decode("utf-8")
    series: dict[str, list[list[str]]] = {t: [] for t in request["tickers"]}
    for row in csv.DictReader(io.StringIO(prices, newline="")):
        series[row["ticker"]].append([row["date"], row["close"], row["lastupdated"]])
    model = json.dumps({"series": series}, ensure_ascii=True, separators=(",", ":"))
    # JSON remains data even when a ticker contains an HTML closing script tag.
    model = (
        model.replace("<", r"\u003c").replace(">", r"\u003e").replace("&", r"\u0026")
    )
    directory = Path(__file__).resolve().parent
    css = (directory / "research_viewer.css").read_text(encoding="utf-8")
    script = (directory / "research_viewer.js").read_text(encoding="utf-8")
    policy = (
        "default-src 'none'; connect-src 'none'; object-src 'none'; "
        "base-uri 'none'; form-action 'none'; script-src "
        + _policy_hash(script)
        + "; style-src "
        + _policy_hash(css)
    )
    coverage = "".join(
        "<tr>"
        + "".join(
            f"<td>{_escape(value)}</td>"
            for value in (ticker, item["first_date"], item["last_date"], item["rows"])
        )
        + "</tr>"
        for ticker, item in metadata["observed_coverage"].items()
    )
    identities = "".join(
        f"<dt>{label}</dt><dd><code>{_escape(summary[key])}</code></dd>"
        for label, key in (
            ("Package ID", "package_id"),
            ("ZIP SHA256", "archive_sha256"),
            ("源快照 SHA256", "snapshot_sha256"),
        )
    )
    template = Template(
        (directory / "research_viewer.html").read_text(encoding="utf-8")
    )
    page = template.substitute(
        policy=_escape(policy),
        css=css,
        script=script,
        model=model,
        rows=summary["rows"],
        tickers=len(series),
        start=request["start"],
        end=request["end"],
        field=_escape(summary["price_field"]),
        basis=_escape(summary["price_basis"]),
        basis_label=BASIS_LABELS[summary["price_basis"]],
        extracted=_escape(metadata["extracted_at"] or "unknown"),
        timezone=_escape(metadata["extracted_at_timezone"]),
        external="预先记录 ID 已匹配" if external_id else "未提供外部 ID",
        options="".join(
            f'<option value="{_escape(t)}">{_escape(t)}</option>' for t in series
        ),
        coverage=coverage,
        identities=identities,
    )
    return page.encode("utf-8")


def render_viewer(
    bundle_path: str | Path,
    output_path: str | Path,
    *,
    expected_package_id: str | None = None,
) -> dict[str, Any]:
    """Verify a local v1 bundle and publish a new self-contained HTML file.

    The source CSV and original task are never opened. Prices remain strings;
    only the browser chart uses approximate numeric coordinates. Existing files,
    symlinks and concurrent outputs are never replaced. A changed bundle, invalid
    package, or viewer limit raises ValueError; local I/O errors raise OSError.
    Returns the verified bundle summary and the generated HTML's SHA256.
    """
    output = Path(output_path)
    if output.exists() or output.is_symlink():
        raise ValueError("output must be a new file; existing files are never replaced")
    path = Path(bundle_path)
    if path.stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError("bundle exceeds the viewer byte limit")
    with path.open("rb") as stream:
        data = stream.read(MAX_BUNDLE_BYTES + 1)
    if len(data) > MAX_BUNDLE_BYTES:
        raise ValueError("bundle exceeds the viewer byte limit")
    summary = verify_bundle(path, expected_package_id=expected_package_id)
    if summary["archive_sha256"] != _sha(data):
        raise ValueError("bundle changed while preparing the viewer")
    if summary["rows"] > MAX_VIEW_ROWS:
        raise ValueError(f"bundle exceeds the viewer row limit ({MAX_VIEW_ROWS})")
    rendered = _page(data, summary, expected_package_id is not None)
    temporary = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(rendered)
        os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {**summary, "viewer_sha256": _sha(rendered)}


def main(argv: Sequence[str] | None = None) -> int:
    """Run the local viewer CLI; return 0 on success or 2 on input/I/O failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-id")
    args = parser.parse_args(argv)
    try:
        summary = render_viewer(
            args.bundle, args.output, expected_package_id=args.expected_id
        )
    except (OSError, ValueError, UnicodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
