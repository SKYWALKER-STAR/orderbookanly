"""Escaped, read-only terminal markup backed exclusively by stored book data."""
from datetime import datetime, timezone
from decimal import Decimal
from html import escape
import json

from .book import analyze, no_book


def text(value):
    return escape(str(value), quote=True)


def fmt(value, places=4):
    return "—" if value is None else f"{Decimal(str(value)):,.{places}f}"


def clock(timestamp):
    return datetime.fromtimestamp(int(timestamp) / 1000, timezone.utc).strftime("%H:%M:%S.%f")[:-3]


def levels(book, side):
    total = Decimal(0)
    result = []
    for price, size in book[side]:
        price, size = Decimal(price), Decimal(size)
        total += size
        result.append((price, size, total))
    return result


def depth_svg(bids, asks):
    all_levels = bids + asks
    if not all_levels:
        return '<div class="terminal-empty">暂无档位，等待订单簿更新。</div>'
    low = min(p for p, _, _ in all_levels)
    high = max(p for p, _, _ in all_levels)
    span = high - low or Decimal(1)
    ceiling = max(c for _, _, c in all_levels) or Decimal(1)
    parts = ['<svg class="depth-svg" viewBox="0 0 1000 150" role="img" aria-label="买卖盘累计深度，横轴价格，纵轴累计份额">']
    for y in (30, 65, 100):
        parts.append(f'<path d="M8 {y} H992" class="depth-grid"/>')
    for rows, color in ((bids, "#00f59b"), (asks, "#ffb3b5")):
        if not rows:
            continue
        points = [(8 + float((p-low)/span)*984, 120-float(c/ceiling)*110) for p, _, c in sorted(rows)]
        path = f'M{points[0][0]:.2f} {points[0][1]:.2f}'
        for x, y in points[1:]:
            path += f' H{x:.2f} V{y:.2f}'
        parts.append(f'<path d="{path} V120 H{points[0][0]:.2f} Z" fill="{color}" fill-opacity=".16"/>')
        parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2"/>')
        for x, y in (points[0], points[-1]):
            parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2" fill="{color}"/>')
    parts.append(f'<text x="8" y="143">{fmt(low)}</text><text x="992" y="143" text-anchor="end">{fmt(high)} PRICE</text></svg>')
    return ''.join(parts)


