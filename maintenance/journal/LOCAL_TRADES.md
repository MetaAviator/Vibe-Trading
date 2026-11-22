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

报告应列出买入毛额 140.00 USD、卖出毛额 142.00 USD、费用 0.39 USD，以及净成交现金和 FIFO 已实现盈亏各 **1.61 USD**。报告中的 `source_sha256` 锁定这次读取的 CSV 字节。相同文件可在另一台机器用同版工具重跑，再核对输入哈希和结果。

## 换成自己的 CSV

先保留原券商导出文件，将有权使用的记录另存成 [输入合同](CONTRACT.md)定义的 UTF-8 CSV。参考[合成模板](examples/synthetic_review_trades.csv)，把命令的第一个路径换成自己的文件，并选择新的输出文件名。无需修改模板本身。

- 一份文件对应同一账户、同一时间口径，只纳入 USD 美股现货买入和卖出。按证券使用一致的代码格式。按时间匹配，同一时间戳按 CSV 行顺序。
- 每行需要唯一 `execution_id`、明确的日期时间、数量、价格、毛额、费用和币种。金额使用十进制小数点，`amount` 必须等于 `quantity × price`。只有确认无费用时才填 `0`，已确认的返佣可用负数。程式从期初空仓开始核对，要求所有持仓在表内闭合。
- `cash_check` 从输入的毛额和费用直接算净成交现金，`profile.total_pnl` 来自现有 FIFO 分析器。`fifo_minus_cash` 保留两者差额；每段 FIFO 配对的分级舍入可能产生差异。账户费项、现金、时区和持仓仍须与独立结单核对。
- CSV 是成交输入。拆股、分红、换汇及其他账户现金流需按[真实记录验收](REAL_DATA.md)另行处理。个人 CSV 和报告存放于仓库外或 `private-journal/`。报告生成要求新文件名。

发生输入错误时查看命令标出的 CSV 行和字段，先与原始记录对照。修正规范化副本后再跑。
