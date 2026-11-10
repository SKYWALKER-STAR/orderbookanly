"""Read-only depth analytics using live books and sampled historical books."""
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from statistics import pstdev

import pandas as pd
import streamlit as st

from .book import analyze, no_book
from .storage import RedisStore, SnapshotStore, redis_client, clickhouse_client
from .terminal import text, fmt, depth_svg, levels

log = logging.getLogger(__name__)


def panel(title, body):
    st.html(f'<section class="terminal quant-panel"><h3>{title}</h3>{body}</section>')


def stat(label, value, tone="bid"):
    return f'<div><small>{text(label)}</small><strong class="{tone}">{text(value)}</strong></div>'


def chart(frame, field, height, signed=False):
    if frame.empty or frame[field].notna().sum() == 0:
        st.info("窗口内暂无有效历史数据，请启动 snapshot 或扩大时间窗口。")
        return
    encoding = {
        "x": {"field": "time", "type": "temporal", "title": "UTC", "scale": {"type": "utc"}},
        "y": {"field": field, "type": "quantitative", "title": "ODI" if signed else "价差（价格单位）",
              "scale": {"domain": [-1, 1]} if signed else {"zero": False}},
        "tooltip": [{"field": "time", "type": "temporal"}, {"field": field, "format": ".4f"}],
    }
    layers = []
    if signed:
        # Clip the same line at zero: colors do not interpolate across missing rows.
        for sign, color in ((1, "#00f59b"), (-1, "#ffb3b5")):
            layers.append({"transform": [{"calculate": f"isValid(datum.{field}) ? {'max' if sign == 1 else 'min'}(datum.{field}, 0) : null", "as": "part"}],
                           "mark": {"type": "area", "color": color, "opacity": .35},
                           "encoding": {**encoding, "y": {**encoding["y"], "field": "part"}}})
        layers.append({"mark": {"type": "rule", "color": "#849588", "strokeDash": [3, 4]},
                       "encoding": {"y": {"datum": 0}}})
    layers.append({"mark": {"type": "line", "color": "#00f59b" if signed else "#7fe5ff", "point": len(frame) == 1},
                   "encoding": encoding})
    st.vega_lite_chart(frame, {"height": height, "background": "#0a0e16", "layer": layers,
                              "config": {"view": {"stroke": None}, "axis": {"gridColor": "#1c2028"}}},
                       use_container_width=True)


