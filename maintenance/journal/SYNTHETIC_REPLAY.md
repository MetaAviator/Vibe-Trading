# 离线研究与复盘样例

用合成证券 AAA 的两笔模拟成交，核对报告中的 4.00 USD 净盈亏。两笔成交价分别取自样例的未复权收盘价。成交时间、数量和费用都是样例设定。

## 先核对一份报告

[下载复盘样例 ZIP](replay-starter.zip?raw=1)并解压。有 Python 3.12 就能在解压后的目录复算，也可在本分支源码根目录运行同一命令。无需安装 Vibe-Trading 或 pip 依赖，也无需账户、模型、行情订阅或原始行情 CSV。

```sh
python3.12 -S -m maintenance.journal.examples.synthetic_replay verify \
  maintenance/journal/examples/starter_evidence \
  --expected-id "$(cat maintenance/journal/examples/starter-package-id.txt)"
```

Windows PowerShell 可运行同一入口。

```powershell
py -3.12 -S -m maintenance.journal.examples.synthetic_replay verify maintenance/journal/examples/starter_evidence --expected-id (Get-Content maintenance/journal/examples/starter-package-id.txt)
```

看到 `"net_pnl_usd": "4.00"` 就完成了第一次复算。三个文件同时保留成交、费用和来源价格，适合先核对报告中的数字，再安装环境生成自己的研究与复盘证据。

## 生成证据

按平台安装最小复盘环境后，可在原有合成行情上生成报告。[macOS、Linux 和 Windows 安装步骤](INSTALL.md)

以新目录作为输出。记录控制台打印的 `package_id`，在浏览器打开输出目录中的 `research.html`。核对 `closeunadj` 和两个日期的原始价格，展开「查看包与快照身份」核对包 ID 和源快照 SHA256。

## 导出后独立复算

将 `research.zip`、`fills.csv` 和 `journal.json` 拷贝到另一目录或机器。下载同一提交的源码，用 Python 3.12 指定生成时另行记录的包 ID。

```sh
python3.12 -S -m maintenance.journal.examples.synthetic_replay \
  verify /path/to/copied-evidence --expected-id '<生成时记录的 package_id>'
```

核验器将成交价格与包内对应证券和日期的未复权价格逐项匹配，再用 Decimal 从成交量、价格和各行费用独立计算净盈亏。预期是一组平仓、0.50 USD 费用和 4.00 USD 净盈亏。

维护分支的离线 CI 在 Linux 构建样例，仅传递这三个文件给 macOS。macOS 移走源 CSV 后，用 Python 标准库复算新导出，同时重新生成并解压公开下载包，确认它与已发布文件逐字节一致。

| 证据 | 核对内容 |
| --- | --- |
| `research.zip` | 规范化请求、原始小数价格、观测覆盖、复权口径、源快照 SHA256 和包身份。 |
| `fills.csv` | 两笔固定模拟成交及费用，买入 2 股 × 20.25，卖出 2 股 × 22.50。 |
| `journal.json` | 实际 journal 分析入口生成的配对和净盈亏，计算式为 45.00 − 40.50 − 0.50。 |
| `research.html` | 可选的离线查看器，适合展示对应日线价格和证据口径。 |

这个样例固定使用仓库合成数据。授权行情的输入合同见[研究输入包](RESEARCH_BUNDLE.md)，真实成交和账户复盘的验收条件见[真实数据验收](REAL_DATA.md)。
