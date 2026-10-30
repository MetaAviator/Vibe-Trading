# 可复现研究输入包 v1

在仓库根目录用 Python 3.12 运行。任务配置记录本地快照、股票、日期窗和显式价格字段；构建器复用[本地价格读取器](MARKET_DATA.md#本地-sharadar-价格只读适配)，一次读取完整源文件校验SHA256，生成单个ZIP。包可在没有源CSV的情况下离线检查。

## 先运行合成示例

示例中的AAA、BBB及全部价格均为生成的合成数据，不代表真实证券或供应商价格。

```sh
python -m maintenance.journal.research_bundle build \
  maintenance/journal/examples/synthetic_task.json \
  --output private-journal/synthetic-research.zip

python -m maintenance.journal.research_bundle verify \
  private-journal/synthetic-research.zip
```

build返回package_id、archive_sha256、所选字段/口径、行数和观测覆盖。记录package_id后，可用 `verify <bundle.zip> --expected-id <已记录的64位package_id>` 锁定包内容；内容修改后即使重算内部清单，也会与已记录ID不符。

输出必须是新文件。已有文件或符号链接不会替换；校验失败退出码为2，不生成结果。成功发布使用完整临时ZIP的不覆盖链接，避免并发写入替换结果。

## 本地任务配置

完整可运行例子见 [synthetic_task.json](examples/synthetic_task.json)。将它另存到private-journal并按实际文件修改：

| 字段 | 合同 |
|---|---|
| schema_version | 整数1，布尔值不接受 |
| source_csv | 已存在的本地stocks/SEP快照；相对路径以任务JSON目录为基准 |
| expected_sha256 | 预先登记的完整源文件SHA256，不由构建器猜测或刷新 |
| tickers | 非空字符串列表；去两端空白、转大写并归一为排序唯一集合 |
| start、end | ISO YYYY-MM-DD，含两端；每个请求代码在窗口内至少有一条价格 |
| price_field | 必填close、closeadj或closeunadj，复权口径沿用行情子合同，无默认值 |
| manifest（可省略） | 既有本地pull_manifest.json，核对stocks项；提取时间与时区未知仍保留unknown |
| max_rows（可省略） | 正整数，默认100000；超限报错，不截断 |

任务JSON上限1MiB，拒绝未知/重复键、非有限JSON常量、错误类型和无效日期。路径按字面解析，不展开环境变量或执行命令；不接受网络URL，不下载数据或读取API key。

本地任务配置负责路径绑定；包内request.json记录规范化请求和源/清单hash，不复制原始文件路径。再次构建使用原本地任务和固定快照；移动路径而保持数据、请求和清单字节不变时，包身份不变。

## ZIP内容与复现身份

ZIP恰含以下5个文件：

| 文件 | 内容 |
|---|---|
| prices.csv | date,ticker,close,lastupdated；close为显式选定字段的原始十进制文本 |
| metadata.json | 来源schema、字段/复权、单位、频率、快照hash、提取记录、筛选/观测覆盖和验证边界 |
| request.json | 不含路径的规范化请求与源/清单身份 |
| REPORT.md | 来源、口径、请求窗口、各代码实际首末日/行数及未验证项 |
| manifest.json | 内容文件字节数/SHA256、package_id、构建器/校验器/适配器代码指纹和Python版本 |

price_field/basis必须随prices.csv保留。包内close列不把不同复权序列变成同一口径；lastupdated只是修订日期。请求窗与实际首末日不等于预期交易日完整覆盖。

相同规范化任务、源文件/清单字节、工具代码和Python运行环境重复构建，ZIP字节和hash一致。容器采用固定顺序、权限和技术时间戳，以及ZIP_STORED；技术时间戳不表示市场日期或提取时刻。代码或Python版本变动会记录到包身份中，不承诺跨版本字节相同。

verify只读取ZIP，不向文件系统解压。它检查固定成员集、无重复/穿越成员、无符号链接/加密/压缩成员、128MiB大小界限、文件校验和与package_id，还检查CSV日期/排序/唯一性/正有限价格/修订日、请求与元数据口径/覆盖一致，以及报告和已检元数据一致。

校验和说明完整性和文件身份。未提供外部已记录ID时，自洽的重制包不能因此得到来源认证；即使提供ID，也不替代对供应商、逐价真值、永久证券身份、PIT或交易日历的验证。包不生成账户成交、策略收益或投资结论。

## API与本地交付

`maintenance.journal.research_bundle.build_bundle(task_path, output_path)` 构建并自检后返回摘要。

`maintenance.journal.research_bundle_check.verify_bundle(bundle_path, expected_package_id=None)` 独立离线校验并返回相同摘要。两者使用原标准库和已有读取器，无新应用依赖。

真实源文件、本地配置和生成包留在已忽略的private-journal/；研究证据留在research/。公开仓库与CI只使用上述合成示例。生成包保留数据内容，仍按原有私有数据边界处理。

已有 ZIP 可交给[研究包只读查看器](RESEARCH_VIEWER.md)生成单个离线 HTML：先校验，再查看价格、复权口径与观测覆盖；无需重新读取源 CSV 或构建 ZIP。
