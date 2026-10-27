# Rix Journal Lab · 实验维护线

本分支基于HKUDS/Vibe-Trading维护只读、离线交易复盘。当前定位是可重复验收的研究版本；支持范围由下表和[来源合同](CONTRACT.md)限定。根LICENSE、NOTICE及上游出处保留。

行情CSV与交易CSV分别定义；已有date,ticker,close格式属于[行情子合同](MARKET_DATA.md)，不能据此完成账户成本验收。

## 维护什么

| 层次 | 本分支责任 |
|---|---|
| 直接维护 | 通用CSV输入合同、费用完整性披露、净盈亏/FIFO统计解释、离线验收与示例 |
| 共享维护 | parser、数字转换、FIFO及目标路径回归；通用缺陷以独立贡献分支回馈上游 |
| 跟踪而不承诺完整支持 | 上游agent/provider、前端、行情源、shadow、其他券商和市场 |
| 首版边界 | 美股现货USD、单账户单期间、只读历史记录、期初期末空仓；真实来源另行核验 |

富途是首个原生适配候选，已有上游parser可复用；当前没有由真实导出确认的完整列合同，不将“存在适配器”解释为“已覆盖全部费用”。通用CSV的amount表示毛成交额，fee表示该行已确认的同币种总费用，不能由金额差额猜测。

## 可重复运行

Python 3.12。在仓库根目录执行：

```sh
python3.12 -m venv .venv-journal
.venv-journal/bin/python -m pip install --require-hashes -r maintenance/journal/requirements.lock
.venv-journal/bin/python -m pytest agent/tests/test*journal*.py agent/tests/test_generic_split_datetime.py --disable-socket -q
.venv-journal/bin/python maintenance/journal/check_ledger.py --output maintenance/journal/artifacts/synthetic
```

Windows使用.venv-journal/Scripts/python.exe。依赖为目标路径的最小集合，不代表完整agent环境。

check_ledger只生成合成数据，不接受个人交易输入。手工指定配对、Decimal现金与费用独立账本核对文件→标准化→FIFO→public JSON；代码指纹和结果写到指定输出目录。执行时禁用网络socket。真实记录须另按[REAL_DATA.md](REAL_DATA.md)验收，不能用合成通过代替。

## 版本、贡献和维护

当前版本见[VERSION](VERSION)，范围与阶段见[ROADMAP.md](ROADMAP.md)，上游基线与本地补丁见[UPSTREAM.json](UPSTREAM.json)。CI只处理仓库内合成数据，禁止上传真实记录、账户文件或其输出。

个人功能在本实验分支推进；上游贡献从即时上游main独立起步。已有贡献反馈继续负责；新上游选题优先与本分支有关的正确性或依赖问题。维护时间以个人项目为主，不以贡献数量作为里程碑，也不承担上游所有子系统的独立支持承诺。
