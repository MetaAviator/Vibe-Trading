"""A five-row report keeps counts distinct from quotes and uses real call refs.

The OHLCV values here are synthetic; live acceptance evidence stays private.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.grounding import GroundingLedger
from src.agent.loop import AgentLoop
from src.agent.tools import BaseTool, ToolRegistry
from src.providers.chat import LLMResponse, ToolCallRequest

pytestmark = pytest.mark.unit

CALL = "call_market_9f3a"
SYMBOL = "AAPL.US"
ROWS = [
    {"trade_date": f"2026-10-{day}", "close": 340.25 + index, "volume": 1000 + index}
    for index, day in enumerate(range(19, 24))
]
PAYLOAD = json.dumps({SYMBOL: ROWS, "_provenance": {SYMBOL: {"source": "yfinance"}}})


def _draft(*, ref: str | None = None) -> str:
    text = (
        "AAPL.US 日线（yfinance，USD），预期交易日历完整性未验证。\n\n"
        "| 项目 | 值 |\n|---|---|\n| **实际行数** | 5 行 |\n\n"
        "| 日期 | 收盘价 |\n|---|---|\n"
        + "".join(f"| {row['trade_date']} | {row['close']} |\n" for row in ROWS)
    )
    if ref is not None:
        text += "\n```figures\n5 | count | 实际行数\n" + "\n".join(
            f"{row['close']} | observed | {SYMBOL} {row['trade_date']} 收盘价 | {ref}"
            for row in ROWS
        ) + "\n```"
    return text


def _ledger(tmp_path: Path) -> GroundingLedger:
    ledger = GroundingLedger(run_dir=tmp_path, user_message=f"报告 {SYMBOL} 日线数据")
    ledger.ingest_tool_result(
        tool_name="get_market_data", arguments={"codes": [SYMBOL], "source": "yfinance"},
        result=PAYLOAD, call_id=CALL, success=True,
    )
    return ledger


def test_five_row_report_passes_without_treating_row_count_as_a_price(tmp_path: Path) -> None:
    result = _ledger(tmp_path).validate_final_answer(_draft())
    assert result.valid, result.issues
    assert result.released_text.strip() == _draft().strip()


def test_bad_market_path_stays_rejected_and_correction_names_the_real_call(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    result = ledger.validate_final_answer(_draft(ref=f"get_market_data::{SYMBOL}[0].close"))
    assert not result.valid
    assert all(issue["reason"] == "not_in_referenced_call" for issue in result.issues)
    correction = ledger.correction_prompt(result)
    assert CALL in correction
    assert "exact call id alone" in correction
    corrected = ledger.revalidate(_draft(ref=CALL))
    assert corrected.valid, corrected.issues
    # A genuine call id never licenses an invented close or a price in a count role.
    assert not ledger.revalidate(_draft(ref=CALL).replace("340.25", "999.25")).valid
    assert not ledger.revalidate(_draft(ref=CALL).replace("| observed |", "| count |")).valid


class _MarketTool(BaseTool):
    name = "get_market_data"
    description = "Return synthetic market rows."
    parameters = {"type": "object", "properties": {"codes": {"type": "array", "items": {"type": "string"}}}}

    @classmethod
    def check_available(cls) -> bool:
        return False

    def __init__(self, result: str) -> None:
        self.result = result

    def execute(self, **_kwargs) -> str:
        return self.result


def test_agent_loop_releases_prices_after_copying_the_real_market_call(tmp_path: Path) -> None:
    class CorrectingLLM:
        calls = 0

        def stream_chat(self, messages, on_text_chunk=None, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(tool_calls=[ToolCallRequest(CALL, "get_market_data", {"codes": [SYMBOL]})])
            if self.calls == 2:
                content = _draft(ref=f"get_market_data::{SYMBOL}[0].close")
            else:
                assert self.calls == 3
                assert CALL in messages[-1]["content"]
                content = _draft(ref=CALL)
            if on_text_chunk:
                on_text_chunk(content)
            return LLMResponse(content=content)

        def chat(self, _messages, **_kwargs):
            return LLMResponse(content="")

    registry = ToolRegistry()
    registry.register(_MarketTool(PAYLOAD))
    llm = CorrectingLLM()
    agent = AgentLoop(registry=registry, llm=llm, max_iterations=5)
    agent.memory.run_dir = str(tmp_path)
    result = agent.run(f"报告 {SYMBOL} 日线数据")
    assert result["status"] == "success"
    assert not result.get("degraded")
    assert result["content"] == _draft().strip()
    assert llm.calls == 3
