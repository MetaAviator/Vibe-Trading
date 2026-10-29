# 行情输入子合同

行情记录和账户成交是不同数据类型。三列date,ticker,close可以表示日线价格，不含个人买卖方向、成交数量、实际手续费、现金或仓位。

## 最低字段与元数据

- date：对应交易日；记录市场日历/时区和日线频率。
- ticker：代码及来源映射；同一ticker/date必须有明确唯一性规则。
- close：有限价格；另行保存原始price_field和price_basis，不能仅靠列名推断复权。
- 元数据：source、table、currency、frequency、price_field、price_basis、extracted_at、lastupdated（若有）、文件hash、筛选条件和数据许可来源。

价格口径应明确标为未复权、拆股调整、含分红/分拆调整或unknown；unknown不能静默变成已确认的口径。多种口径的序列不能未经处理直接拼接比较。更新日期也不自动等于历史当时可得日期。

可用行情做研究回测、基准和复盘价格上下文；账户费用闭环仍需独立成交与对账证据。不能从每日收盘价生成“真实成交”，也不能把市场volume解释为账户成交数量。

公开测试只使用生成的合成行情。用户的订阅文件及导出数据留在本地，当前没有在本仓实现付费API下载器或账户连接。

## 本地 Sharadar 价格只读适配

`sharadar_prices.py` 提供独立 Python API 和 CLI。它不自动接入账户 journal，不计算策略收益；只读取调用者指定的本地 stocks/SEP 快照。

依据 [Sharadar 官方字段定义](https://sharadar.com/docs/stocks)，必须显式选择一个原始字段：

| price_field | 输出 price_basis | 单位 |
|---|---|---|
| close | split_adjusted | USD/share |
| closeadj | split_dividend_spinoff_adjusted | USD/share |
| closeunadj | unadjusted | USD/share |

没有默认 price_field。下面的 closeadj 仅为调用示例；它不意味着所有研究问题都应使用该口径。输入变量应指向已有本地文件和预先登记的 SHA256；不在命令中配置 API key。

```sh
python maintenance/journal/sharadar_prices.py "$LOCAL_STOCKS_CSV" \
  --tickers AAA,BBB --start 2026-01-02 --end 2026-01-09 \
  --price-field closeadj --expected-sha256 "$EXPECTED_SHA256" \
  --output private-journal/prices.json
```

如有既有 pull_manifest.json，可额外提供 `--manifest "$LOCAL_PULL_MANIFEST"`。适配器核对其 stocks 项的 hash/bytes/rows/cols；保存 pulled_at 原文和清单 hash。缺失提取时间或时区继续标 unknown，不使用 mtime 替代。清单是本地证据，哈希核对固定文件身份，不单独证明供应商真实性或历史当时可得。

Python API 为 `read_sharadar_prices(path, *, tickers, start, end, price_field, expected_sha256, manifest_path=None, max_rows=100000)`。tickers 是代码字符串集合，日期为 ISO YYYY-MM-DD 且窗口含两端；每个请求代码在窗口内至少有一条记录，否则报错。返回 `metadata` 和 `prices`；价格保留原始十进制字符串，逐条保留 lastupdated，按 date/ticker 排序。metadata 保存来源、字段/口径、单位、频率、快照 hash、筛选条件和实际首末日/行数，不把观测覆盖称为预期交易日完整。

校验范围：

- 全文件：CSV 行宽、非空 ticker、同一读取过程的原始字节 SHA256；读取前后 stat 必须稳定。
- 请求代码：全部日期可解析，防止坏日期被窗口过滤隐藏。
- 所选日期窗口：规范化 ticker/date 唯一，所选价格非空、有限且为正，lastupdated 有效且不早于价格日期。

不校验其他代码/窗口的价格，不做全源质量认证。未选择的价格字段、volume 和 OHLC 顺序不属于这个价格列接口的验收；不填量、不前填、不自动去重/换字段。max_rows 默认 100,000，超限报错而不截断。PIT、永久证券身份、交易日历完整性仍标未验证；lastupdated 只是修订日期。

CLI 校验失败返回退出码 2，不生成价格结果。成功后以不覆盖方式创建新 JSON；既有输出和输入文件不会被替换。private-journal/ 已忽略；公开测试仅运行合成 CSV，不上传个人输出。
