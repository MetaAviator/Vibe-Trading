# 安装 Journal Lab / Install Journal Lab

在本机生成可复算的报告，核对成交价、费用、复权口径与净盈亏。这页安装的是离线复盘工具所需的最小 Python 环境。完整交易智能体的安装见[上游快速安装入口](../../README.md#one-line-install-pypi)。

如果已有 Python 3.12，可先下载约 43 KB 的样例直接复算；这个入口无需安装 pip 依赖。[先核对一份报告](SYNTHETIC_REPLAY.md#先核对一份报告)

## 获取源码 / Get the source

确认本机可运行 `python3.12 --version`，或在 Windows PowerShell 中运行 `py -3.12 --version`。[Python 安装说明](https://docs.python.org/3.12/using/index.html)

如果从 GitHub Release 开始，请在所选版本的 **Assets** 中下载 **Source code (zip)** 并解压，或用 `git clone --branch <版本标签> --single-branch https://github.com/MetaAviator/Vibe-Trading.git` 检出同一版本。

开发版可从 GitHub [下载维护分支源码 ZIP](https://github.com/MetaAviator/Vibe-Trading/archive/refs/heads/codex/rix-journal-maintenance.zip) 并解压，或用 Git 检出。

```sh
git clone --branch codex/rix-journal-maintenance --single-branch https://github.com/MetaAviator/Vibe-Trading.git
cd Vibe-Trading
```

如果解压工具无法处理源码 ZIP 中的中文文件名，可用 `python3.12 -m zipfile -e <源码ZIP> <目标目录>` 解压。Windows 对应的命令是 `py -3.12 -m zipfile -e <源码ZIP> <目标目录>`。

以下命令从源码根目录运行。安装阶段从 PyPI 下载按哈希锁定的依赖。生成样例可离线进行，无需模型、券商连接、行情订阅或真实账户数据。

## macOS / Linux

```sh
python3.12 -m venv .venv-journal
.venv-journal/bin/python -m pip install --require-hashes -r maintenance/journal/requirements.lock
PYTHONPATH=agent .venv-journal/bin/python -m maintenance.journal.examples.synthetic_replay \
  build private-journal/synthetic-replay
```

## Windows PowerShell

使用 Python 3.12 安装程序提供的 `py` 启动器。在 PowerShell 中运行以下命令，无需激活虚拟环境。

```powershell
py -3.12 -m venv .venv-journal
& .\.venv-journal\Scripts\python.exe -m pip install --require-hashes -r maintenance/journal/requirements.lock
$env:PYTHONPATH = 'agent'
& .\.venv-journal\Scripts\python.exe -m maintenance.journal.examples.synthetic_replay build private-journal/synthetic-replay
```

## 检查结果 / Check the result

成功时终端会显示 `"net_pnl_usd": "4.00"`，并输出包 ID。用浏览器打开 `private-journal/synthetic-replay/research.html`，可核对两天的未复权价格及源快照。再次生成需要指定一个新的输出目录。

样例通过后，可[用自己的规范化成交 CSV 生成本地报告](LOCAL_TRADES.md)。

复制报告、成交 CSV 和研究 ZIP 后，可用 Python 3.12 的标准库在另一台机器复算。[独立复算步骤](SYNTHETIC_REPLAY.md#导出后独立复算)
