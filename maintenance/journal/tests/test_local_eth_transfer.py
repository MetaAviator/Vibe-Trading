"""Check the local transfer evidence contract without a signer or node."""

import json
from hashlib import sha256
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from maintenance.journal.examples import local_eth_transfer as lab


@pytest.fixture
def evidence():
    parent, block, tx_hash = ("0x" + name * 64 for name in ("a", "b", "c"))
    chain = 123456
    value, price = 10**16, 10**9
    gas = 21_000
    refs = {
        "from": "0x" + "1" * 40,
        "to": "0x" + "2" * 40,
        "blockHash": block,
        "blockNumber": "0x2",
        "transactionIndex": "0x0",
        "type": "0x2",
    }
    return {
        "schema_version": 1,
        "chain_id": chain,
        "transaction": {**refs, "hash": tx_hash, "chainId": hex(chain), "input": "0x", "value": hex(value)},
        "receipt": {
            **refs,
            "transactionHash": tx_hash,
            "logs": [],
            "status": "0x1",
            "gasUsed": hex(gas),
            "effectiveGasPrice": hex(price),
        },
        "before": {"hash": parent, "number": "0x1", "sender_balance": hex(10**18), "recipient_balance": "0x0"},
        "after": {
            "hash": block,
            "parentHash": parent,
            "number": "0x2",
            "transactions": [tx_hash],
            "sender_balance": hex(10**18 - value - gas * price),
            "recipient_balance": hex(value),
        },
    }


def test_a_copy_of_the_verifier_recomputes_with_only_python(evidence, tmp_path):
    script = tmp_path / "verify.py"
    shutil.copyfile(Path(lab.__file__), script)
    raw = json.dumps(evidence).encode()
    (tmp_path / "transfer.json").write_bytes(raw)
    verify = [sys.executable, "-S", "verify.py", "verify", "transfer.json", "--expected-sha256"]
    output = subprocess.check_output([*verify, sha256(raw).hexdigest()], cwd=tmp_path)
    assert json.loads(output)["transfer_wei"] == "10000000000000000"
    assert json.loads(output)["fee_wei"] == "21000000000000"
    failed = subprocess.run([*verify, "0" * 64], cwd=tmp_path, capture_output=True)
    assert failed.returncode == 2


@pytest.mark.parametrize(
    ("section", "field", "value", "error"),
    [
        ("receipt", "status", "0x0", "successful native transfer"),
        ("receipt", "gasUsed", "0x1", "do not reconcile"),
        ("after", "recipient_balance", "0x0", "do not reconcile"),
        ("after", "parentHash", "0x" + "c" * 64, "following the funded snapshot"),
        ("after", "transactions", [], "following the funded snapshot"),
        ("transaction", "chainId", "0x1", "unexpected local ETH transfer"),
    ],
)
def test_inconsistent_evidence_is_rejected(evidence, section, field, value, error):
    evidence[section][field] = value
    with pytest.raises(ValueError, match=error):
        lab.verify_evidence(evidence)


def test_pending_receipt_is_retried_without_broadcasting(evidence, monkeypatch):
    receipt = evidence["receipt"]
    replies = iter([None, receipt])

    class RPC:
        def call(self, method, tx_hash):
            assert method == "eth_getTransactionReceipt"
            assert tx_hash == receipt["transactionHash"]
            return next(replies)

    monkeypatch.setattr(lab, "sleep", lambda _delay: None)
    assert lab._wait_for_receipt(RPC(), receipt["transactionHash"]) == receipt


