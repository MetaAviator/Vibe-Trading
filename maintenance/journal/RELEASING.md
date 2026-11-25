# Rix Journal Lab 发布资料

发版前固定已验收的维护分支提交。确认独立试用者能根据 GitHub 说明复算 4.00 USD，并从报告追到原始成交。若说明或下载包因此修改，复验所改部分，再固定发布提交。

## 从固定提交准备下载文件

在已有该提交的 Git 检出根目录运行。该命令只需 Python 3.12 和 Git。用完整 SHA 替换 `<commit>`，把输出路径指向新目录。

```sh
python3.12 -S -m maintenance.journal.prepare_release --commit '<commit>' --output journal-release-assets
```

Windows PowerShell 可用 `py -3.12`。该命令从 Git 对象取出 `replay-starter.zip`、`review-starter.zip`，并对两份下载生成 `SHA256SUMS.txt`。目录已存在时命令停止。发布资产来自指定提交中的两个 ZIP。

可在 macOS / Linux 复核文件哈希。

```sh
cd journal-release-assets
shasum -a 256 -c SHA256SUMS.txt
```

Windows PowerShell 可运行 `Get-FileHash -Algorithm SHA256 *.zip`，把两行结果和 `SHA256SUMS.txt` 对照。解压后按各包内的 README 复算。其中 FIFO 包里的 `review-sha256.txt` 用于核对内层 `review.zip`，`SHA256SUMS.txt` 用于校验外层下载。

## GitHub Release 内容

- 发行名称为 **Rix Journal Lab v0.1.0**，标签为 `v0.1.0`。说明项目基于 HKUDS/Vibe-Trading，保留 LICENSE 和 NOTICE。
- 上传两个样例 ZIP 及 `SHA256SUMS.txt`，在正文附上版本对应的试用和安装文档。使用同一 Release 的 **Source code (zip)** 或 `git clone --branch v0.1.0 --single-branch https://github.com/MetaAviator/Vibe-Trading.git` 安装。解压后从源码根目录继续 [安装说明](INSTALL.md) 中的平台步骤。
- 描述首版支持的单账户、单期间、费用明确且期初期末空仓的美元计价美股现货历史复盘，以及本机独立复算成交现金、费用和 FIFO 配对的步骤。
- 指明首包预期结果 4.00 USD。FIFO 包预期费用 0.39 USD，净成交现金与三段 FIFO 盈亏均为 1.61 USD。可从首段配对的两个执行 ID 找回原始成交。
