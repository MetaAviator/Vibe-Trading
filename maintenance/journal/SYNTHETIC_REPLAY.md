# 离线研究与复盘样例

用合成证券 AAA 的两笔模拟成交，核对报告中的 4.00 USD 净盈亏。两笔成交价分别取自样例的未复权收盘价。成交时间、数量和费用都是样例设定。

## 生成证据

在仓库根目录用 Python 3.12 运行。安装依赖后，样例禁用网络 socket，无需模型、行情账号或券商连接。

```sh
python3.12 -m venv .venv-journal
.venv-journal/bin/python -m pip install --require-hashes -r maintenance/journal/requirements.lock

PYTHONPATH=agent .venv-journal/bin/python -m maintenance.journal.examples.synthetic_replay \
  build private-journal/synthetic-replay
```

以新目录作为输出。记录控制台打印的 `package_id`，在浏览器打开输出目录中的 `research.html`。核对 `closeunadj` 和两个日期的原始价格，展开「查看包与快照身份」核对包 ID 和源快照 SHA256。

## 导出后独立复算

将 `research.zip`、`fills.csv` 和 `journal.json` 拷贝到另一目录或机器。使用同一提交和上面的 Python 依赖，指定生成时另行记录的包 ID。复算只需要这三个导出文件和仓库中的同版样例工具。

```sh
.venv-journal/bin/python -m maintenance.journal.examples.synthetic_replay \
  verify /path/to/copied-evidence --expected-id '<生成时记录的 package_id>'
```

核验器将成交价格与包内对应证券和日期的未复权价格逐项匹配，再用 Decimal 从成交量、价格和各行费用独立计算净盈亏。预期是一组平仓、0.50 USD 费用和 4.00 USD 净盈亏。

| 证据 | 核对内容 |
| --- | --- |
| `research.zip` | 规范化请求、原始小数价格、观测覆盖、复权口径、源快照 SHA256 和包身份。 |
| `fills.csv` | 两笔固定模拟成交及费用，买入 2 股 × 20.25，卖出 2 股 × 22.50。 |
| `journal.json` | 实际 journal 分析入口生成的配对和净盈亏，计算式为 45.00 − 40.50 − 0.50。 |
| `research.html` | 可选的离线查看器，适合展示对应日线价格和证据口径。 |

这个样例固定使用仓库合成数据。授权行情的输入合同见[研究输入包](RESEARCH_BUNDLE.md)，真实成交和账户复盘的验收条件见[真实数据验收](REAL_DATA.md)。
