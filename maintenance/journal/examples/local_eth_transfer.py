"""Run a disposable, local-only ETH or ERC-20 transfer and replay its evidence."""

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
_TOKEN_SUPPLY = 1_000_000_000
_TOKEN_TRANSFER = 12_345_678
_TOKEN_DECIMALS = 6
_TOKEN_RUNTIME_BYTES = 1171
_TOKEN_CREATION_SHA256 = "a38e8e1f1771277b93f29c6282b1efef03a8001e195620c8ae89bf09bcd22c91"
_TOKEN_RUNTIME_SHA256 = "266aad2c8464797d26f6c9657ceaf40bddfa7cfcf9ba9324c5c2e9e1fa54afd2"
_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_ZERO_ADDRESS = "0x" + "0" * 40


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


def _address_word(address: str) -> str:
    return address[2:].zfill(64)


def _uint_word(value: int) -> str:
    return f"{value:064x}"


def _transfer_input(recipient: str) -> str:
    return "0xa9059cbb" + _address_word(recipient) + _uint_word(_TOKEN_TRANSFER)


def _receipt_fee(receipt: dict) -> tuple[int, int]:
    used = _quantity(receipt.get("gasUsed"))
    return used, used * _quantity(receipt.get("effectiveGasPrice"))


def _transfer_log(receipt: dict, contract: str, sender: str, recipient: str, amount: int) -> int:
    logs = receipt.get("logs")
    if not isinstance(logs, list) or len(logs) != 1:
        raise ValueError("expected a single ERC-20 Transfer log")
    log = _object(logs[0], "Transfer log")
    if (
        log.get("address") != contract
        or log.get("topics") != [_TRANSFER_TOPIC, "0x" + _address_word(sender), "0x" + _address_word(recipient)]
        or log.get("data") != "0x" + _uint_word(amount)
        or log.get("removed") is not False
        or any(
            log.get(key) != receipt.get(key)
            for key in ("transactionHash", "blockHash", "blockNumber", "transactionIndex")
        )
    ):
        raise ValueError("ERC-20 Transfer log does not match the transaction, wallet addresses and amount")
    return _quantity(log.get("logIndex"))


