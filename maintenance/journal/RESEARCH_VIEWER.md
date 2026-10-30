# 研究包只读查看器

从现有 v1 研究 ZIP 生成单个自包含 HTML，在本地浏览器查看价格、显式复权口径与观测覆盖。无需原始 CSV、原任务配置、网络、服务端或新增 Python 依赖。此入口独立于账户 journal 与 shadow。

## 运行

在仓库根目录使用 Python 3.12。先用[合成示例](RESEARCH_BUNDLE.md#先运行合成示例)生成 ZIP；示例的代码与价格均为合成，不代表真实证券或供应商数据。

```sh
python -m maintenance.journal.research_bundle build \
  maintenance/journal/examples/synthetic_task.json \
  --output private-journal/synthetic-research.zip

python -m maintenance.journal.research_viewer \
  private-journal/synthetic-research.zip \
  --output private-journal/synthetic-view.html
```

打开生成的 HTML 即可使用。股票选择、价格图与每页 50 行的原始价格表使用本地 JavaScript；关闭脚本时仍可阅读口径、来源和全部观测覆盖。若文件已存在，请使用新的输出文件名；不会覆盖已有结果。

可加 `--expected-id <预先记录的64位package_id>`。未提供时，页面明确显示“未提供外部 ID”；提供且匹配时显示“预先记录 ID 已匹配”。两者均不认证供应商真实性。

## 展示与边界

- 原始价格、日期、lastupdated 不经浮点改写；包内 close 列对应所记录的 close、closeadj 或 closeunadj，没有默认复权。
- 图形仅使用近似坐标，横轴按实际日期间隔；极端数值超出绘图范围时提示改读原始表格。单点不显示趋势，连线不补造中间日期记录。
- 请求窗和实际观测首末日、行数分别展示。预期交易日历未验证，不能据此计算完整覆盖率或把间隔自动称为缺失交易日。
- 保留提取时间及 unknown 时区、PIT/永久证券身份等未验证边界；lastupdated 是修订日期，ZIP 的固定技术时间不是提取时间。
- 展示来源声明、显式字段/口径、package_id、ZIP/源快照 SHA256。完整性与口径一致性不替代来源认证或逐价真值。

输入先通过既有 `verify_bundle`。查看器保留有界 ZIP 字节，并核对这份字节的 hash 与校验结果；后续仅解析已匹配的字节，避免校验后路径被替换。整个过程不解压、不读取源价格文件。包上限沿用 128MiB；本查看器最多 100000 行，超限明确失败、不截断。

HTML 的文字和 JSON 均转义，DOM 写入使用文本；CSP 仅允许所附哈希脚本/样式并禁用网络请求。生成 HTML 前不写最终输出，完成后以不覆盖方式发布；已有文件、符号链接、竞争写入均受到保护。输入/校验/输出错误的 CLI 退出码为 2。

API：`maintenance.journal.research_viewer.render_viewer(bundle_path, output_path, expected_package_id=None)`；后一个参数为关键字参数。返回已验证包摘要及 `viewer_sha256`。

HTML 包含包内价格内容，沿用原数据的本地存储与分享边界。真实包与 HTML 留在已忽略的 private-journal/；公开源码与 CI 只使用合成数据。查看器不计算策略收益或推断账户成交。
