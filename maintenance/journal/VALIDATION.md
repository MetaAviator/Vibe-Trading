# 验证快照 · 2026-10-27

固定上游b831575c，个人线整合费用06003db4和价格cd22e3bd；后续维护包只加入文档、验收入口、依赖锁、CI和格式修整。原贡献分支未修改。

| 证据 | 本地实际结果 |
|---|---|
| 干净Python 3.12环境，哈希锁安装19个包 | 成功，未借用已有.venv |
| 全部目标journal/date/filter集合，--disable-socket | 416 passed，6条既有日期warnings |
| 独立手工链接Decimal合成账本 | 12/12场景，74行，39配对 |
| 日期/全坏日期/数量/价格/费用/方向6个必需探针 | 均返回error |
| Shadow extraction/backtest/attribution/currency集合（原开发环境） | 102 passed，233条既有warnings |
| Ruff、新numeric与验收脚本Black、diff、CI grep/private-files | 通过 |

92个价格/费用聚焦用例已包含在416中，不重复相加。12个账本场景含格式兼容控制，不代表12种独立策略。缺费、无ID重复和未平仓探针仍展示既有限制，不能据此认定真实账户可信。

远程CI由rix-journal.yml按该实验分支实际提交运行；本地结果不冒充远程通过。可从Actions读取该提交的结果。没有验证完整前端、所有broker、完整agent或实盘。

真实账户验收尚未完成：目前提供的文件是行情数据，不含成交、实际费用、现金或持仓依据。合成与真实门槛分别记录。
