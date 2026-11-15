"""Verify source listing symbols in single-profile key/value reports."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from src.agent.grounding.figures import _fenced_blocks, table_rows
from src.agent.grounding.registry import grounding_check

if TYPE_CHECKING:
    from src.agent.grounding.ledger import GroundingLedger

LISTING_SYMBOL = r"[\^A-Za-z0-9][A-Za-z0-9.=_^-]*"
_LISTING_CELL = re.compile(rf"(?P<markup>\x60|\*\*|__|)(?P<symbol>{LISTING_SYMBOL})(?P=markup)")
_LISTING_LABELS = {"挂牌代码", "上市代码", "listing symbol", "listing ticker"}


@grounding_check(
    name="profile-listing-symbol", code="listing_symbol_mismatch", phase="answer", order=1,
    description="A single company's listing-symbol cell contradicts the profile tool's listing.symbol.",
)
def _profile_listing_symbol(ledger: GroundingLedger, content: str) -> list[dict[str, Any]]:
    profiles = ledger._profile_listings
    if len(profiles) != 1:
        return []
    ticker, listings = next(iter(profiles.items()))
    if len(listings) != 1:
        return []
    symbol, call_id = next(iter(listings.items()))
    fences = _fenced_blocks(content)
    issues = []
    for row in table_rows(content):
        if len(row.cells) != 2 or row.cells[0][0].strip("*_` ").casefold() not in _LISTING_LABELS:
            continue
        cell, start, _ = row.cells[1]
        match = _LISTING_CELL.fullmatch(cell)
        if match is None or any(left <= start < right for left, right, _, _ in fences):
            continue
        claim = match["symbol"]
        if claim == symbol:
            continue
        issues.append({
            "code": "listing_symbol_mismatch",
            "claim": claim,
            "span": [start + match.start("symbol"), start + match.end("symbol")],
            "expected": symbol,
            "source_tool_call_ids": [call_id],
            "repairable": claim.casefold() in {ticker, symbol.casefold()},
            "message": f"Write {symbol} in the listing-symbol cell, exactly as {call_id} returned it in data.listing.symbol.",
        })
    return issues
