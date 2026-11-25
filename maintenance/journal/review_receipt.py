"""Render a local HTML receipt from a successful trade bundle verification."""

from __future__ import annotations

from base64 import b64encode
from collections.abc import Iterable
from hashlib import sha256
from html import escape

_STYLE = """:root{font:16px/1.55 system-ui,sans-serif;color:#192640;background:#edf1f6}
*{box-sizing:border-box}
body{margin:0;padding:clamp(12px,4vw,42px)}
main{max-width:1040px;margin:auto;background:#fff;padding:clamp(18px,4vw,48px);border-radius:16px}
h1{font-size:clamp(1.65rem,4vw,2.35rem);margin:0;letter-spacing:.02em}
h2{font-size:1.15rem;margin:0 0 10px}
p{margin:8px 0;color:#44536b}
section{border-top:1px solid #dfe5ed;margin-top:24px;padding-top:22px}
.summary,.facts{margin:18px 0;display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,180px),1fr));gap:10px}
dl div{min-width:0;background:#f3f6fa;padding:10px 14px;border-radius:8px}
dt{font-size:.87rem;color:#41536c}
dd{margin:3px 0 0;font-variant-numeric:tabular-nums}
.summary dd{font-size:1.35rem;font-weight:700}
.hashes{display:grid;gap:9px;margin-top:12px}
.hashes code{display:block;overflow-wrap:anywhere;font-size:.86rem}
.scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse;text-align:left;font-size:.88rem;font-variant-numeric:tabular-nums}
th,td{padding:9px 10px;border-bottom:1px solid #dfe5ed;vertical-align:top}
th{background:#f3f6fa;white-space:nowrap}
td{overflow-wrap:anywhere;min-width:4rem}
"""
_STYLE_HASH = b64encode(sha256(_STYLE.encode("utf-8")).digest()).decode("ascii")


def _text(value: object) -> str:
    return escape(str(value))


def _facts(rows: Iterable[tuple[str, str]]) -> str:
    return "".join(f"<div><dt>{_text(label)}</dt><dd>{_text(value)}</dd></div>" for label, value in rows)


def _fifo_section(receipt: dict) -> str:
    if "fifo_matches" not in receipt:
        return (
            "<section><h2>逐笔 FIFO</h2><p>本次核对了报告中的 FIFO 差额算术。"
            "逐笔复算可使用 --verify-fifo。</p></section>"
        )
    columns = (
        ("证券", "symbol"),
        ("买入成交 ID", "buy_execution_id"),
        ("卖出成交 ID", "sell_execution_id"),
        ("数量", "quantity_exact"),
        ("买入费用", "buy_fee_usd_exact"),
        ("卖出费用", "sell_fee_usd_exact"),
        ("净盈亏", "net_pnl_usd"),
    )
    headers = "".join(f'<th scope="col">{_text(label)}</th>' for label, _ in columns)
    rows = "".join(
        "<tr>" + "".join(f"<td>{_text(match[key])}</td>" for _, key in columns) + "</tr>"
        for match in receipt["fifo_matches"]
    )
    return (
        "<section><h2>逐笔 FIFO</h2>"
        f'<p>独立复算 {_text(receipt["fifo_match_count"])} 段。报告样本已核对 {_text(receipt["fifo_samples_checked"])} 段。'
        "数量和分摊费用用精确分数表示，金额单位为 USD。</p>"
        '<div class="scroll" role="region" aria-label="FIFO 配对表" tabindex="0">'
        f"<table><thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table></div></section>"
    )


def render_receipt(receipt: dict, *, matched_expected_sha256: bool) -> bytes:
    cash = receipt["cash_check"]
    fifo_label = "独立 FIFO 盈亏" if "recomputed_fifo_pnl" in receipt else "报告所列 FIFO 盈亏"
    fifo_pnl = receipt.get("recomputed_fifo_pnl", receipt["reported_fifo_pnl"])
    summary = _facts(
        (
            ("净成交现金", f'{cash["net_trade_cash"]} USD'),
            ("费用合计", f'{cash["fees"]} USD'),
            (fifo_label, f"{fifo_pnl} USD"),
        )
    )
    amounts = _facts(
        (
            ("买入毛额", f'{cash["buy_gross"]} USD'),
            ("卖出毛额", f'{cash["sell_gross"]} USD'),
            ("FIFO 盈亏与净成交现金差额", f'{cash["fifo_minus_cash"]} USD'),
        )
    )
    hashes = "".join(
        f"<div><dt>{label} SHA256</dt><dd><code>{_text(receipt[key])}</code></dd></div>"
        for label, key in (("ZIP", "bundle_sha256"), ("原始 CSV", "source_sha256"), ("报告 JSON", "report_sha256"))
    )
    match_status = (
        "ZIP 已匹配预先记录的 SHA256。"
        if matched_expected_sha256
        else "已计算 ZIP SHA256。核验范围为包内文件的一致性及复算结果。"
    )
    page = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'sha256-{_STYLE_HASH}'; base-uri 'none'; form-action 'none'">
<title>成交报告核验</title><style>{_STYLE}</style></head>
<body><main><header>
<h1>成交报告核验</h1>
<p>{_text(receipt["date_range"])} · {_text(receipt["source_rows"])} 笔成交</p>
</header>
<dl class="summary">{summary}</dl>
<section><h2>现金核对</h2><dl class="facts">{amounts}</dl></section>
{_fifo_section(receipt)}
<section><h2>文件身份</h2><p>{match_status}</p><dl class="hashes">{hashes}</dl></section>
</main></body></html>
"""
    return page.encode("utf-8")
