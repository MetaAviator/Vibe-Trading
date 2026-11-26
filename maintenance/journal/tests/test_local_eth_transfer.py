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