def _verify_token_transfer(evidence: dict, fee: int) -> dict:
    receipt, before, after, token = (evidence[key] for key in ("receipt", "before", "after", "token"))
    sender, recipient, contract = evidence["transaction"]["from"], token["recipient"], token["address"]
    code = token.get("runtime_code")
    if (
        type(token.get("decimals")) is not int
        or token["decimals"] != _TOKEN_DECIMALS
        or not isinstance(code, str)
        or not re.fullmatch(rf"0x[0-9a-f]{{{_TOKEN_RUNTIME_BYTES * 2}}}", code)
        or sha256(bytes.fromhex(code[2:])).hexdigest() != _TOKEN_RUNTIME_SHA256
    ):
        raise ValueError("ERC-20 decimals or runtime commitment does not match the lab fixture")
    deployment = _object(evidence.get("deployment"), "deployment")
    deployment_hash = _hex(deployment.get("transactionHash"), 32)
    _, deployment_fee = _receipt_fee(deployment)
    if (
        deployment.get("from") != sender
        or deployment.get("to") is not None
        or deployment.get("contractAddress") != contract
        or deployment.get("blockHash") != before["hash"]
        or deployment.get("blockNumber") != before["number"]
        or before.get("transactions") != [deployment_hash]
        or _quantity(deployment.get("status")) != 1
        or _quantity(deployment.get("type")) != 2
        or _quantity(deployment.get("transactionIndex")) != 0
        or deployment_fee <= 0
    ):
        raise ValueError("expected successful token deployment in the preceding block")
    _transfer_log(deployment, contract, _ZERO_ADDRESS, sender, _TOKEN_SUPPLY)
    index = _transfer_log(receipt, contract, sender, recipient, _TOKEN_TRANSFER)
    sender_before = _quantity(before.get("sender_balance"))
    if (
        sender_before != _PREFUND - deployment_fee
        or sender_before - _quantity(after.get("sender_balance")) != fee
        or _quantity(before.get("recipient_balance")) != 0
        or _quantity(after.get("recipient_balance")) != 0
    ):
        raise ValueError("ERC-20 transfer ETH balances do not reconcile with the two fees")
    if (
        _quantity(before.get("sender_token_balance")) != _TOKEN_SUPPLY
        or _quantity(after.get("sender_token_balance")) != _TOKEN_SUPPLY - _TOKEN_TRANSFER
        or _quantity(before.get("recipient_token_balance")) != 0
        or _quantity(after.get("recipient_token_balance")) != _TOKEN_TRANSFER
    ):
        raise ValueError("ERC-20 balances do not reconcile with the Transfer log")
    whole, fraction = divmod(_TOKEN_TRANSFER, 10**_TOKEN_DECIMALS)
    return {
        "asset": "erc20",
        "symbol": "TEST6",
        "token_address": contract,
        "token_decimals": _TOKEN_DECIMALS,
        "transfer_atomic": str(_TOKEN_TRANSFER),
        "transfer_amount": f"{whole}.{fraction:0{_TOKEN_DECIMALS}d}",
        "transfer_log_index": index,
        "deployment_hash": deployment_hash,
        "deployment_fee_wei": str(deployment_fee),
    }


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
    sender, destination = (_hex(tx.get(key), 20) for key in ("from", "to"))
    asset = evidence.get("asset", "eth")
    if asset == "erc20":
        token = _object(evidence.get("token"), "token")
        recipient = _hex(token.get("recipient"), 20)
        if (
            len({sender, destination, recipient}) != 3
            or _ZERO_ADDRESS in (sender, destination, recipient)
            or token.get("address") != destination
            or tx.get("input") != _transfer_input(recipient)
            or _quantity(tx.get("value")) != 0
        ):
            raise ValueError("unexpected local ERC-20 transfer parameters")
    elif asset == "eth":
        recipient = destination
        if sender == recipient or tx.get("input") != "0x" or _quantity(tx.get("value")) != _TRANSFER:
            raise ValueError("unexpected local ETH transfer parameters")
    else:
        raise ValueError("unsupported local transfer asset")
    tx_hash = _hex(tx.get("hash"), 32)
    block_hash = _hex(after.get("hash"), 32)
    previous_hash = _hex(before.get("hash"), 32)
    block_number = _quantity(after.get("number"))
    if _quantity(tx.get("chainId")) != chain_id:
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
            or (item.get("from"), item.get("to")) != (sender, destination)
        ):
            raise ValueError("transaction and receipt must identify the same type-2 transfer")
    if receipt.get("transactionHash") != tx_hash or _quantity(receipt.get("status")) != 1:
        raise ValueError("expected a successful native transfer or ERC-20 transfer receipt")

    gas_used, fee = _receipt_fee(receipt)
    if asset == "erc20":
        if fee <= 0:
            raise ValueError("expected positive ERC-20 transfer gas")
        details = _verify_token_transfer(evidence, fee)
    else:
        details = _verify_native_transfer(receipt, before, after, gas_used, fee)
    return {
        "chain_id": chain_id,
        "transaction_hash": tx_hash,
        "sender": sender,
        "recipient": recipient,
        "block_number": block_number,
        **details,
        "gas_used": gas_used,
        "fee_wei": str(fee),
    }


def _verify_native_transfer(receipt: dict, before: dict, after: dict, gas_used: int, fee: int) -> dict:
    if receipt.get("logs") != []:
        raise ValueError("expected a successful native transfer receipt without contract logs")
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
    return {"transfer_wei": str(_TRANSFER)}


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


def _snapshot(rpc: LoopbackRPC, tag: str, sender: str, recipient: str, token: str | None = None) -> dict:
    block = rpc.call("eth_getBlockByNumber", tag, False)
    mark = {"blockHash": block["hash"], "requireCanonical": True}
    snapshot = {
        **{key: block[key] for key in ("hash", "number", "parentHash", "transactions")},
        "sender_balance": rpc.call("eth_getBalance", sender, mark),
        "recipient_balance": rpc.call("eth_getBalance", recipient, mark),
    }
    if token is not None:
        for role, address in (("sender", sender), ("recipient", recipient)):
            value = rpc.call("eth_call", {"to": token, "data": "0x70a08231" + _address_word(address)}, mark)
            snapshot[f"{role}_token_balance"] = hex(int(_hex(value, 32), 16))
    return snapshot


