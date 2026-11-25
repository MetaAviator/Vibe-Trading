"""Open two isolated Web chats for an offline, synthetic market replay demo."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory


SYMBOL = "AAPL.US"
DAY = "2026-10-22"
CALL = "call_synthetic_market_demo"
CLOSE = 123.45
SOURCE = "synthetic-demo"
ADJUSTMENT = "unadjusted"
PROMPT = f"复用 get_market_data 调用 ID `{CALL}` 的 {SYMBOL} {DAY} 日线收盘价。"


def seed_sessions(root: Path) -> tuple[str, str]:
    from src.agent.trace import TraceWriter
    from src.session.models import Attempt, Message, Session
    from src.session.store import SessionStore

    store = SessionStore(root / "sessions")
    session = store.create_session(Session(title="合成演示 · 同一会话"))

    def start_turn(prompt: str) -> Attempt:
        attempt = Attempt(session_id=session.session_id, parent_attempt_id=session.last_attempt_id, prompt=prompt)
        attempt.mark_running()
        store.append_message(Message(session_id=session.session_id, role="user", content=prompt))
        return attempt

    def finish_turn(attempt: Attempt, content: str, *, tool_trail: list | None = None) -> None:
        attempt.mark_completed(summary=content)
        store.create_attempt(attempt)
        store.append_message(
            Message(
                session_id=session.session_id,
                role="assistant",
                content=content,
                linked_attempt_id=attempt.attempt_id,
                tool_trail=tool_trail or [],
            )
        )
        session.last_attempt_id = attempt.attempt_id

    first = start_turn(f"演示用合成行情：{SYMBOL} {DAY} 日线收盘价。")
    run_dir = root / "runs" / "synthetic-market-demo"
    run_dir.mkdir(parents=True)
    first.run_dir = str(run_dir)
    args = {"codes": [SYMBOL], "interval": "1D", "source": SOURCE, "run_dir": first.run_dir}
    result = {
        SYMBOL: [{"trade_date": DAY, "close": CLOSE}],
        "_provenance": {
            SYMBOL: {"source": SOURCE, "adjustment": ADJUSTMENT, "quote_currency": "USD"},
        },
    }
    trace = TraceWriter(store.base_dir / session.session_id)
    try:
        trace.write({"type": "tool_call", "iter": 1, "tool": "get_market_data", "args": args, "call_id": CALL})
        trace.write_tool_result(CALL, json.dumps(result), "get_market_data", "ok", 0, 1)
    finally:
        trace.close()
    finish_turn(
        first,
        f"初始合成行情：{CLOSE:.2f} USD，调用 ID `{CALL}`。",
        tool_trail=[
            {"tool": "get_market_data", "status": "ok", "call_id": CALL},
        ],
    )

    for index in range(1, 51):
        attempt = start_turn(f"合成占位消息 {index}/50")
        finish_turn(attempt, "收到。")
    store.update_session(session)
    other = store.create_session(Session(title="合成演示 · 新会话"))
    return session.session_id, other.session_id


def reject_live_calls(*_args, **_kwargs):
    raise RuntimeError("The synthetic demo does not initialize a model or live tools.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8905)
    options = parser.parse_args()
    frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if not (frontend / "index.html").is_file():
        parser.error("Build frontend/dist first with npm run build in frontend/.")

    with TemporaryDirectory(prefix="vibe-synthetic-market-") as temp:
        root = Path(temp)
        os.environ.update(
            {
                "VIBE_TRADING_HOME": temp,
                "PYTHON_DOTENV_DISABLED": "1",
                "ENABLE_SESSION_RUNTIME": "true",
                "API_AUTH_KEY": "",
                "VIBE_TRADING_API_KEY": "",
            }
        )
        from fastapi.responses import JSONResponse
        import uvicorn

        import api_server
        from src.api.spa import SPAStaticFiles
        import src.providers.chat
        import src.tools

        session, other = seed_sessions(root)
        src.providers.chat.ChatLLM = reject_live_calls
        src.tools.build_registry = reject_live_calls

        @api_server.app.middleware("http")
        async def demo_writes(request, call_next):
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                path = request.url.path
                if request.method != "POST" or (
                    path != "/auth/sse-ticket"
                    and path not in {f"/sessions/{session}/messages", f"/sessions/{other}/messages"}
                ):
                    return JSONResponse(
                        {"detail": "Only the two synthetic demo chats accept messages."}, status_code=405
                    )
            return await call_next(request)

        api_server.app.mount("/", SPAStaticFiles(directory=str(frontend), html=True), name="frontend")
        url = f"http://127.0.0.1:{options.port}"
        print(f"同一会话 {url}/?session={session}\n新会话 {url}/?session={other}", flush=True)
        print(f"原始回合 {url}/sessions/{session}/messages?limit=1000\n复制消息 {PROMPT}", flush=True)
        print(f"临时原始记录 {root / 'sessions' / session / 'trace.jsonl'}", flush=True)
        # Use the production routes while skipping startup probes and state migration.
        uvicorn.run(api_server.app, host="127.0.0.1", port=options.port, lifespan="off")


if __name__ == "__main__":
    main()
