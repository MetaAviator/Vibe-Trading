# 在 Web 中复用一笔已经保存的行情

这个演示用合成数据展示如何按工具调用 ID 找回同一会话的日线收盘价。使用真实的 Web 界面和会话复用逻辑。初始行情、来源和调用 ID 都由脚本预置，`123.45 USD` 是演示值。

## 启动演示

需要 Python 3.12、Node.js 22.22 或更新版本以及已安装的 Web 源码依赖。首次安装可在仓库根目录执行：

```sh
python3.12 -m venv .venv-demo
.venv-demo/bin/python -m pip install -e .
npm --prefix frontend ci
npm --prefix frontend run build
```

已安装依赖并构建 Web 前端后，从仓库根目录启动：

```sh
PYTHONPATH=agent .venv-demo/bin/python agent/scripts/demo_market_followup.py
```

已有 Web 开发环境可以直接使用现有的 Python 解释器。若端口被占用，在命令结尾加 `--port 8906`。演示绑定本机环回地址，在临时目录写入合成会话和追踪记录。用 Ctrl+C 停止后，临时目录会被清理。演示运行无需模型、API 密钥或行情订阅。

## 复现同会话和新会话的行为

1. 打开终端打印的「原始回合」链接。在返回记录的开头找到 `123.45` 和 `call_synthetic_market_demo`。原始回合之后已插入 50 轮合成对话。
2. 打开「同一会话」链接。页面只加载最近 100 条消息，原始回合已移出这个窗口。复制下面这条消息并发送。

   ```text
   复用 get_market_data 调用 ID `call_synthetic_market_demo` 的 AAPL.US 2026-10-22 日线收盘价。
   ```

   助手应显示 `AAPL.US`，日期为 `2026-10-22`，收盘为 `123.45 USD`，来源为 `synthetic-demo`，复权口径为 `unadjusted`，调用 ID 与原始回合一致。
3. 打开「新会话」链接，发送同一句话。助手应返回「同一会话中没有与指定工具调用 ID 匹配且可核验的日线收盘价。」

可打开终端打印的 `trace.jsonl` 核对原始合成 `tool_call` 和 `tool_result`，以及本次产生的 `prior_market_evidence`。两次发送都无需调用模型或重新取数。