def render(cfg, market, outcome, catalog):
    """Called within the dashboard refresh fragment; controls affect only this view."""
    left, right = st.columns([2, 1])
    with right:
        with st.container(border=True):
            st.markdown("### QUANT CONTROLLER")
            depth = st.radio("分析档位 N", [5, 10, 20, 50], index=1, horizontal=True, key="quant_depth")
            window = st.selectbox("历史窗口", [300, 900, 3600], format_func=lambda s: f"{s // 60} 分钟", key="quant_window")
            st.caption(f"ACTIVE TARGET / #{market} · {outcome['name']}")
            st.caption("仅调整本页计算，不修改采集配置或历史数据。")
    books, errors = {}, []
    client = None
    try:
        client = redis_client(cfg)
        store = RedisStore(client, cfg)
        for market_id in catalog:
            latest = store.read(market_id)
            if latest:
                books[market_id] = latest['book']
    except Exception as exc:
        log.warning("Microstructure Redis read failed (%s)", type(exc).__name__)
        errors.append("Redis 读取失败；部分实时指标不可用。")
    finally:
        if client is not None:
            client.close()
    raw_book = books.get(market)
    convert = lambda book: no_book(book) if outcome['indexSet'] == 2 else book
    book = convert(raw_book) if raw_book else {"bids": [], "asks": []}
    metrics = analyze(book, depth)
    odi = metrics['imbalance']
    age = (time.time()*1000-raw_book['received_timestamp_ms'])/1000 if raw_book else None
    with left:
        gauge = 50 if odi is None else (odi+1)*50
        panel('ORDERBOOK DEPTH IMBALANCE (ODI) <small>L2 MICROSTRUCTURE</small>',
              f'<div class="panel-label">刷新 {cfg.getfloat("visualization", "refresh_seconds"):g}s · 数据年龄 {fmt(age, 1)}s · {text(outcome["name"])}</div>'
              '<div class="odi-formula"><span>Imbalance = </span><div><span class="bid">Bid Depth(N)</span> − <span class="ask">Ask Depth(N)</span><hr>'
              '<span class="bid">Bid Depth(N)</span> + <span class="ask">Ask Depth(N)</span></div></div>'
              f'<div class="odi-reading">CURRENT ODI <b class="{"bid" if odi is not None and odi >= 0 else "ask"}">{"—" if odi is None else f"{odi:+.3f}"}</b></div>'
              f'<div class="odi-gauge"><i style="left:{gauge:.2f}%"></i></div><div class="odi-labels"><span>−1 ASK</span><span>0 NEUTRAL</span><span>+1 BID</span></div>'
              '<div class="quant-stats">' + stat(f'TOP-{depth} BID', fmt(metrics['bid_depth'], 2) if raw_book else '—')
              + stat(f'TOP-{depth} ASK', fmt(metrics['ask_depth'], 2) if raw_book else '—', 'ask')
              + stat('NET DEPTH / SHARES', fmt(metrics['bid_depth']-metrics['ask_depth'], 2) if raw_book else '—') + '</div>')
    if age is not None and age > cfg.getfloat('visualization', 'stale_seconds'):
        st.warning("当前盘口已陈旧，以下实时指标基于最后保存的订单簿。")
    if raw_book is None:
        st.info("当前市场尚无实时订单簿，历史分析仍可查看。")
    history, client = [], None
    limit = cfg.getint('visualization', 'history_limit')
    try:
        client = clickhouse_client(cfg)
        history = list(SnapshotStore(client, cfg).history_window(
            market, datetime.now(timezone.utc)-timedelta(seconds=window), limit))
    except Exception as exc:
        log.warning("Microstructure history read failed (%s)", type(exc).__name__)
        errors.append("ClickHouse 历史读取失败；时间序列和价差波动不可用。")
    finally:
        if client is not None:
            client.close()
    for error in errors:
        st.warning(error)
    samples = []
    for row in sorted(history, key=lambda r: r['snapshot_at']):
        historical = convert({'bids': json.loads(row['bids']), 'asks': json.loads(row['asks'])})
        m = analyze(historical, depth)
        samples.append({'time': row['snapshot_at'], 'odi': m['imbalance'], 'spread': m['spread']})
    frame = pd.DataFrame(samples, columns=['time', 'odi', 'spread'])
    if not frame.empty:
        frame['time'] = pd.to_datetime(frame['time'], utc=True)
    main, side = st.columns([2, 1])
    with main:
        with st.container(border=True):
            st.markdown("### HIGH-PRECISION DEPTH IMBALANCE TIME SERIES")
            st.caption(f"最近 {window // 60} 分钟 · {len(frame)} 个 ClickHouse 快照 · 按 N={depth} 重新计算 · 不是逐笔 tick")
            if len(history) == limit:
                st.caption(f"达到查询上限 {limit}，仅展示窗口内最近样本。")
            chart(frame, 'odi', 310, signed=True)
            st.caption("绿色：买方深度占优 · 粉色：卖方深度占优 · 空盘口的 ODI 不定义")
    with side:
        bids, asks = levels(book, 'bids'), levels(book, 'asks')
        panel('CUMULATIVE DEPTH CURVE',
              f'<div class="panel-label">全部档位 · MID PRICE {fmt(metrics["mid_price"])}</div><div class="quant-depth">{depth_svg(bids, asks)}</div>'
              '<div class="quant-stats">'+stat('Σ BID / SHARES', fmt(bids[-1][2], 2) if bids else '—')
              +stat('Σ ASK / SHARES', fmt(asks[-1][2], 2) if asks else '—', 'ask')+'</div>')
    a, b, c = st.columns(3)
    spreads = [x['spread'] for x in samples if x['spread'] is not None]
    with a:
        with st.container(border=True):
            st.markdown('### SPREAD VOLATILITY')
            st.metric('窗口内价差标准差 / 价格单位', fmt(pstdev(spreads)) if len(spreads) >= 2 else '—')
            chart(frame, 'spread', 100)
            st.caption(f'{len(spreads)} 个有效快照 · 总体标准差，至少需要 2 个样本')
    with b:
        sizes = [float(q) for s in ('bids', 'asks') for _, q in book[s][:depth]]
        total = sum(sizes)
        hhi = sum((q/total)**2 for q in sizes) if total else None
        top3 = sum(float(q) for s in ('bids', 'asks') for _, q in book[s][:min(3, depth)]) / total if total else None
        panel('CONCENTRATION INDEX', '<div class="quant-big bid">'+fmt(hhi, 3)+'</div>'
              f'<div class="panel-label">HHI · 双侧前 {depth} 档，按份额权重平方和</div>'
              f'<div class="quant-stats">{stat("每侧前 3 档 / 所选深度", "—" if top3 is None else f"{top3:.1%}")}</div>'
              '<p>集中度描述档位分布，不代表交易风险评级。</p>')
    with c:
        panel('REPLENISHMENT RATE', '<div class="quant-big">—</div><div class="panel-label">数据暂不支持</div>'
              '<p>尚无逐笔成交、订单身份及撤单原因，无法识别成交后的补单时延。</p>'
              '<div class="panel-foot">存活率 / 欺骗挂单检测：不可计算</div>')
    with st.container(border=True):
        st.markdown('### CROSS-MARKET MICROSTRUCTURE MATRIX')
        st.caption(f'配置市场 · 各市场原始盘口 indexSet=1 · 前 {depth} 档 · 时间差包含两端时钟偏差')
        matrix = []
        for market_id, (meta_row, _) in catalog.items():
            raw = books.get(market_id)
            meta = meta_row['metadata'] if meta_row else {}
            source = next((o.get('name') for o in meta.get('outcomes', []) if str(o.get('indexSet')) == '1'), '未同步')
            m = analyze(raw, depth) if raw else None
            matrix.append({'Market ID': market_id, 'Title': meta.get('title', '未同步'), 'Outcome': source,
                           'Bid': m['best_bid'] if m else None, 'Ask': m['best_ask'] if m else None,
                           'Spread / Mid': m['spread']/m['mid_price'] if m and m['mid_price'] else None,
                           'Depth (SH)': m['bid_depth']+m['ask_depth'] if m else None,
                           'ODI': m['imbalance'] if m else None,
                           '源→接收 (ms)': raw['received_timestamp_ms']-raw['source_timestamp_ms'] if raw else None,
                           '数据年龄 (s)': (time.time()*1000-raw['received_timestamp_ms'])/1000 if raw else None})
        st.dataframe(pd.DataFrame(matrix), hide_index=True, use_container_width=True)
    panel('QUANT_NOTE / ORDERBOOK MICROSTRUCTURE',
          '<p>以上指标描述未成交挂单的份额分布，不是成交量或主动买卖流。历史曲线来自定时快照，可能重复采样或存在缺口，不能代表 50ms 逐笔行情。'
          'HHI 使用所选双侧档位的份额权重；价差标准差使用窗口内有效快照。切换顶部市场可查看对应分析。</p>')