def render_book(book, market, outcome, depth, limit, age=None, metadata=None):
    display = no_book(book) if outcome["indexSet"] == 2 else book
    outcome_name = outcome["name"]
    m = analyze(display, depth)
    bids, asks = levels(display, "bids"), levels(display, "asks")
    total = m['bid_depth'] + m['ask_depth']
    cards = []
    def card(label, value, detail, tone="", ratio=None):
        bar = '' if ratio is None else f'<div class="meter"><i style="width:{ratio*100:.2f}%"></i></div>'
        cards.append(f'<div class="telemetry {tone}"><label>{label}</label><strong>{text(value)}</strong><small>{text(detail)}</small>{bar}</div>')
    card('BEST BID', fmt(m['best_bid']), f'{outcome_name} · {fmt(bids[0][1], 2) if bids else "—"} SH', 'bid')
    card('BEST ASK', fmt(m['best_ask']), f'{outcome_name} · {fmt(asks[0][1], 2) if asks else "—"} SH', 'ask')
    card('SPREAD', fmt(m['spread']), '最优卖价 − 最优买价', 'cyan')
    card('MIDPOINT [L2]', fmt(m['mid_price']), '最优报价中间价')
    card(f'BID DEPTH N={depth}', fmt(m['bid_depth'], 2), 'SHARES / 买方份额', 'bid', m['bid_depth']/total if total else 0)
    card(f'ASK DEPTH N={depth}', fmt(m['ask_depth'], 2), 'SHARES / 卖方份额', 'ask', m['ask_depth']/total if total else 0)
    imbalance = m['imbalance']
    card('ORDER IMBALANCE', '—' if imbalance is None else f'{imbalance:+.3f}', '买卖深度归一化差值', 'bid' if imbalance is not None and imbalance >= 0 else 'ask')
    if age is not None:
        card('DATA AGE', f'{age:.1f}s', '距本地最后接收', 'cyan')
    shown_bids, shown_asks = bids[:limit], asks[:limit]
    max_depth = max([c for _, _, c in shown_bids + shown_asks], default=Decimal(1)) or Decimal(1)
    def row(values, side, best):
        p, size, cumulative = values
        badge = '<em>BEST</em>' if best else ''
        width = float(cumulative / max_depth) * 100
        return (f'<tr class="{side}{" best" if best else ""}" style="--depth:{width:.3f}%">'
                f'<td>{fmt(p)} {badge}</td><td>{fmt(size, 2)}</td><td>{fmt(cumulative, 2)}</td><td>{fmt(p*100, 2)}%</td></tr>')
    ladder = ''.join(row(v, 'ask', i == 0) for i, v in reversed(list(enumerate(shown_asks))))
    ladder += f'<tr class="spread-row"><td colspan="4">SPREAD: <b>{fmt(m["spread"])}</b><span> MID: {fmt(m["mid_price"])} · {text(outcome_name)} PERSPECTIVE</span></td></tr>'
    ladder += ''.join(row(v, 'bid', i == 0) for i, v in enumerate(shown_bids))
    if not all((bids, asks)):
        ladder += '<tr><td colspan="4" class="terminal-empty">部分或全部盘口为空；缺失报价显示为 —。</td></tr>'
    metadata = metadata or {}
    title = metadata.get('title') or metadata.get('question') or f'Market #{market}'
    question = metadata.get('question') or ''
    status = metadata.get('tradingStatus') or metadata.get('status') or '—'
    return (f'<div class="terminal"><section class="telemetry-strip">{"".join(cards)}</section>'
            f'<section class="market-banner"><div><h2>{text(title)}</h2><small>#{text(market)} · {text(question)}</small>'
            f'<br><small>存储的市场状态 / {text(status)}</small></div>'
            f'<span class="perspective">{text(outcome_name)} 视角</span>'
            f'<small class="outcome-token">onChainId: {text(outcome.get("onChainId") or "未提供")}</small>'
            f'<small>价格显示 4 位小数 · 原始精度见 Raw Inspector</small></section>'
            f'<section class="terminal-panel"><div class="panel-label">CUMULATIVE DEPTH PROFILE (BIDS VS ASKS)'
            f'<span class="bid"> Σ Bids: {fmt(bids[-1][2], 2) if bids else "0"}</span>'
            f'<span class="ask"> Σ Asks: {fmt(asks[-1][2], 2) if asks else "0"}</span></div>{depth_svg(bids, asks)}</section>'
            f'<section class="terminal-panel"><h3><span class="bid">▤</span> L2 Orderbook Ladder <small>累计份额 / {text(outcome_name)}</small></h3>'
            f'<table class="ladder"><thead><tr><th>PRICE</th><th>SIZE (SHARES)</th><th>CUMULATIVE</th><th>IMPLIED %</th></tr></thead><tbody>{ladder}</tbody></table>'
            f'<div class="panel-foot">LADDER DOCK <span class="bid">每侧前 {limit} 档</span><span>深度指标前 {depth} 档 · 深度图含全部档位</span></div></section></div>')


def render_deltas(entries, outcome):
    rows = []
    for entry_id, fields in entries:
        payload = json.loads(fields['data'])
        for side in ('bids', 'asks'):
            for price, size in payload['changes'][side].items():
                price, size = Decimal(price), Decimal(size)
                shown_side = side
                if outcome['indexSet'] == 2:
                    price = 1 - price
                    shown_side = 'asks' if side == 'bids' else 'bids'
                tone = 'bid' if shown_side == 'bids' else 'ask'
                action = 'DELETE' if size == 0 else 'SET'
                rows.append(f'<tr><td>{clock(payload["timestamp_ms"])} <span class="cyan">{text(entry_id)}</span></td>'
                            f'<td><em class="action-{action.lower()}">{action}</em></td><td class="{tone}">{tone.upper()}</td>'
                            f'<td>{fmt(price)}</td><td class="{tone}">{fmt(size, 2)}</td></tr>')
    body = ''.join(rows) or '<tr><td colspan="5" class="terminal-empty">最近记录中暂无档位变更，等待下一次更新。</td></tr>'
    return (f'<section class="terminal terminal-panel stream-panel"><h3><span class="bid">※</span> Redis Stream <small class="bid">:deltas</small></h3>'
            f'<div class="panel-label">最近 {len(entries)} 条消息 · {len(rows)} 个档位变更 · {text(outcome["name"])} 视角</div>'
            '<div class="stream-scroll"><table class="delta-table"><thead><tr><th>UTC / STREAM ID</th><th>ACTION</th><th>SIDE</th><th>PRICE</th><th>SIZE</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div><div class="panel-foot">SET = 设置绝对份额；DELETE = 删除档位。非成交量，非数量差值。</div></section>')
