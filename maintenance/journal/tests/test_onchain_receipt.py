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


@pytest.fixture
def rpc_inputs(inputs):
    context, block, transaction = (json.loads(inputs[name]) for name in ("context", "block", "transaction"))
    context.update(evidence_kind="public_rpc_snapshot", rpc_source="Example RPC")
    block["transactions"] = ["0x" + "ab" * 32] * int(transaction["transactionIndex"], 16) + [transaction["hash"]]
    raw = {
        "block": block,
        "transaction": transaction,
        "receipt": json.loads(inputs["receipt"]),
        "finalized": {"number": hex(int(block["number"], 16) + 64), "hash": "0x" + "99" * 32},
    }
    return {
        "context": json.dumps(context).encode(),
        **{name: json.dumps({"jsonrpc": "2.0", "id": 1, "result": body}).encode() for name, body in raw.items()},
    }


def test_rpc_package_retains_envelopes_and_recomputes(rpc_inputs):
    payload, report = onchain_receipt.build_package(rpc_inputs)
    assert report["evidence_kind"] == "public_rpc_snapshot"
    assert report["rpc_source"] == "Example RPC"
    assert report["observed_finalized_head"]["number"] == report["block_number"] + 64
    assert report["gas"]["fee_eth"] == "0.003"
    assert onchain_receipt.verify_package(payload, report["bundle_sha256"]) == report
    assert {name: raw.encode() for name, raw in json.loads(payload)["inputs"].items()} == rpc_inputs


@pytest.mark.parametrize(
    ("name", "change", "message"),
    [
        ("block", lambda data: data["result"]["transactions"].pop(), "present at the recorded index"),
        ("finalized", lambda data: data["result"].update(number="0x1"), "finalized head is inconsistent"),
        ("receipt", lambda data: data.update(error={"code": -32603}), "successful JSON-RPC 2.0 response"),
    ],
)
def test_rpc_package_rejects_inconsistent_snapshots(rpc_inputs, name, change, message):
    _update(rpc_inputs, name, change)
    with pytest.raises(ValueError, match=message):
        onchain_receipt.build_package(rpc_inputs)


@pytest.fixture
def balance_inputs(rpc_inputs):
    _update(rpc_inputs, "context", lambda data: data.update(balance_check="block_hash"))
    parent = "0x" + "12" * 32
    _update(rpc_inputs, "block", lambda data: data["result"].update(parentHash=parent))
    block_hash = json.loads(rpc_inputs["block"])["result"]["hash"]
    context = json.loads(rpc_inputs["context"])
    wallet = context["wallet"]
    token0, token1 = (token["address"] for token in context["tokens"])
    values = {
        "before": {"native": 10**18, token0: 1_000_000_000, token1: 0},
        "after": {"native": 997_000_000_000_000_000, token0: 749_875_000, token1: 10**17},
    }
    observations = {}
    for phase, block in (("before", parent), ("after", block_hash)):
        mark = {"blockHash": block, "requireCanonical": True}
        entries = {}
        for index, (asset, balance) in enumerate(values[phase].items(), start=1):
            if asset == "native":
                method, params, result = "eth_getBalance", [wallet, mark], hex(balance)
            else:
                method = "eth_call"
                params = [{"to": asset, "data": "0x70a08231" + wallet[2:].zfill(64)}, mark]
                result = "0x" + balance.to_bytes(32).hex()
            entries[asset] = {
                "request": {"method": method, "params": params, "jsonrpc": "2.0", "id": index},
                "response": json.dumps({"jsonrpc": "2.0", "id": index, "result": result}),
            }
        observations[phase] = entries
    return {**rpc_inputs, "balances": json.dumps(observations).encode()}


def test_balances_reconcile_the_erc20_flows_and_native_gas(balance_inputs):
    payload, report = onchain_receipt.build_package(balance_inputs)
    assert [asset["delta_atomic"] for asset in report["block_balance_check"]["assets"]] == [
        "-3000000000000000",
        "-250125000",
        "100000000000000000",
    ]
    assert report["block_balance_check"]["parent_block_hash"] == "0x" + "12" * 32
    assert onchain_receipt.verify_package(payload, report["bundle_sha256"]) == report


def test_an_explicit_balance_check_requires_its_input(balance_inputs):
    del balance_inputs["balances"]
    with pytest.raises(ValueError, match="expected exactly these inputs: .*balances"):
        onchain_receipt.summarize(balance_inputs)


@pytest.mark.parametrize(
    ("change", "error"),
    [
        (
            lambda data: data["after"]["native"]["request"]["params"][1].update(requireCanonical=False),
            "canonical block hash",
        ),
        (
            lambda data: data["after"]["native"]["request"]["params"].__setitem__(0, "0x" + "de" * 20),
            "match the wallet",
        ),
        (
            lambda data: data["after"]["native"]["request"]["params"][1].update(blockHash="0x" + "12" * 32),
            "canonical block hash",
        ),
        (
            lambda data: data["after"]["native"].update(response='{"jsonrpc":"2.0","id":2,"result":"0x0"}'),
            "match the request id",
        ),
        (
            lambda data: data["after"]["native"].update(response='{"jsonrpc":"2.0","id":1,"result":"0x0"}'),
            "delta does not match",
        ),
    ],
)
def test_balance_check_rejects_mismatched_observations(balance_inputs, change, error):
    _update(balance_inputs, "balances", change)
    with pytest.raises(ValueError, match=error):
        onchain_receipt.summarize(balance_inputs)
