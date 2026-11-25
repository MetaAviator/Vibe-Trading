"""Check the evidence boundaries and package handoff for one synthetic swap."""

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from maintenance.journal import onchain_receipt


EXAMPLES = Path(__file__).resolve().parents[1] / "examples/synthetic_onchain"


@pytest.fixture
def inputs():
    return {path.stem: path.read_bytes() for path in EXAMPLES.glob("*.json")}


def _update(inputs, name, change):
    data = json.loads(inputs[name])
    change(data)
    inputs[name] = json.dumps(data).encode()


def test_report_recomputes_exact_token_units_and_gas(inputs):
    report = onchain_receipt.summarize(inputs)
    assert report["status"] == "swap"
    assert report["swap_log_index"] == 9
    assert [
        (flow["symbol"], flow["direction"], flow["amount"], flow["transfer_log_index"])
        for flow in report["wallet_erc20_flows"]
    ] == [("TEST-USDC", "out", "250.125", 8), ("TEST-WETH", "in", "0.1", 7)]
    assert report["gas"] == {
        "used": "125000",
        "price_wei": "24000000000",
        "fee_wei": "3000000000000000",
        "fee_eth": "0.003",
    }


def test_reverted_transaction_records_gas_without_swap_flows(inputs):
    _update(inputs, "receipt", lambda receipt: receipt.update(status="0x0", logs=[]))
    report = onchain_receipt.summarize(inputs)
    assert report["status"] == "reverted"
    assert report["wallet_erc20_flows"] == []
    assert report["swap_log_index"] is None
    assert report["gas"]["fee_eth"] == "0.003"


@pytest.mark.parametrize(
    ("name", "change", "error"),
    [
        ("receipt", lambda data: data["logs"].pop(), "one Swap and two"),
        ("receipt", lambda data: data["logs"][2].update(address="0x" + "ab" * 20), "unsupported or duplicate event"),
        ("receipt", lambda data: data["logs"][0].update(data="0x" + "01" * 32), "does not match the pool"),
        ("receipt", lambda data: data["logs"][1].update(logIndex="0x7"), "unique and increasing"),
        ("receipt", lambda data: data["logs"][1].update(removed=True), "same transaction and block"),
        (
            "receipt",
            lambda data: data["logs"][1].update(transactionHash="0x" + "ab" * 32),
            "same transaction and block",
        ),
        ("receipt", lambda data: data.pop("effectiveGasPrice"), "effectiveGasPrice"),
        ("receipt", lambda data: data.update(status="0x0"), "invalid receipt status or logs"),
        ("receipt", lambda data: data.update(type="0x3"), "type-2"),
        ("transaction", lambda data: data.update(value="0x1"), "native ETH transaction value"),
        ("transaction", lambda data: data.update(chainId="0x2"), "mainnet chain parameters"),
        ("block", lambda data: data.update(hash="0x" + "ef" * 32), "references differ"),
        ("context", lambda data: data["tokens"][0].pop("decimals"), "token.decimals"),
    ],
)
def test_rejects_incomplete_or_contradictory_evidence(inputs, name, change, error):
    _update(inputs, name, change)
    with pytest.raises(ValueError, match=error):
        onchain_receipt.build_package(inputs)


def test_rejects_ambiguous_raw_json(inputs):
    inputs["receipt"] = inputs["receipt"].replace(b"{", b'{"status":"0x0",', 1)
    with pytest.raises(ValueError, match="duplicate JSON key: status"):
        onchain_receipt.build_package(inputs)


def test_valid_digest_alone_cannot_bypass_report_recalculation(inputs):
    package, report = onchain_receipt.build_package(inputs)
    with pytest.raises(ValueError, match="SHA256 does not match"):
        onchain_receipt.verify_package(package, "0" * 64)
    assert onchain_receipt.verify_package(package, report["bundle_sha256"]) == report
    changed = json.loads(package)
    changed["report"]["gas"]["fee_wei"] = "0"
    tampered = json.dumps(changed).encode()
    with pytest.raises(ValueError, match="independently recomputed"):
        onchain_receipt.verify_package(tampered, sha256(tampered).hexdigest())


def test_receiver_needs_only_one_trusted_python_module_and_the_evidence(inputs, tmp_path):
    inputs["receipt"] = inputs["receipt"].replace(b"\n", b"\r\n")
    payload, report = onchain_receipt.build_package(inputs)
    assert payload == onchain_receipt.build_package(inputs)[0]
    originals = json.loads(payload)["inputs"]
    assert {name: text.encode() for name, text in originals.items()} == inputs

    module = tmp_path / "maintenance/journal/onchain_receipt.py"
    module.parent.mkdir(parents=True)
    shutil.copyfile(onchain_receipt.__file__, module)
    (tmp_path / "transfer.json").write_bytes(payload)
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    received = subprocess.check_output(
        [
            sys.executable,
            "-S",
            "-m",
            "maintenance.journal.onchain_receipt",
            "verify",
            "transfer.json",
            "--expected-sha256",
            report["bundle_sha256"],
        ],
        cwd=tmp_path,
        env=env,
    )
    assert json.loads(received) == report
