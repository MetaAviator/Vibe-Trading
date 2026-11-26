"""Run a disposable, local-only ETH transfer and check its public evidence offline."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from hashlib import sha256
from http.client import HTTPConnection, HTTPException
import json
import os
from pathlib import Path
import re
from secrets import randbelow, token_urlsafe
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep


_PREFUND = 10**18
_TRANSFER = 10**16
_MAX_EVIDENCE = 64 * 1024
_CHAIN_MIN = 100_000
_CHAIN_MAX = 2**31


def _quantity(value: object) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"0x(?:0|[1-9a-f][0-9a-f]{0,63})", value):
        raise ValueError("expected a JSON-RPC hex quantity")
    return int(value, 16)


def _hex(value: object, size: int) -> str:
    if not isinstance(value, str) or not re.fullmatch(rf"0x[0-9a-f]{{{size * 2}}}", value):
        raise ValueError("invalid address or hash in the evidence")
    return value


def _object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def verify_evidence(evidence: dict) -> dict:
    if type(evidence.get("schema_version")) is not int or evidence["schema_version"] != 1:
        raise ValueError("unsupported local transfer evidence version")
    chain_id = evidence.get("chain_id")
    if type(chain_id) is not int or not _CHAIN_MIN <= chain_id < _CHAIN_MAX:
        raise ValueError("expected a disposable development chain ID")
    tx = _object(evidence.get("transaction"), "transaction")
    receipt = _object(evidence.get("receipt"), "receipt")
    before = _object(evidence.get("before"), "before")
    after = _object(evidence.get("after"), "after")
    sender, recipient = (_hex(tx.get(key), 20) for key in ("from", "to"))
    tx_hash = _hex(tx.get("hash"), 32)
    block_hash = _hex(after.get("hash"), 32)
    previous_hash = _hex(before.get("hash"), 32)
    block_number = _quantity(after.get("number"))
    if (
        sender == recipient
        or _quantity(tx.get("chainId")) != chain_id
        or tx.get("input") != "0x"
        or _quantity(tx.get("value")) != _TRANSFER
    ):
        raise ValueError("unexpected local ETH transfer parameters")
    if (
        after.get("transactions") != [tx_hash]
        or after.get("parentHash") != previous_hash
        or block_hash == previous_hash
        or block_number != _quantity(before.get("number")) + 1
    ):
        raise ValueError("expected one transfer in the block following the funded snapshot")
    for item in (tx, receipt):
        if (
            item.get("blockHash") != block_hash
            or _quantity(item.get("blockNumber")) != block_number
            or _quantity(item.get("transactionIndex")) != 0
            or _quantity(item.get("type")) != 2
            or (item.get("from"), item.get("to")) != (sender, recipient)
        ):
            raise ValueError("transaction and receipt must identify the same type-2 transfer")
    if receipt.get("transactionHash") != tx_hash or _quantity(receipt.get("status")) != 1 or receipt.get("logs") != []:
        raise ValueError("expected a successful native transfer receipt without contract logs")

    gas_used = _quantity(receipt.get("gasUsed"))
    fee = gas_used * _quantity(receipt.get("effectiveGasPrice"))
    sender_before = _quantity(before.get("sender_balance"))
    receiver_before = _quantity(before.get("recipient_balance"))
    if (
        gas_used != 21_000
        or fee <= 0
        or sender_before != _PREFUND
        or receiver_before != 0
        or sender_before - _quantity(after.get("sender_balance")) != _TRANSFER + fee
        or _quantity(after.get("recipient_balance")) - receiver_before != _TRANSFER
    ):
        raise ValueError("receipt gas and balance changes do not reconcile")
    return {
        "chain_id": chain_id,
        "transaction_hash": tx_hash,
        "sender": sender,
        "recipient": recipient,
        "block_number": block_number,
        "transfer_wei": str(_TRANSFER),
        "gas_used": gas_used,
        "fee_wei": str(fee),
    }


class LoopbackRPC:
    def __init__(self, port: int):
        self.port = port
        self.request_id = 0

    def call(self, method: str, *params):
        self.request_id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params})
        connection = HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request("POST", "/", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError(f"local RPC returned HTTP {response.status}")
            payload = response.read(_MAX_EVIDENCE + 1)
            if len(payload) > _MAX_EVIDENCE:
                raise ValueError("local RPC response is too large")
            reply = _object(json.loads(payload), "RPC response")
        finally:
            connection.close()
        if reply.get("id") != self.request_id or "error" in reply or "result" not in reply:
            raise ValueError(f"local RPC could not complete {method}")
        return reply["result"]


def _wait_for_node(rpc: LoopbackRPC, node: subprocess.Popen, chain_id: int) -> None:
    finish = monotonic() + 10
    while node.poll() is None and monotonic() < finish:
        try:
            reported = rpc.call("eth_chainId")
        except (OSError, HTTPException):
            sleep(0.05)
            continue
        if _quantity(reported) == chain_id and "anvil" in rpc.call("web3_clientVersion").lower():
            return
        raise ValueError("the loopback RPC is not the fresh Anvil chain")
    raise ValueError("the local Anvil process did not become ready")


def _snapshot(rpc: LoopbackRPC, tag: str, sender: str, recipient: str) -> dict:
    block = rpc.call("eth_getBlockByNumber", tag, False)
    mark = {"blockHash": block["hash"], "requireCanonical": True}
    return {
        **{key: block[key] for key in ("hash", "number", "parentHash", "transactions")},
        "sender_balance": rpc.call("eth_getBalance", sender, mark),
        "recipient_balance": rpc.call("eth_getBalance", recipient, mark),
    }


def _wait_for_receipt(rpc: LoopbackRPC, tx_hash: str) -> dict:
    finish = monotonic() + 5
    while monotonic() < finish:
        receipt = rpc.call("eth_getTransactionReceipt", tx_hash)
        if receipt is not None:
            return _object(receipt, "receipt")
        sleep(0.05)
    raise ValueError("the local test transaction is still missing its receipt")


def _run_once(rpc: LoopbackRPC, chain_id: int, directory: Path) -> dict:
    try:
        from eth_account import Account
    except ImportError:
        raise ValueError("install eth-account==0.14.0 in the lab's isolated Python environment") from None

    password = token_urlsafe(32)
    account = Account.create()
    sender, recipient = account.address.lower(), Account.create().address.lower()
    keystore = directory / "sender-keystore.json"
    with os.fdopen(os.open(keystore, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as file:
        json.dump(Account.encrypt(account.key, password, kdf="scrypt"), file)
    if os.name == "posix" and keystore.stat().st_mode & 0o077:
        raise ValueError("the disposable keystore must be accessible only by its owner")
    del account
    encrypted = keystore.read_text(encoding="utf-8")
    try:
        Account.decrypt(encrypted, token_urlsafe(32))
    except ValueError:
        pass
    else:
        raise ValueError("the encrypted keystore accepted an unrelated password")

    rpc.call("anvil_setBalance", sender, hex(_PREFUND))
    rpc.call("evm_mine")
    before = _snapshot(rpc, "latest", sender, recipient)
    fee_floor = _quantity(rpc.call("eth_getBlockByNumber", before["number"], False)["baseFeePerGas"])
    priority = 10**9
    unsigned = {
        "chainId": chain_id,
        "nonce": _quantity(rpc.call("eth_getTransactionCount", sender, "latest")),
        "to": bytes.fromhex(recipient[2:]),
        "value": _TRANSFER,
        "gas": 21_000,
        "type": 2,
        "maxPriorityFeePerGas": priority,
        "maxFeePerGas": 2 * fee_floor + priority,
    }
    key = Account.decrypt(encrypted, password)
    signed = Account.sign_transaction(unsigned, key)
    del password, key
    keystore.unlink()
    if Account.recover_transaction(signed.raw_transaction).lower() != sender:
        raise ValueError("the signed transaction did not recover the test sender")
    tx_hash = rpc.call("eth_sendRawTransaction", "0x" + bytes(signed.raw_transaction).hex())
    if tx_hash != "0x" + bytes(signed.hash).hex():
        raise ValueError("the local node returned an unexpected transaction hash")
    receipt = _wait_for_receipt(rpc, tx_hash)
    tx = _object(rpc.call("eth_getTransactionByHash", tx_hash), "transaction")
    after = _snapshot(rpc, receipt["blockNumber"], sender, recipient)
    # Keep the public transfer fields needed for replay, without signer material.
    tx_fields = (
        "chainId",
        "hash",
        "from",
        "to",
        "input",
        "value",
        "blockHash",
        "blockNumber",
        "transactionIndex",
        "type",
    )
    return {
        "schema_version": 1,
        "chain_id": chain_id,
        "transaction": {key: tx[key] for key in tx_fields},
        "receipt": receipt,
        "before": before,
        "after": after,
    }


def run_lab(anvil: str) -> dict:
    binary = shutil.which(anvil)
    if binary is None:
        raise ValueError("Anvil is required for the transfer lab")
    with TemporaryDirectory(prefix="journal-local-transfer-") as temp:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        chain_id = _CHAIN_MIN + randbelow(_CHAIN_MAX - _CHAIN_MIN)
        rpc = LoopbackRPC(port)
        node = subprocess.Popen(
            [
                str(Path(binary).resolve()),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--chain-id",
                str(chain_id),
                "--accounts",
                "0",
            ],
            cwd=temp,
            env={"PATH": os.defpath},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_for_node(rpc, node, chain_id)
            return _run_once(rpc, chain_id, Path(temp))
        finally:
            node.terminate()
            try:
                node.wait(timeout=5)
            except subprocess.TimeoutExpired:
                node.kill()
                node.wait()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    run = modes.add_parser("run", help="Start a fresh Anvil node and perform a test-only transfer")
    run.add_argument("--anvil", default="anvil", help="Local Anvil executable")
    run.add_argument("--output", type=Path, required=True)
    verify = modes.add_parser("verify", help="Check the recorded transfer without running a node")
    verify.add_argument("evidence", type=Path)
    verify.add_argument("--expected-sha256", required=True)
    options = parser.parse_args(argv)
    try:
        if options.mode == "run":
            from maintenance.journal.review_bundle import _publish_new_file

            if options.output.expanduser().exists():
                raise ValueError("use a new output path")
            evidence = run_lab(options.anvil)
            summary = verify_evidence(evidence)
            payload = json.dumps(evidence, sort_keys=True, indent=2).encode("utf-8") + b"\n"
            _publish_new_file(options.output.expanduser(), payload)
            digest = sha256(payload).hexdigest()
        else:
            with options.evidence.expanduser().open("rb") as file:
                payload = file.read(_MAX_EVIDENCE + 1)
            digest = sha256(payload).hexdigest()
            if len(payload) > _MAX_EVIDENCE or digest != options.expected_sha256.lower():
                raise ValueError("evidence is too large or its SHA256 does not match")
            summary = verify_evidence(_object(json.loads(payload), "evidence"))
    except (OSError, HTTPException, ValueError, RecursionError, KeyError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps({**summary, "evidence_sha256": digest}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
