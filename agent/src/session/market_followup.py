"""Recover a dated close from the immediately preceding successful turn."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Sequence

from src.agent.grounding.identity import _infer_currency, _scan_symbols
from src.agent.trace import TraceWriter
from src.session.models import Attempt, AttemptStatus, Message

if TYPE_CHECKING:
    from src.session.store import SessionStore


_REUSE = re.compile(
    r"^(?:复用\s*(?:上一轮|上轮)\s*|reuse (?:the )?previous turn(?:'s)?\s+)`?get_market_data\b",
    re.I,
)
_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_DISPLAY_DATE = re.compile(r"(?<!\d)(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|(?:\d{4}年)?\d{1,2}月\d{1,2}日)(?!\d)")
_LIVE = re.compile(r"实时|今日|今天|当前|现在|最新|现价|\b(?:latest|live|current|today|now)\b", re.I)
_TOOL = "get_market_data"


def is_market_followup(prompt: str) -> bool:
    """Recognize the explicit replay prefix even if the evidence is unavailable."""
    return _REUSE.match(prompt.strip()) is not None


@dataclass(frozen=True)
class PriorMarketEvidence:
    session_id: str
    attempt_id: str
    call_id: str
    symbol: str
    day: str
    close: Decimal
    source: str
    adjustment: str
    currency: str

    def matches_request(self, prompt: str, session_id: str) -> bool:
        return self.session_id == session_id and _requested_close(prompt) == (self.symbol, self.day)

    def report(self, prompt: str) -> str:
        chinese = re.search(r"[㐀-鿿]", prompt) is not None
        header = "| 项目 | 值 |" if chinese else "| Field | Value |"
        labels = (
            ("日期", f"收盘价 ({self.currency})", "实际数据源", "复权口径", "原工具调用 ID")
            if chinese
            else ("Date", f"Close ({self.currency})", "Source", "Adjustment", "Original tool call ID")
        )
        price = format(self.close, "f")
        values = (self.day, price, self.source, self.adjustment, f"`{self.call_id}`")
        rows = (f"| {label} | {value} |" for label, value in zip(labels, values))
        return "\n".join((f"# {self.symbol}", "", header, "|---|---|", *rows))


def missing_market_followup_message(prompt: str) -> str:
    if re.search(r"[㐀-鿿]", prompt):
        return "上一轮没有可核验的匹配日线收盘价及原始调用证据。"
    return "The previous turn has no verifiable daily close and original tool reference for this request."


def _requested_close(prompt: str) -> tuple[str, str] | None:
    if not is_market_followup(prompt) or _LIVE.search(prompt):
        return None
    symbols = _scan_symbols(prompt)
    dates = set(_DATE.findall(prompt))
    if len(symbols) != 1 or len(dates) != 1 or set(_DISPLAY_DATE.findall(prompt)) != dates:
        return None
    symbol, = symbols
    day, = dates
    try:
        date.fromisoformat(day)
    except ValueError:
        return None
    return symbol, day


def load_prior_market_evidence(
    store: SessionStore, attempt: Attempt, messages: Sequence[Message],
) -> PriorMarketEvidence | None:
    """Restore one explicitly requested daily close from trusted execution records."""
    request = _requested_close(attempt.prompt)
    if not attempt.parent_attempt_id or request is None:
        return None
    symbol, day = request

    try:
        parent = store.get_attempt(attempt.session_id, attempt.parent_attempt_id)
    except (OSError, ValueError, TypeError):
        return None
    if (
        not parent
        or parent.attempt_id != attempt.parent_attempt_id
        or parent.session_id != attempt.session_id
        or parent.status != AttemptStatus.COMPLETED
        or not parent.run_dir
        or type(parent.started_at) not in (int, float)
        or not parent.completed_at
    ):
        return None
    try:
        completed = datetime.fromisoformat(parent.completed_at)
        if completed.tzinfo is None:
            return None
        ended = completed.timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return None
    replies = [
        message for message in messages
        if message.session_id == attempt.session_id and message.role == "assistant"
        and message.linked_attempt_id == parent.attempt_id
    ]
    if len(replies) != 1 or not isinstance(replies[0].tool_trail, list):
        return None
    call_ids = {
        entry["call_id"]
        for entry in replies[0].tool_trail
        if isinstance(entry, dict)
        if entry.get("tool") == _TOOL and entry.get("status") == "ok"
        and isinstance(entry.get("call_id"), str)
        and re.fullmatch(r"[\w-]{1,100}", entry["call_id"], re.ASCII)
    }
    if not call_ids:
        return None

    trace_dir = store.base_dir / attempt.session_id
    try:
        entries = [entry for entry in TraceWriter.read(trace_dir)
                   if isinstance(entry, dict) and entry.get("tool") == _TOOL
                   and isinstance(entry.get("call_id"), str) and entry["call_id"] in call_ids
                   and isinstance(entry.get("ts"), (int, float)) and parent.started_at <= entry["ts"] <= ended]
    except (OSError, UnicodeError):
        return None

    traces: dict[str, list[dict]] = {}
    for entry in entries:
        if entry.get("type") in {"tool_call", "tool_result"}:
            traces.setdefault(entry["call_id"], []).append(entry)
    matches = []
    for records in traces.values():
        if len(records) != 2:
            continue
        call, result = records
        args = call.get("args")
        if (
            call.get("type") != "tool_call"
            or not isinstance(args, dict)
            or args.get("run_dir") != parent.run_dir
            or args.get("interval") != "1D"
            or not isinstance(args.get("codes"), list)
            or symbol not in args["codes"]
            or type(call.get("iter")) is not int
            or result.get("type") != "tool_result"
            or result.get("iter") != call["iter"]
            or result.get("status") != "ok"
        ):
            continue
        TraceWriter._resolve_entry_offloads(trace_dir, result, {"result"})
        quote = _dated_close(result.get("result"), symbol, day)
        if quote is not None:
            matches.append(PriorMarketEvidence(
                attempt.session_id, parent.attempt_id, call["call_id"], symbol, day, *quote,
            ))
    return matches[0] if len(matches) == 1 else None


def _dated_close(raw: object, symbol: str, day: str) -> tuple[Decimal, str, str, str] | None:
    if not isinstance(raw, str):
        return None
    try:
        payload = json.loads(raw, parse_float=Decimal)
        rows = payload[symbol]
        provenance = payload["_provenance"][symbol]
        if not isinstance(rows, list) or not isinstance(provenance, dict):
            return None
        matching = [row for row in rows if datetime.fromisoformat(row["trade_date"]).date().isoformat() == day]
        row, = matching
        close = row["close"]
        if type(close) not in (int, Decimal) or not math.isfinite(close):
            return None
        close = Decimal(close)
        if abs(close.as_tuple().exponent) > 1000:
            return None
        source, adjustment = provenance["source"], provenance["adjustment"]
        currency = provenance.get("quote_currency")
        # Older market snapshots may omit quote_currency. Apply the same
        # canonical currency rule as ingestion when no conversion is declared.
        if currency is None and provenance.get("currency_conversion", "none") == "none":
            currency = _infer_currency(symbol)
        if not all(
            isinstance(value, str) and re.fullmatch(r"[\w. /-]{1,80}", value, re.ASCII) and value.strip()
            for value in (source, adjustment)
        ) or not isinstance(currency, str) or re.fullmatch(r"[A-Z]{3}", currency) is None:
            return None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return close, source, adjustment, currency
