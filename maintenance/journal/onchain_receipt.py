"""Build or independently verify a synthetic Ethereum swap evidence package."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from hashlib import sha256
import json
from pathlib import Path
import re
import sys


_INPUTS = ("context", "block", "transaction", "receipt")
_MAX_INPUT = 256 * 1024
_MAX_PACKAGE = 2 * 1024 * 1024
_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_SWAP = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"


def _unique_keys(pairs: list[tuple]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _object(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON number: {value}")


def _load(payload: bytes, name: str, limit: int) -> dict:
    if len(payload) > limit:
        raise ValueError(f"{name} exceeds its size limit")
    return _object(
        json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_keys, parse_constant=_reject_constant), name
    )


def _hex(value: object, size: int, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(rf"0x[0-9a-fA-F]{{{size * 2}}}", value):
        raise ValueError(f"{field} must contain {size} hex bytes")
    return value.lower()


def _quantity(value: object, field: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]{0,63})", value):
        raise ValueError(f"{field} must be a JSON-RPC quantity")
    return int(value, 16)


def _addresses(context: dict) -> tuple[str, str, str]:
    addresses = tuple(_hex(context.get(role), 20, role) for role in ("wallet", "router", "pool"))
    if len(set(addresses)) != 3 or "0x" + "0" * 40 in addresses:
        raise ValueError("wallet, router and pool must be distinct nonzero addresses")
    return addresses


def _tokens(value: object) -> list[dict]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("context requires two tokens in pool token0/token1 order")
    tokens = []
    for entry in value:
        token = _object(entry, "token")
        address = _hex(token.get("address"), 20, "token.address")
        symbol, decimals = token.get("symbol"), token.get("decimals")
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", symbol):
            raise ValueError("token.symbol must be a short label")
        if type(decimals) is not int or not 0 <= decimals <= 255:
            raise ValueError("token.decimals must be an integer between 0 and 255")
        tokens.append({"address": address, "symbol": symbol, "decimals": decimals})
    if not "0x" + "0" * 40 < tokens[0]["address"] < tokens[1]["address"]:
        raise ValueError("token0 and token1 must use sorted distinct nonzero addresses")
    return tokens


def _reference(entry: dict, hash_field: str = "transactionHash") -> dict:
    return {
        "transaction_hash": _hex(entry.get(hash_field), 32, hash_field),
        "block_hash": _hex(entry.get("blockHash"), 32, "blockHash"),
        "block_number": _quantity(entry.get("blockNumber"), "blockNumber"),
        "transaction_index": _quantity(entry.get("transactionIndex"), "transactionIndex"),
    }


def _indexed_address(value: object) -> str:
    word = _hex(value, 32, "address topic")
    if word[2:26] != "0" * 24:
        raise ValueError("address topic must be zero padded")
    return "0x" + word[26:]


def _units(amount: int, decimals: int) -> str:
    whole, fraction = divmod(amount, 10**decimals)
    return f"{whole}.{fraction:0{decimals}d}".rstrip("0") if fraction else str(whole)


def _swap_flows(logs: list, reference: dict, addresses: tuple[str, str, str], tokens: list[dict]) -> tuple[int, list]:
    wallet, router, pool = addresses
    if len(logs) != 3:
        raise ValueError("expected one Swap and two direct Transfer logs for a single hop")
    transfers = {}
    pool_delta = None
    swap_index = None
    last_index = -1
    for item in logs:
        log = _object(item, "log")
        index = _quantity(log.get("logIndex"), "logIndex")
        if index <= last_index:
            raise ValueError("log indices must be unique and increasing")
        last_index = index
        if log.get("removed") is not False or _reference(log) != reference:
            raise ValueError("log must belong to the same transaction and block and not be removed")
        address = _hex(log.get("address"), 20, "log.address")
        topics = log.get("topics")
        if not isinstance(topics, list) or len(topics) != 3:
            raise ValueError("expected an event signature and two indexed addresses")
        signature = _hex(topics[0], 32, "event topic")
        sender, receiver = map(_indexed_address, topics[1:])
        if signature == _SWAP and address == pool and (sender, receiver) == (router, wallet) and pool_delta is None:
            data = bytes.fromhex(_hex(log.get("data"), 160, "Swap data")[2:])
            pool_delta = (int.from_bytes(data[:32], signed=True), int.from_bytes(data[32:64], signed=True))
            swap_index = index
            if any(data[64:76]) or any(data[96:112]):
                raise ValueError("Swap price and liquidity must use their ABI widths")
            tick = int.from_bytes(data[128:], signed=True)
            if not -(2**23) <= tick < 2**23:
                raise ValueError("Swap tick must be an int24")
        elif signature == _TRANSFER and address not in transfers:
            transfers[address] = (sender, receiver, int(_hex(log.get("data"), 32, "Transfer data"), 16), index)
        else:
            raise ValueError("unsupported or duplicate event in the single-hop receipt")
    if pool_delta is None or pool_delta[0] * pool_delta[1] >= 0 or swap_index != last_index:
        raise ValueError("expected a final Swap with opposite nonzero pool deltas")

    flows = []
    for token, delta in zip(tokens, pool_delta, strict=True):
        transfer = transfers.get(token["address"])
        sides = (wallet, pool) if delta > 0 else (pool, wallet)
        if transfer is None or transfer[:3] != (*sides, abs(delta)):
            raise ValueError("wallet Transfer does not match the pool Swap amount and direction")
        flows.append(
            {
                **token,
                "direction": "out" if delta > 0 else "in",
                "atomic_units": str(abs(delta)),
                "amount": _units(abs(delta), token["decimals"]),
                "transfer_log_index": transfer[3],
            }
        )
    return swap_index, flows


def summarize(inputs: dict[str, bytes]) -> dict:
    if set(inputs) != set(_INPUTS):
        raise ValueError("context, block, transaction and receipt are required")
    context, block, transaction, receipt = (_load(inputs[name], name, _MAX_INPUT) for name in _INPUTS)
    if context.get("evidence_kind") != "synthetic" or _quantity(transaction.get("chainId"), "chainId") != 1:
        raise ValueError("expected synthetic evidence with Ethereum mainnet chain parameters")
    addresses = _addresses(context)
    wallet, router, pool = addresses
    tokens = _tokens(context.get("tokens"))
    reference = _reference(receipt)
    if (
        _reference(transaction, "hash") != reference
        or _hex(block.get("hash"), 32, "block.hash") != reference["block_hash"]
        or _quantity(block.get("number"), "block.number") != reference["block_number"]
    ):
        raise ValueError("transaction, receipt and block references differ")
    for entry in (transaction, receipt):
        if (
            _hex(entry.get("from"), 20, "from") != wallet
            or _hex(entry.get("to"), 20, "to") != router
            or _quantity(entry.get("type"), "type") != 2
        ):
            raise ValueError("expected a type-2 transaction from the selected wallet to the selected router")
    if _quantity(transaction.get("value"), "value") != 0:
        raise ValueError("native ETH transaction value is outside the ERC-20 prototype")

    gas_used = _quantity(receipt.get("gasUsed"), "gasUsed")
    gas_price = _quantity(receipt.get("effectiveGasPrice"), "effectiveGasPrice")
    if not gas_used:
        raise ValueError("a mined transaction requires positive gasUsed")
    state = _quantity(receipt.get("status"), "status")
    logs = receipt.get("logs")
    if not isinstance(logs, list) or state not in (0, 1) or (state == 0 and logs):
        raise ValueError("invalid receipt status or logs")
    swap_index, flows = _swap_flows(logs, reference, addresses, tokens) if state else (None, [])
    gas_wei = gas_used * gas_price
    return {
        "evidence_kind": "synthetic",
        "chain_id": 1,
        **reference,
        "block_timestamp": _quantity(block.get("timestamp"), "block.timestamp"),
        "wallet": wallet,
        "router": router,
        "pool": pool,
        "status": "swap" if state else "reverted",
        "swap_log_index": swap_index,
        "wallet_erc20_flows": flows,
        "gas": {
            "used": str(gas_used),
            "price_wei": str(gas_price),
            "fee_wei": str(gas_wei),
            "fee_eth": _units(gas_wei, 18),
        },
        "input_sha256": {name: sha256(inputs[name]).hexdigest() for name in _INPUTS},
    }


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def build_package(inputs: dict[str, bytes]) -> tuple[bytes, dict]:
    report = summarize(inputs)
    payload = _canonical(
        {
            "schema_version": 1,
            "inputs": {name: inputs[name].decode("utf-8") for name in _INPUTS},
            "report": report,
        }
    )
    if len(payload) > _MAX_PACKAGE:
        raise ValueError("encoded evidence exceeds the package size limit")
    return payload, {**report, "bundle_sha256": sha256(payload).hexdigest()}


def verify_package(payload: bytes, expected_sha256: str) -> dict:
    if len(payload) > _MAX_PACKAGE:
        raise ValueError("evidence package exceeds the size limit")
    digest = sha256(payload).hexdigest()
    if digest != expected_sha256.lower():
        raise ValueError("evidence SHA256 does not match the expected value")
    package = _load(payload, "evidence package", _MAX_PACKAGE)
    version = package.get("schema_version")
    if set(package) != {"schema_version", "inputs", "report"} or type(version) is not int or version != 1:
        raise ValueError("expected a version 1 evidence package")
    originals = _object(package["inputs"], "inputs")
    if not all(isinstance(raw, str) for raw in originals.values()):
        raise ValueError("evidence inputs must contain the original JSON text")
    report = summarize({name: raw.encode("utf-8") for name, raw in originals.items()})
    if _canonical(package["report"]) != _canonical(report):
        raise ValueError("stored report does not match the independently recomputed results")
    return {**report, "bundle_sha256": digest}


def _read(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        payload = handle.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("input file exceeds the size limit")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    build = modes.add_parser("build", help="Stage the four original JSON inputs and the recomputed report")
    build.add_argument("input_directory", type=Path)
    build.add_argument("--output", type=Path, required=True)
    verify = modes.add_parser("verify", help="Verify a package and recompute its results offline")
    verify.add_argument("package", type=Path)
    verify.add_argument("--expected-sha256", required=True)
    options = parser.parse_args(argv)
    try:
        if options.mode == "build":
            from .review_bundle import _publish_new_file

            folder = options.input_directory.expanduser()
            payload, report = build_package({name: _read(folder / f"{name}.json", _MAX_INPUT) for name in _INPUTS})
            _publish_new_file(options.output.expanduser(), payload)
        else:
            report = verify_package(_read(options.package.expanduser(), _MAX_PACKAGE), options.expected_sha256)
    except (OSError, ValueError, RecursionError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