@pytest.fixture
def token_evidence(evidence):
    contract = "0x" + "3" * 40
    deployed = "0x" + "d" * 64
    sender, recipient = (evidence["transaction"][key] for key in ("from", "to"))
    creation = Path(lab.__file__).with_name("local_wallet") / "LabToken.hex"
    # This compiled init code returns its trailing runtime bytes unchanged.
    runtime = "0x" + creation.read_text(encoding="ascii").strip()[-2342:]
    evidence.update(
        asset="erc20", token={"address": contract, "recipient": recipient, "decimals": 6, "runtime_code": runtime}
    )
    evidence["transaction"].update(to=contract, value="0x0", input=lab._transfer_input(recipient))
    evidence["receipt"].update(to=contract, gasUsed=hex(51_482))
    before, after = evidence["before"], evidence["after"]
    before.update(
        transactions=[deployed],
        sender_balance=hex(10**18 - 300_000 * 10**9),
        sender_token_balance=hex(10**9),
        recipient_token_balance="0x0",
    )
    after.update(
        sender_balance=hex(int(before["sender_balance"], 16) - 51_482 * 10**9),
        recipient_balance="0x0",
        sender_token_balance=hex(10**9 - 12_345_678),
        recipient_token_balance=hex(12_345_678),
    )
    evidence["deployment"] = {
        "from": sender,
        "to": None,
        "type": "0x2",
        "status": "0x1",
        "gasUsed": hex(300_000),
        "effectiveGasPrice": hex(10**9),
        "transactionHash": deployed,
        "contractAddress": contract,
        "blockHash": before["hash"],
        "blockNumber": before["number"],
        "transactionIndex": "0x0",
    }
    for key, source, target, units in (
        ("deployment", "0x" + "0" * 40, sender, 10**9),
        ("receipt", sender, recipient, 12_345_678),
    ):
        event = {
            name: evidence[key][name] for name in ("blockHash", "blockNumber", "transactionHash", "transactionIndex")
        }
        evidence[key]["logs"] = [
            {
                **event,
                "removed": False,
                "logIndex": "0x0",
                "address": contract,
                "topics": [
                    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                    "0x" + source[2:].zfill(64),
                    "0x" + target[2:].zfill(64),
                ],
                "data": "0x" + units.to_bytes(32).hex(),
            }
        ]
    return evidence


def test_token_amount_and_eth_fees_are_recomputed_offline(token_evidence, tmp_path):
    script = tmp_path / "verify.py"
    shutil.copyfile(Path(lab.__file__), script)
    raw = json.dumps(token_evidence).encode()
    (tmp_path / "token.json").write_bytes(raw)
    result = json.loads(
        subprocess.check_output(
            [sys.executable, "-S", "verify.py", "verify", "token.json", "--expected-sha256", sha256(raw).hexdigest()],
            cwd=tmp_path,
        )
    )
    assert result["token_decimals"] == 6
    assert result["transfer_amount"] == "12.345678"
    assert result["transfer_atomic"] == "12345678"
    assert result["deployment_fee_wei"] == "300000000000000"
    assert result["fee_wei"] == "51482000000000"


@pytest.mark.parametrize(
    ("change", "error"),
    [
        (lambda data: data["token"].update(decimals=18), "decimals or runtime commitment"),
        (lambda data: data["token"].update(runtime_code="0x" + "ff" * 1171), "decimals or runtime commitment"),
        (lambda data: data["receipt"]["logs"][0].update(address="0x" + "f" * 40), "Transfer log does not match"),
        (lambda data: data["receipt"]["logs"][0].update(data="0x" + "0" * 64), "Transfer log does not match"),
        (lambda data: data["receipt"].update(logs=[]), "single ERC-20 Transfer log"),
        (
            lambda data: data["after"].update(recipient_token_balance="0x0"),
            "balances do not reconcile with the Transfer",
        ),
        (lambda data: data["after"].update(recipient_balance="0x1"), "ETH balances do not reconcile"),
        (lambda data: data["deployment"].update(status="0x0"), "successful token deployment"),
    ],
)
def test_unmatched_token_evidence_is_rejected(token_evidence, change, error):
    change(token_evidence)
    with pytest.raises(ValueError, match=error):
        lab.verify_evidence(token_evidence)
