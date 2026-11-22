"""Build and independently check the public two-fill synthetic example."""

from __future__ import annotations

import argparse
import csv
from decimal import Decimal
from hashlib import sha256
import io
import json
import os
from pathlib import Path
from shutil import copyfile
import sys
from unittest.mock import patch
from zipfile import ZipFile

from maintenance.journal.research_bundle_check import verify_bundle

EXAMPLES = Path(__file__).resolve().parent
FIXTURE = EXAMPLES / "synthetic_replay_fills.csv"


def verify(directory: Path, expected_id: str) -> dict:
    bundle = directory / "research.zip"
    summary = verify_bundle(bundle, expected_package_id=expected_id)
    archive = bundle.read_bytes()
    if sha256(archive).hexdigest() != summary["archive_sha256"]:
        raise ValueError("research ZIP changed after verification")
    with ZipFile(io.BytesIO(archive)) as zipped:
        price_rows = list(csv.DictReader(io.StringIO(zipped.read("prices.csv").decode("utf-8"))))
    fills = (directory / "fills.csv").read_bytes().replace(b"\r\n", b"\n")
    if fills != FIXTURE.read_bytes().replace(b"\r\n", b"\n"):
        raise ValueError("fills do not match the published synthetic fixture")
    fill_rows = list(csv.DictReader(io.StringIO(fills.decode("utf-8"))))
    observed = {(row["ticker"], row["date"]): Decimal(row["close"]) for row in price_rows}
    if summary["price_field"] != "closeunadj" or any(
        observed.get((row["symbol"], row["datetime"][:10])) != Decimal(row["price"]) for row in fill_rows
    ):
        raise ValueError("a fill is missing its unadjusted source price")
    buy, sell = fill_rows
    buy_cash = Decimal(buy["quantity"]) * Decimal(buy["price"])
    sell_cash = Decimal(sell["quantity"]) * Decimal(sell["price"])
    fees = Decimal(buy["fee"]) + Decimal(sell["fee"])
    net = sell_cash - buy_cash - fees
    report = json.loads((directory / "journal.json").read_text(encoding="utf-8"), parse_float=Decimal)
    profile = report.get("profile", {})
    roundtrips = profile.get("roundtrips_sample", [])
    expected_roundtrip = {
        "symbol": buy["symbol"],
        "buy_dt": buy["datetime"],
        "sell_dt": sell["datetime"],
        "qty": Decimal(buy["quantity"]),
        "buy_price": Decimal(buy["price"]),
        "sell_price": Decimal(sell["price"]),
        "currency": buy["currency"],
        "pnl": net,
    }
    if (
        report.get("status") != "ok"
        or report.get("total_records") != 2
        or profile.get("total_trades") != 2
        or profile.get("total_roundtrips") != 1
        or profile.get("total_pnl") != net
        or len(roundtrips) != 1
        or any(roundtrips[0].get(key) != value for key, value in expected_roundtrip.items())
    ):
        raise ValueError("journal report differs from independently recomputed USD P&L")
    return {
        "package_id": summary["package_id"],
        "source_sha256": summary["snapshot_sha256"],
        "price_field": summary["price_field"],
        "closed_trades": 1,
        "fees_usd": f"{fees:.2f}",
        "net_pnl_usd": f"{net:.2f}",
    }


def build(directory: Path) -> dict:
    from maintenance.journal.research_bundle import build_bundle
    from maintenance.journal.research_viewer import render_viewer
    from src.tools.trade_journal_tool import analyze_trade_journal

    directory.mkdir(parents=True, exist_ok=False)
    bundle = directory / "research.zip"
    summary = build_bundle(EXAMPLES / "synthetic_replay_task.json", bundle)
    render_viewer(bundle, directory / "research.html", expected_package_id=summary["package_id"])
    fills = directory / "fills.csv"
    copyfile(FIXTURE, fills)
    with patch.dict(os.environ, {"VIBE_TRADING_ALLOWED_FILE_ROOTS": str(directory)}):
        report = analyze_trade_journal(str(fills), analysis_type="profile")
    (directory / "journal.json").write_text(report + "\n", encoding="utf-8")
    return verify(directory, summary["package_id"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build").add_argument("directory", type=Path)
    check = commands.add_parser("verify")
    check.add_argument("directory", type=Path)
    check.add_argument("--expected-id", required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    try:
        if args.command == "build":
            from pytest_socket import disable_socket

            disable_socket()
            result = build(directory)
        else:
            result = verify(directory, args.expected_id)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
