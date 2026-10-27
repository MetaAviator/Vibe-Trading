"""Numeric cells shared by trade-journal format adapters."""

from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd

# Broker CSV/Excel cells often include ISO codes or currency glyphs around the
# number (Schwab/IBKR "$1,234.56", JP/CN "¥1000"). Commas are already stripped;
# without stripping these tokens float() fails and we silently store 0.0.
_CURRENCY_TOKEN_RE = re.compile(
    r"(?i)(?<![A-Za-z])(?:USDT|USDC|USD|EUR|GBP|JPY|CNY|HKD)(?![A-Za-z])|[$€£¥￥]"
)


def _to_float(val: Any, default: float = 0.0) -> float:
    """Safely cast to float; return default on failure."""
    if val is None:
        return default
    try:
        s = str(val).strip().replace("\u2212", "-")
        s = _CURRENCY_TOKEN_RE.sub("", s)
        if "," in s and "." in s and s.rfind(",") > s.rfind("."):
            # "1.234,56": the rightmost separator is the decimal one, so dots
            # group thousands. Stripping only commas read it as 1.23456.
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
        s = s.strip()
        if not s:
            return default
        # Accounting exports write a negative as "(1,234.50)" (optionally
        # "$(1,234.50)"); without this float() fails and the cell becomes 0.0.
        negate = s.startswith("(") and s.endswith(")")
        if negate:
            s = s[1:-1].strip()
            if not s or s[0] in "+-":
                return default
        parsed = float(s)
        if negate:
            parsed = -parsed
        return parsed if math.isfinite(parsed) else default
    except (ValueError, TypeError):
        return default


def _trade_fee(raw: Any, side: str, require_readable_fees: bool) -> float:
    """Validate readable buy/sell fees while retaining legacy missing values."""
    if not require_readable_fees or side not in {"buy", "sell"}:
        return _to_float(raw)
    if raw is None or pd.isna(raw) or not str(raw).strip():
        return 0.0
    fee = _to_float(raw, default=math.nan)
    if not math.isfinite(fee):
        raise ValueError(
            "Trade journal contains invalid buy/sell fees. "
            "Correct non-missing fee values to finite numbers before analysis."
        )
    return fee


def _trade_price(raw: Any, side: str, require_trade_prices: bool) -> float:
    """Keep legacy fallback unless a caller requires readable fill prices."""
    if not require_trade_prices or side == "dividend":
        return _to_float(raw)
    price = _to_float(raw, default=math.nan)
    if not math.isfinite(price):
        raise ValueError(
            "Trade journal contains missing or invalid buy/sell prices. "
            "Correct price values to finite numbers before analysis."
        )
    return price
