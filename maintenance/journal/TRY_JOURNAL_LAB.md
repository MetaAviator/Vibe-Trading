# 试用 Rix Journal Lab / Try Rix Journal Lab

在自己的机器上复算成交报告，并沿报告追到原始成交与费用。两份样例都使用合成数据，解压后只需要 Python 3.12。可以把下载的 ZIP 移到另一台已有 Python 3.12 的机器验证。

## 1. 复算 4.00 USD / Recompute 4.00 USD

[下载约 43 KB 的样例 / Download the 43 KB sample](replay-starter.zip?raw=1)。解压，在解压目录打开终端。成功时终端会显示 `"net_pnl_usd": "4.00"`。用 `maintenance/journal/examples/starter_evidence/fills.csv` 核对两笔成交和费用，就能跟着复算这笔盈亏。

Extract the ZIP and open a terminal in the extracted folder. Run the command for your platform, then find `4.00` in the output and the two fills in `maintenance/journal/examples/starter_evidence/fills.csv`.

macOS / Linux

```sh
python3.12 -S -m maintenance.journal.examples.synthetic_replay verify maintenance/journal/examples/starter_evidence --expected-id "$(cat maintenance/journal/examples/starter-package-id.txt)"
```

Windows PowerShell

```powershell
py -3.12 -S -m maintenance.journal.examples.synthetic_replay verify maintenance/journal/examples/starter_evidence --expected-id (Get-Content maintenance/journal/examples/starter-package-id.txt)
```

## 2. 查看逐笔配对和离线报告 / Inspect FIFO matches offline

[下载 FIFO 核验样例 / Download the FIFO sample](review-starter.zip?raw=1)，解压到另一个目录。在外层解压目录运行以下一行，保留内层 `review.zip` 供核验。

Extract this download into another folder and run the command there. Keep the inner `review.zip` intact for verification.

macOS / Linux

```sh
python3.12 -S -m maintenance.journal.review_bundle verify review.zip --expected-sha256 "$(cat review-sha256.txt)" --verify-fifo --output-html receipt.html
```

Windows PowerShell

```powershell
py -3.12 -S -m maintenance.journal.review_bundle verify review.zip --expected-sha256 (Get-Content review-sha256.txt) --verify-fifo --output-html receipt.html
```

在浏览器离线打开 `receipt.html`，看 ZIP 哈希是否匹配，费用是否为 **0.39 USD**，以及三段 FIFO 净盈亏是否合计 **1.61 USD**。试着用页面上第一段的 `S001` 和 `S003`，在 `review.zip` 内的 `trades.csv` 找到两侧原始成交。想再次生成 HTML 时，指定一个新的 `.html` 文件名。

Open `receipt.html` to see the expected ZIP hash match, USD 0.39 in fees and USD 1.61 in FIFO P&L across three matches. Look up IDs `S001` and `S003` in `trades.csv` inside `review.zip`.

## 3. 用自己的成交 CSV / Generate a report from your CSV

[安装 Journal Lab / Install Journal Lab](INSTALL.md) · [支持的成交格式与证据移交 / Trade CSV and evidence transfer](LOCAL_TRADES.md)

首版范围是单账户、单期间的美元计价美股现货历史复盘。输入为费用明确、期初期末空仓的规范化成交 CSV。Rix Journal Lab 基于 [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)，随附 [MIT 许可证](../../LICENSE)和[上游声明](../../NOTICE)。
