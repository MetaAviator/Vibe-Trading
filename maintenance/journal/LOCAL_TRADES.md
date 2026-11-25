# 用本地成交 CSV 生成复盘报告

完成 [Journal Lab 安装](INSTALL.md)后，在仓库根目录运行。可以先用仓库里的两种合成证券验证流程。分析和输出留在本机，无需模型、行情订阅或券商连接。

## 先用合成成交走一遍

macOS / Linux

```sh
PYTHONPATH=agent .venv-journal/bin/python -m maintenance.journal.review_trades \
  maintenance/journal/examples/synthetic_review_trades.csv \
  --output private-journal/synthetic-trade-report.json
```

Windows PowerShell

```powershell
$env:PYTHONPATH = 'agent'
& .\.venv-journal\Scripts\python.exe -m maintenance.journal.review_trades maintenance/journal/examples/synthetic_review_trades.csv --output private-journal/synthetic-trade-report.json
```

报告应列出买入毛额 140.00 USD、卖出毛额 142.00 USD、费用 0.39 USD，以及净成交现金和 FIFO 已实现盈亏各 **1.61 USD**。报告中的 `source_sha256` 锁定这次读取的 CSV 字节。相同文件可交给另一台机器用标准库复算。

## 交给另一台机器复算

将原始成交 CSV 和 JSON 报告交给有权复核的人。在同一版仓库的根目录，只用 Python 3.12 运行，保留原 CSV 的字节，避免用表格软件重新保存后改变哈希。

macOS / Linux

```sh
python3.12 -S -m maintenance.journal.review_trades \
  maintenance/journal/examples/synthetic_review_trades.csv \
  --verify-report private-journal/synthetic-trade-report.json
```

Windows PowerShell

```powershell
py -3.12 -S -m maintenance.journal.review_trades maintenance/journal/examples/synthetic_review_trades.csv --verify-report private-journal/synthetic-trade-report.json
```

复核成功时输出 `"status": "ok"`，以及 CSV 和报告各自的 SHA256、行数、日期区间和复算现金。文件不匹配或数值不符时返回退出码 2。要确认收到的是同一份报告，可将输出的报告 SHA256 与通过独立渠道保存的预期值比较。

默认复核毛额、费用、净成交现金和报告所列 FIFO 差额。输出的 `fifo_check` 为 `difference_arithmetic_only`。

需要核对 FIFO 时，在同一条命令末尾加 `--verify-fifo`。程序按原 CSV 的时间和同秒行顺序重做所有配对，按配对份额分摊买卖费用，核对总盈亏、配对总数和报告中展示的最多五段配对。成功结果带有完整的配对清单、两侧成交 ID、精确数量、分摊费用和每段净盈亏。数量与精确费用以分数表示，例如 `1/6` USD。

每段净盈亏用精确有理数计算，再舍入到美分，半分时取偶数。现有报告引擎使用浮点，半分边界可能相差一美分。严格复核遇到差异时会报告两个值或指出有差异的样本，返回退出码 2。成交 ID 会出现在这次本机输出中。费用来源、其他报告统计和账户总账仍按[真实记录验收](REAL_DATA.md)核对。

## 换成自己的 CSV

先保留原券商导出文件，将有权使用的记录另存成 [输入合同](CONTRACT.md)定义的 UTF-8 CSV。参考[合成模板](examples/synthetic_review_trades.csv)，把命令的第一个路径换成自己的文件，并选择新的输出文件名。无需修改模板本身。

- 一份文件对应同一账户、同一时间口径，只纳入 USD 美股现货买入和卖出。按证券使用一致的代码格式。按时间匹配，同一时间戳按 CSV 行顺序。
- 每行需要唯一 `execution_id`、明确的日期时间、数量、价格、毛额、费用和币种。金额使用十进制小数点，`amount` 必须等于 `quantity × price`。只有确认无费用时才填 `0`，已确认的返佣可用负数。程式从期初空仓开始核对，要求所有持仓在表内闭合。
- `cash_check` 从输入的毛额和费用直接算净成交现金，`profile.total_pnl` 来自现有 FIFO 分析器。`fifo_minus_cash` 保留两者差额；每段 FIFO 配对的分级舍入可能产生差异。账户费项、现金、时区和持仓仍须与独立结单核对。
- CSV 是成交输入。拆股、分红、换汇及其他账户现金流需按[真实记录验收](REAL_DATA.md)另行处理。个人 CSV 和报告存放于仓库外或 `private-journal/`。报告生成要求新文件名。

发生输入错误时查看命令标出的 CSV 行和字段，先与原始记录对照。修正规范化副本后再跑。

## 将 CSV 和报告一起移交

发送方可把**原 CSV 和已生成的报告**封装成一个 ZIP 文件。生成 ZIP 前先复算，文件名必须是新路径。在仓库根目录运行以下命令，Windows PowerShell 可将 `python3.12` 换成 `py -3.12`，并把命令写在一行。

```sh
python3.12 -S -m maintenance.journal.review_bundle build \
  maintenance/journal/examples/synthetic_review_trades.csv \
  private-journal/synthetic-trade-report.json \
  --output private-journal/synthetic-trade-review.zip
```

单独记录输出的 `bundle_sha256`。接收方从可信来源取得同版仓库，在仓库根目录直接核对 ZIP，无需解压文件或安装额外 Python 包。用预先记录的 64 个十六进制字符替换下例中的占位符。

```sh
python3.12 -S -m maintenance.journal.review_bundle verify \
  /path/to/synthetic-trade-review.zip --expected-sha256 '<bundle_sha256>'
```

ZIP 原样保留 `trades.csv` 和 `report.json`。核验器比对 ZIP 哈希、原始 CSV 哈希与报告金额，包括费用、净成交现金和 FIFO 差额的算术一致性。`--expected-sha256` 可省略，省略时核对限于包内文件的一致性及重算结果。包内成交与报告均为明文，含个人数据的 ZIP 应留在本机或通过有权访问的私有渠道移交。

在 `build` 或 `verify` 命令末尾加 `--verify-fifo`，即可按原 CSV 重做所有 FIFO 配对，检查总盈亏、总段数及报告展示的配对样本。旧版 ZIP 可直接严格复核，开启选项也不会改变 ZIP 的内容或哈希。验收使用每段半分时取偶数的美分口径。旧报告浮点舍入若有差异，命令返回退出码 2，严格模式封装不会写出 ZIP。