def _wait_for_receipt(rpc: LoopbackRPC, tx_hash: str) -> dict:
    finish = monotonic() + 5
    while monotonic() < finish:
        receipt = rpc.call("eth_getTransactionReceipt", tx_hash)
        if receipt is not None:
            return _object(receipt, "receipt")
        sleep(0.05)
    raise ValueError("the local test transaction is still missing its receipt")


def _send_signed(rpc: LoopbackRPC, signed) -> dict:
    tx_hash = rpc.call("eth_sendRawTransaction", "0x" + bytes(signed.raw_transaction).hex())
    if tx_hash != "0x" + bytes(signed.hash).hex():
        raise ValueError("the local node returned an unexpected transaction hash")
    return _wait_for_receipt(rpc, tx_hash)


def _run_once(rpc: LoopbackRPC, chain_id: int, directory: Path, asset: str) -> dict:
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

    def sign(passphrase: str, destination: str | None, value: int, gas: int, data: bytes = b""):
        fee_floor = _quantity(rpc.call("eth_getBlockByNumber", "latest", False)["baseFeePerGas"])
        priority = 10**9
        unsigned = {
            "chainId": chain_id,
            "nonce": _quantity(rpc.call("eth_getTransactionCount", sender, "latest")),
            "value": value,
            "gas": gas,
            "type": 2,
            "data": data,
            "maxPriorityFeePerGas": priority,
            "maxFeePerGas": 2 * fee_floor + priority,
        }
        if destination is not None:
            unsigned["to"] = bytes.fromhex(destination[2:])
        signed = Account.sign_transaction(unsigned, Account.decrypt(encrypted, passphrase))
        if Account.recover_transaction(signed.raw_transaction).lower() != sender:
            raise ValueError("the signed transaction did not recover the test sender")
        return signed

    contract = None
    extra = {}
    if asset == "erc20":
        creation_path = Path(__file__).with_name("local_wallet") / "LabToken.hex"
        creation = bytes.fromhex(creation_path.read_text(encoding="ascii"))
        if sha256(creation).hexdigest() != _TOKEN_CREATION_SHA256:
            raise ValueError("unexpected local test token creation bytecode")
        deployment = _send_signed(rpc, sign(password, None, 0, 500_000, creation))
        if _quantity(deployment.get("status")) != 1:
            raise ValueError("the local token fixture deployment failed")
        contract = _hex(deployment.get("contractAddress"), 20)
        mark = {"blockHash": deployment["blockHash"], "requireCanonical": True}
        code = rpc.call("eth_getCode", contract, mark)
        decimals = int(_hex(rpc.call("eth_call", {"to": contract, "data": "0x313ce567"}, mark), 32), 16)
        extra = {
            "asset": "erc20",
            "deployment": deployment,
            "token": {"address": contract, "recipient": recipient, "decimals": decimals, "runtime_code": code},
        }

    before = _snapshot(rpc, "latest", sender, recipient, contract)
    if contract is None:
        signed = sign(password, recipient, _TRANSFER, 21_000)
    else:
        signed = sign(password, contract, 0, 100_000, bytes.fromhex(_transfer_input(recipient)[2:]))
    del password
    keystore.unlink()
    receipt = _send_signed(rpc, signed)
    tx = _object(rpc.call("eth_getTransactionByHash", receipt["transactionHash"]), "transaction")
    after = _snapshot(rpc, receipt["blockNumber"], sender, recipient, contract)
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
        **extra,
    }


def run_lab(anvil: str, *, asset: str = "eth") -> dict:
    if asset not in {"eth", "erc20"}:
        raise ValueError("unsupported local transfer asset")
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
            return _run_once(rpc, chain_id, Path(temp), asset)
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
    run.add_argument("--asset", choices=("eth", "erc20"), default="eth")
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
            evidence = run_lab(options.anvil, asset=options.asset)
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
