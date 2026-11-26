# 本地测试钱包转账与离线复核

此演练在全新的 Anvil 节点上用两个一次性地址转移 0.01 测试 ETH。收据可连同父区块和成交区块的余额快照移交，由另一份 Python 脚本副本离线复算价值、手续费和余额差。节点仅绑定 `127.0.0.1`，使用本轮随机生成的 chain ID。

## 运行演练

在 macOS 或 Linux 的仓库根目录，准备 Python 3.12 和 Anvil。验收使用 Foundry Anvil v1.10.0。演练的签名依赖留在独立虚拟环境。 [Foundry v1.10.0 发行文件](https://github.com/foundry-rs/foundry/releases/tag/v1.10.0)

```sh
python3.12 -m venv .venv-wallet-lab
.venv-wallet-lab/bin/python -m pip install 'eth-account==0.14.0'
.venv-wallet-lab/bin/python -m maintenance.journal.examples.local_eth_transfer run --output private-journal/test-eth-transfer.json
```

程序默认从 `PATH` 查找 Anvil，也可用 `--anvil /path/to/anvil`。RPC 地址和测试交易金额由演练程序确定。Anvil 只给新生成的发送地址注入测试余额。发送前检查私钥恢复出的发送地址，使用 EIP-1559 类型 2 签名，然后向本机 Anvil 提交已签名交易。回读成功收据并核对接收方增加 0.01 ETH、发送方减少 0.01 ETH 加实际 gas。 [Anvil 文档](https://www.getfoundry.sh/anvil/index.html) · [eth-account 签名与加密接口](https://eth-account.readthedocs.io/en/stable/eth_account.html)

## 钱包密码和文件

发送方私钥由 `eth-account` 生成，使用 scrypt 加密到仅本轮使用的临时 keystore。POSIX 文件权限为 `0600`。解锁密码由加密安全随机数生成器生成，仅在进程内存使用。程序测试无关密码会被拒绝，再用正确密码签名。签名后删除 keystore，结束后关闭 Anvil 并清理临时目录。程序不签收款地址的交易，也不保留其私钥。输出只包含复核需要的公开字段与测试余额。此演练使用一次性钱包，适合用来理解签名和证据移交。 [Geth keystore 账户说明](https://geth.ethereum.org/docs/developers/dapp-developer/native-accounts)

## 接收方复算

发送方另外记录 `evidence_sha256`。接收方将可信版本的 `maintenance/journal/examples/local_eth_transfer.py` 复制成 `verify.py`，将 `test-eth-transfer.json` 放到同一目录。

```sh
python3.12 -S verify.py verify test-eth-transfer.json --expected-sha256 '<事先记录的 SHA256>'
```

接收方只用 Python 标准库。程序检查区块只含这笔交易、父区块对应交易前余额，收据状态、gas 和双方余额差一致。记录保留本地节点提供的观察值。独立验证者可检查文件完整性和算术关系。
