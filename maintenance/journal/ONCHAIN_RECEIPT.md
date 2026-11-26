# 合成链上交换收据复核

这个离线原型用 Ethereum 主网字段格式的合成数据，核对一个钱包经指定路由、在一个 Uniswap v3 池内完成的一次 ERC-20 单跳交换。它使用给定的钱包、路由、池和代币元数据，可将钱包收到和付出的代币数量追溯到原始日志。

## 生成并移交证据包

在源码根目录使用 Python 3.12。无需安装额外依赖或连接网络。

```sh
python3.12 -S -m maintenance.journal.onchain_receipt build maintenance/journal/examples/synthetic_onchain --output private-journal/synthetic-onchain.json
```

程序保存原始 `context.json`、`block.json`、`transaction.json` 与 `receipt.json` 的文本，连同复算报告封装在一个版本化 JSON 文件。记录终端输出的 `bundle_sha256`，通过独立渠道发给接收方。输出路径须为新文件。

接收方可在另一台机器上使用可信版本的 `maintenance/journal/onchain_receipt.py` 和包，不需要发送方的四个原文件。在仅包含该文件和样例包的目录中运行，替换 `<事先记录的 SHA256>`。

```sh
python3.12 -S onchain_receipt.py verify synthetic-onchain.json --expected-sha256 '<事先记录的 SHA256>'
```

在 Windows PowerShell 中将命令开头的 `python3.12` 换成 `py -3.12`。接收方核对整个包的 SHA256 后重新解码原始日志，按整数单位计算结果，并与包内报告比对。

## 样例结果与引用

| 项目 | 样例结果 | 原始依据 |
| --- | --- | --- |
| 钱包付出 | 250.125 TEST-USDC | 日志 8，代币最小单位 250125000 |
| 钱包收到 | 0.1 TEST-WETH | 日志 7，代币最小单位 100000000000000000 |
| 交换 | 钱包收付与池余额变化一致 | 指定池的 Swap 日志 9 |
| gas | 0.003 ETH | 125000 gas × 24000000000 wei，合计 3000000000000000 wei |

报告保留区块和交易哈希、区块号、交易索引、交换与转账日志索引、代币地址和四个输入的 SHA256。包含空日志的失败交易可单列 gas；缺少日志、索引重复、交易引用冲突或转账金额与池变化不符时，核验返回错误。

## 核验范围

支持一个指定钱包直接向路由发送的类型 2 交易，原生 ETH 交易 `value` 为零。成功样例的日志为两条直接在钱包与池之间的 ERC-20 转账，以及一条池 Swap。合成地址、合成区块和给定代币精度用于可复现测试。SHA256 和复算覆盖文件字节及内部一致性。资产数量与 ETH gas 保持原币种，USD 估值及 FIFO 待另行设计。 [Ethereum JSON-RPC](https://ethereum.org/developers/docs/apis/json-rpc/#eth_gettransactionreceipt) · [Uniswap v3 池事件](https://github.com/Uniswap/v3-core/blob/main/contracts/interfaces/pool/IUniswapV3PoolEvents.sol) · [ERC-20](https://eips.ethereum.org/EIPS/eip-20)

## 复算公开 RPC 快照

对于符合相同交易形状的主网交易，保留单次调用的完整 JSON-RPC 响应，使用原有的 `build` 和 `verify` 命令。目录中放入以下输入。

| 文件 | 含义 |
| --- | --- |
| `context.json` | 沿用样例的钱包、路由、池和 token0/token1 元数据，将 `evidence_kind` 设为 `public_rpc_snapshot`，另填 `rpc_source` 作为提供方标签。 |
| `transaction.json` 与 `receipt.json` | 分别来自 `eth_getTransactionByHash` 和 `eth_getTransactionReceipt`。 |
| `block.json` | 来自交易区块高度的 `eth_getBlockByNumber`，第二个参数设为 `false` 以返回交易哈希列表。 |
| `finalized.json` | 来自同一来源的 `eth_getBlockByNumber`，参数为 `["finalized", false]`。 |

接收方检查交易是否位于区块列出的索引，记录 RPC 返回的 finalized 高度与哈希，并拒绝矛盾的引用。离线结论依赖采集时获取的链视图和提供的合约元数据。采集者可用第二个端点复核区块哈希、日志和合约字段。区块包含证明、密码学最终性证明和完整余额对账留在此命令之外。 [Ethereum 执行层 API](https://ethereum.org/developers/docs/apis/json-rpc/#eth_getblockbynumber)
