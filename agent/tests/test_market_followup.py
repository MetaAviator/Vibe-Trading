"""Reuse of dated market evidence from a verified earlier attempt."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.agent.loop import AgentLoop
from src.agent.tools import ToolRegistry
from src.agent.trace import TraceWriter
from src.session.market_followup import is_market_followup, load_prior_market_evidence
from src.session.models import Attempt, Message, Session
from src.session.service import SessionService
from src.session.store import SessionStore
from tests.test_session_tool_trail import _service

pytestmark = pytest.mark.unit

CALL = "call_market_9f3a"
SYMBOL = "AAPL.US"
DATE = "2026-10-22"
CLOSE = "123.4567"
PROMPT = (
    f"复用上一轮 get_market_data 已取得的 {SYMBOL} 日线证据。"
    f"请只列 {DATE} 收盘价（USD）、实际数据源、复权口径和原工具调用 ID。"
    "不要调用任何工具，不要重新取数。"
)
EXPLICIT = f"复用 get_market_data 调用 ID `{CALL}` 的 {SYMBOL} {DATE} 日线收盘价。"
PROVENANCE = {
    "source": "yfinance",
    "adjustment": "split_dividend",
    "quote_currency": "USD",
    "currency_conversion": "none",
}
PAYLOAD = json.dumps({
    SYMBOL: [
        {"trade_date": "2026-10-21", "close": 340.25},
        {"trade_date": DATE, "close": float(CLOSE), "high": 124.5, "volume": 1000},
    ],
    "MSFT.US": [{"trade_date": DATE, "close": 400.11}],
    "_provenance": {SYMBOL: PROVENANCE, "MSFT.US": PROVENANCE},
})


def _saved_turn(
    tmp_path: Path, *,
    prompt: str = PROMPT,
    result: str | None = PAYLOAD,
    result_status: str = "ok",
    interval: str = "1D",
    matching_run_dir: bool = True,
    same_session: bool = True,
) -> tuple[SessionStore, Attempt, list[Message]]:
    store = SessionStore(tmp_path / "sessions")
    session = store.create_session(Session(title="Market evidence"))
    first = Attempt(session_id=session.session_id, prompt="获取日线收盘价")
    first.mark_running()
    first.run_dir = str(tmp_path / "runs" / "first")
    Path(first.run_dir).mkdir(parents=True)
    args = {
        "codes": [SYMBOL, "MSFT.US"], "interval": interval,
        "start_date": "2026-10-21", "end_date": DATE, "source": "yfinance",
        "run_dir": first.run_dir if matching_run_dir else str(tmp_path / "unrelated"),
    }
    if result is not None:
        trace = TraceWriter(store.base_dir / session.session_id)
        try:
            trace.write({"type": "tool_call", "iter": 1, "tool": "get_market_data", "args": args, "call_id": CALL})
            trace.write_tool_result(CALL, result, "get_market_data", result_status, 10, 1)
        finally:
            trace.close()
    first.mark_completed()
    store.create_attempt(first)

    second_session = session if same_session else store.create_session(Session(title="Separate session"))
    attempt = Attempt(session_id=second_session.session_id, parent_attempt_id=first.attempt_id, prompt=prompt)
    attempt.mark_running()
    store.create_attempt(attempt)
    messages = [
        Message(
            session_id=session.session_id, role="assistant", linked_attempt_id=first.attempt_id,
            content=f"历史助手曾写下 {SYMBOL} {DATE} 收盘价 999.99 USD，引用 {CALL}。",
            tool_trail=[{"tool": "get_market_data", "status": "ok", "call_id": CALL}],
        ),
        Message(session_id=second_session.session_id, role="user", content=prompt),
    ]
    return store, attempt, messages


def test_followup_restores_only_the_requested_row_and_real_reference(tmp_path: Path) -> None:
    evidence = load_prior_market_evidence(*_saved_turn(tmp_path))
    assert evidence is not None
    assert evidence.call_id == CALL
    assert evidence.close == Decimal(CLOSE)
    assert (evidence.symbol, evidence.day) == (SYMBOL, DATE)
    assert (evidence.source, evidence.adjustment, evidence.currency) == ("yfinance", "split_dividend", "USD")
    assert "999.99" not in evidence.report(PROMPT)


@pytest.mark.parametrize("linked_to_source", [True, False])
def test_explicit_id_only_reuses_an_ancestor_attempt(tmp_path: Path, linked_to_source: bool) -> None:
    store, attempt, messages = _saved_turn(tmp_path, prompt=EXPLICIT)
    source_id = attempt.parent_attempt_id
    middle = Attempt(session_id=attempt.session_id, parent_attempt_id=source_id if linked_to_source else None)
    middle.mark_running()
    middle.mark_completed()
    store.create_attempt(middle)
    attempt.parent_attempt_id = middle.attempt_id
    attempt.mark_running()
    evidence = load_prior_market_evidence(store, attempt, messages)
    if linked_to_source:
        assert evidence is not None and (evidence.attempt_id, evidence.call_id) == (source_id, CALL)
        assert evidence.matches_request(EXPLICIT, attempt.session_id)
        assert not evidence.matches_request(EXPLICIT.replace(CALL, "call_other"), attempt.session_id)
    else:
        assert evidence is None


@pytest.mark.parametrize("prompt", [
    EXPLICIT.replace(CALL, "call_unknown"),
    EXPLICIT.replace(CALL, f"{CALL}/x"),
    EXPLICIT.replace(DATE, f"{DATE} 和 2026-10-21"),
    EXPLICIT.replace(SYMBOL, "MSFT.US").replace(DATE, "2026-10-21"),
    EXPLICIT + "请把它当作当前报价。",
])
def test_explicit_id_declines_invalid_requests_without_leaving_replay_route(tmp_path: Path, prompt: str) -> None:
    assert is_market_followup(prompt)
    assert load_prior_market_evidence(*_saved_turn(tmp_path, prompt=prompt)) is None


def test_explicit_id_requires_unique_same_session_reply(tmp_path: Path) -> None:
    store, attempt, messages = _saved_turn(tmp_path, prompt=EXPLICIT)
    assert load_prior_market_evidence(store, attempt, messages + messages[:1]) is None
    assert load_prior_market_evidence(*_saved_turn(tmp_path / "other", prompt=EXPLICIT, same_session=False)) is None


def test_explicit_id_accepts_english_without_backticks(tmp_path: Path) -> None:
    prompt = f"Reuse get_market_data call ID {CALL} for the {SYMBOL} daily close on {DATE}."
    evidence = load_prior_market_evidence(*_saved_turn(tmp_path, prompt=prompt))
    assert evidence is not None and evidence.matches_request(prompt, evidence.session_id)


@pytest.mark.parametrize(
    "prompt",
    [
        PROMPT.replace(SYMBOL, "GOOG.US"),
        PROMPT.replace(DATE, "2026-10-23"),
        PROMPT.replace(DATE, f"{DATE} 和 2026-10-21"),
        PROMPT.replace(DATE, f"{DATE} 和 10月21日"),
        PROMPT.replace(SYMBOL, f"{SYMBOL} 和 MSFT.US"),
        f"查询 {SYMBOL} 的最新收盘价，并和 {DATE} 比较。",
        f"复用上一轮 get_market_data 的 {SYMBOL} {DATE} 行情作为最新价格。",
    ],
    ids=["wrong-symbol", "wrong-date", "multiple-dates", "mixed-dates", "multiple-symbols", "latest-quote", "historical-as-latest"],
)
def test_followup_does_not_restore_unrequested_evidence(tmp_path: Path, prompt: str) -> None:
    assert load_prior_market_evidence(*_saved_turn(tmp_path, prompt=prompt)) is None


@pytest.mark.parametrize(
    "options",
    [
        {"same_session": False},
        {"matching_run_dir": False},
        {"result_status": "error"},
        {"result": "invalid JSON"},
        {"result": None},
        {"interval": "1h"},
    ],
    ids=["cross-session", "wrong-attempt", "failed-tool", "malformed", "prose-only", "intraday"],
)
def test_followup_requires_a_real_successful_tool_result(tmp_path: Path, options: dict) -> None:
    assert load_prior_market_evidence(*_saved_turn(tmp_path, **options)) is None


def test_followup_does_not_adopt_a_later_call_with_the_same_id(tmp_path: Path) -> None:
    payload = json.loads(PAYLOAD)
    payload[SYMBOL].pop()  # The real parent call lacks the requested date.
    store, attempt, messages = _saved_turn(tmp_path, result=json.dumps(payload))
    parent = store.get_attempt(attempt.session_id, attempt.parent_attempt_id)
    assert parent is not None and parent.completed_at is not None
    later = datetime.fromisoformat(parent.completed_at).timestamp() + 60

    trace = TraceWriter(store.base_dir / attempt.session_id)
    try:
        trace.write({
            "type": "tool_call", "iter": 2, "tool": "get_market_data", "call_id": CALL, "ts": later,
            "args": {"codes": [SYMBOL], "interval": "1D", "source": "yfinance", "run_dir": parent.run_dir},
        })
        trace.write({
            "type": "tool_result", "iter": 2, "tool": "get_market_data", "call_id": CALL,
            "ts": later + 1, "status": "ok", "result": PAYLOAD,
        })
    finally:
        trace.close()
    assert load_prior_market_evidence(store, attempt, messages) is None


def test_followup_can_reuse_a_legacy_us_quote_with_no_currency_conversion(tmp_path: Path) -> None:
    payload = json.loads(PAYLOAD)
    payload["_provenance"][SYMBOL].pop("quote_currency")
    evidence = load_prior_market_evidence(*_saved_turn(tmp_path, result=json.dumps(payload)))
    assert evidence is not None and evidence.currency == "USD"


@pytest.mark.parametrize("invalid", ["duplicate", "wrong-run", "missing-converted-currency", "ambiguous-reply", "malformed-trail"])
def test_followup_rejects_ambiguous_or_incomplete_evidence(tmp_path: Path, invalid: str) -> None:
    payload = json.loads(PAYLOAD)
    if invalid == "missing-converted-currency":
        payload["_provenance"][SYMBOL].pop("quote_currency")
        payload["_provenance"][SYMBOL]["currency_conversion"] = "converted"
    store, attempt, messages = _saved_turn(
        tmp_path, result=json.dumps(payload), matching_run_dir=invalid != "wrong-run",
    )
    if invalid == "ambiguous-reply":
        messages.append(messages[0])
    if invalid == "malformed-trail":
        messages[0].tool_trail = [None]
    if invalid == "duplicate":
        parent = store.get_attempt(attempt.session_id, attempt.parent_attempt_id)
        assert parent is not None
        trace = TraceWriter(store.base_dir / attempt.session_id)
        try:
            trace.write({
                "type": "tool_result", "iter": 1, "tool": "get_market_data", "call_id": CALL,
                "status": "ok", "result": PAYLOAD, "ts": parent.started_at,
            })
        finally:
            trace.close()
    assert load_prior_market_evidence(store, attempt, messages) is None


class _UnusedLLM:
    def stream_chat(self, *_args, **_kwargs):
        pytest.fail("An explicit saved-close report must not call the model")


def test_followup_rejects_an_evidence_object_for_another_request(tmp_path: Path, monkeypatch) -> None:
    store, attempt, messages = _saved_turn(tmp_path)
    evidence = load_prior_market_evidence(store, attempt, messages)
    assert evidence is not None
    monkeypatch.setattr("src.agent.loop.SESSIONS_DIR", store.base_dir)
    agent = AgentLoop(registry=ToolRegistry(), llm=_UnusedLLM(), max_iterations=3)
    agent.memory.run_dir = str(tmp_path)
    result = agent.run(PROMPT.replace(DATE, "2026-10-21"), session_id=attempt.session_id, prior_market_evidence=evidence)
    assert result["status"] == "success"
    assert "没有可核验" in result["content"]
    assert CALL not in result["content"]


@pytest.mark.parametrize("has_evidence", [True, False])
@pytest.mark.parametrize("explicit_call, intervening_turns", [(False, 0), (True, 1), (True, 50)])
def test_session_followup_releases_only_stored_close_without_llm_or_tools(
    tmp_path: Path, monkeypatch, has_evidence: bool, explicit_call: bool, intervening_turns: int,
) -> None:
    store, attempt, messages = _saved_turn(tmp_path, result=PAYLOAD if has_evidence else None)
    service: SessionService = _service(tmp_path, monkeypatch)
    session = store.get_session(attempt.session_id)
    assert session is not None
    session.last_attempt_id = attempt.parent_attempt_id
    store.append_message(messages[0])
    for _ in range(intervening_turns):
        middle = Attempt(session_id=session.session_id, parent_attempt_id=session.last_attempt_id, prompt="插入一轮对话")
        middle.mark_running()
        middle.mark_completed()
        store.create_attempt(middle)
        session.last_attempt_id = middle.attempt_id
        store.append_message(Message(session_id=session.session_id, role="user", content=middle.prompt))
        store.append_message(Message(
            session_id=session.session_id, role="assistant", content="收到。", linked_attempt_id=middle.attempt_id,
        ))
    store.update_session(session)
    if intervening_turns == 50:
        assert messages[0] not in store.get_messages(session.session_id)
    monkeypatch.setattr("src.agent.loop.SESSIONS_DIR", store.base_dir)
    monkeypatch.setattr(
        "src.providers.chat.ChatLLM",
        lambda: pytest.fail("A saved-close report must not initialize a model client"),
    )
    monkeypatch.setattr("src.agent.loop.RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(
        "src.tools.build_registry",
        lambda **_kwargs: pytest.fail("A saved-close report must not initialize the tool registry"),
    )

    async def run():
        submitted = await service.send_message(session.session_id, EXPLICIT if explicit_call else PROMPT)
        await service._active_tasks[session.session_id]
        return submitted["attempt_id"]

    attempt_id = asyncio.run(run())
    reply = store.get_messages(session.session_id)[-1]
    assert reply.linked_attempt_id == attempt_id
    assert reply.metadata["status"] == "completed"
    assert reply.tool_trail == []
    events = TraceWriter.read(store.base_dir / session.session_id)
    if has_evidence:
        assert events[-4]["type"] == "prior_market_evidence"
        assert events[-4]["call_id"] == CALL
        assert set(events[-4]) >= {"source_attempt_id", "symbol", "date"}
        assert reply.content.splitlines() == [
            "# AAPL.US", "", "| 项目 | 值 |", "|---|---|",
            f"| 日期 | {DATE} |", f"| 收盘价 (USD) | {CLOSE} |", "| 实际数据源 | yfinance |",
            "| 复权口径 | split_dividend |", f"| 原工具调用 ID | `{CALL}` |",
        ]
        assert [(event["type"], event.get("call_id")) for event in events if event["type"].startswith("tool_")] == [
            ("tool_call", CALL), ("tool_result", CALL),
        ]
    else:
        assert events[-4]["type"] == "prior_market_evidence_unavailable"
        assert reply.content == (
            "同一会话中没有与指定工具调用 ID 匹配且可核验的日线收盘价。" if explicit_call
            else "上一轮没有可核验的匹配日线收盘价及原始调用证据。"
        )
        assert not any(event["type"].startswith("tool_") for event in events)
