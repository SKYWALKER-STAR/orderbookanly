"""Read-only UI: run with streamlit run, independently of workers."""
import argparse
import json
import logging
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from orderbookanly.book import analyze, no_book
from orderbookanly.config import load_config, market_ids, setup_logging
from orderbookanly.storage import RedisStore, SnapshotStore, clickhouse_client, redis_client

parser = argparse.ArgumentParser()
parser.add_argument("--config", default="config/local.conf")
args = parser.parse_args()
cfg = load_config(args.config)
setup_logging(cfg)
log = logging.getLogger(__name__)
st.set_page_config(page_title="Predict.fun 订单簿", layout="wide")
st.html(Path(__file__).with_name("dashboard.css").read_text(encoding="utf-8"))
st.caption("PREDICT.FUN / MARKET INTELLIGENCE")
st.title("订单簿分析终端")
st.caption("实时深度 · 市场微观结构 · 历史快照")
st.sidebar.markdown("### 控制台")
market = st.sidebar.selectbox("市场", market_ids(cfg))
outcome = st.sidebar.radio("Outcome", ["Yes", "No"])
visible_levels = st.sidebar.slider("显示档位", 5, 50, 20, 5)
st.sidebar.caption("No 由 Yes 的价格取补数、买卖方向互换得出。时间使用 UTC。")
st.sidebar.divider()
st.sidebar.caption(f"自动刷新 / {cfg.getfloat('visualization', 'refresh_seconds'):g} 秒")
st.sidebar.caption("只读分析 · 不执行交易")


def number(value, digits=4):
    return "—" if value is None else f"{value:,.{digits}f}"


def show_book(book):
    display = no_book(book) if outcome == "No" else book
    metrics = analyze(display, cfg.getint("analysis", "depth_levels"))
    st.caption(f"{outcome.upper()} / MARKET {market}")
    for column, (label, key) in zip(st.columns(4), [
        ("最优买价 / BID", "best_bid"), ("最优卖价 / ASK", "best_ask"),
        ("价差 / SPREAD", "spread"), ("中间价 / MID", "mid_price"),
    ]):
        column.metric(label, number(metrics[key]))
    frames = []
    for side in ("bids", "asks"):
        frame = pd.DataFrame(display[side], columns=["price", "shares"]).astype(float)
        frame["side"] = side
        frame["cumulative_shares"] = frame["shares"].cumsum()
        frames.append(frame)
    levels = pd.concat(frames, ignore_index=True)
    chart, summary = st.columns([3, 1])
    with chart:
        st.subheader("累计深度")
        st.caption("全部档位 · 横轴为价格，纵轴为累计份额")
        if not levels.empty:
            st.vega_lite_chart(levels, {
                "height": 280,
                "mark": {"type": "area", "interpolate": "step-after", "opacity": 0.3, "line": True},
                "encoding": {
                    "x": {"field": "price", "type": "quantitative", "title": "价格", "scale": {"zero": False}},
                    "y": {"field": "cumulative_shares", "type": "quantitative", "title": "累计份额", "stack": None},
                    "color": {"field": "side", "type": "nominal", "scale": {"domain": ["bids", "asks"], "range": ["#36c99a", "#ef7185"]}, "legend": {"title": None}},
                    "tooltip": [{"field": "side"}, {"field": "price", "format": ".4f"}, {"field": "cumulative_shares", "format": ",.2f"}],
                },
            }, use_container_width=True)
        else:
            st.info("当前订单簿为空，等待市场更新后显示深度。")
    with summary:
        st.subheader("深度不平衡")
        st.caption(f"前 {metrics['depth_levels']} 档 · 份额")
        imbalance = metrics["imbalance"]
        st.metric("IMBALANCE", "—" if imbalance is None else f"{imbalance:+.2%}")
        st.metric("买方深度", number(metrics["bid_depth"], 2))
        st.metric("卖方深度", number(metrics["ask_depth"], 2))
        st.caption("正值偏买方，负值偏卖方；不代表成交订单流。")
    st.subheader("买卖盘档位")
    st.caption(f"每侧最多显示 {visible_levels} 档 · 累计份额从最优报价向外计算")
    for column, frame, label, color in zip(st.columns(2), frames, ["BID / 买盘", "ASK / 卖盘"], ["#36c99a", "#ef7185"]):
        with column:
            st.markdown(f"**{label}**")
            table = frame.head(visible_levels).drop(columns="side").rename(columns={
                "price": "价格", "shares": "份额", "cumulative_shares": "累计份额"})
            styled = table.style.format({"价格": "{:.4f}", "份额": "{:,.2f}", "累计份额": "{:,.2f}"})
            if not table.empty:
                styled = styled.bar(subset=["累计份额"], color=color, vmin=0)
            st.dataframe(styled, hide_index=True, use_container_width=True)


@st.fragment(run_every=cfg.getfloat("visualization", "refresh_seconds"))
def live():
    st.subheader("Redis 实时订单簿")
    client = None
    try:
        client = redis_client(cfg)
        store = RedisStore(client, cfg)
        latest = store.read(market)
        if latest is None:
            st.info("暂无数据，请启动 collect 进程。")
            return
        book = latest["book"]
        age = (time.time() * 1000 - book["received_timestamp_ms"]) / 1000
        st.caption(f"数据年龄 / {age:.1f} 秒 · 源时间 / {pd.to_datetime(book['source_timestamp_ms'], unit='ms', utc=True)}")
        if age > cfg.getfloat("visualization", "stale_seconds"):
            st.warning("订单簿长时间未更新，可能市场没有变化或采集已断开。")
        show_book(book)
        with st.expander("原始消息与已存储分析"):
            st.json(latest)
        with st.expander("最近 20 条增量（0 表示删除档位）"):
            st.json(client.xrevrange(store.key(market) + ":deltas", count=20))
    except Exception as exc:
        log.error("Redis view failed (%s)", type(exc).__name__)
        st.error("Redis 读取失败，请检查服务与配置。")
    finally:
        if client is not None:
            client.close()


@st.fragment(run_every=cfg.getfloat("visualization", "refresh_seconds"))
def history():
    st.subheader("ClickHouse 历史快照（Yes 指标）")
    client = None
    try:
        client = clickhouse_client(cfg)
        rows = list(SnapshotStore(client, cfg).history(market, cfg.getint("visualization", "history_limit")))
        if not rows:
            st.info("暂无历史快照，请启动 snapshot 进程。")
            return
        df = pd.DataFrame(rows).sort_values("snapshot_at")
        st.caption("历史趋势固定展示 Yes；下方选中快照的深度与档位跟随侧栏 Outcome。")
        prices, imbalance = st.columns([2, 1])
        with prices:
            st.markdown("**最优报价与中间价**")
            st.line_chart(df.set_index("snapshot_at")[["best_bid", "best_ask", "mid_price"]], color=["#ef7185", "#36c99a", "#a8b5c7"])
        with imbalance:
            st.markdown("**深度不平衡**")
            st.line_chart(df.set_index("snapshot_at")[["imbalance"]], color=["#36c99a"])
        st.dataframe(df.drop(columns=["raw", "bids", "asks", "analysis"]), hide_index=True)
        selected = st.selectbox("查看快照（最新在前）", range(len(rows)),
                                format_func=lambda i: str(rows[i]["snapshot_at"]))
        row = rows[selected]
        show_book({"bids": json.loads(row["bids"]), "asks": json.loads(row["asks"])})
        with st.expander("快照原始数据与分析"):
            st.json({k: json.loads(row[k]) for k in ("raw", "analysis", "bids", "asks")})
    except Exception as exc:
        log.error("History view failed (%s)", type(exc).__name__)
        st.error("ClickHouse 读取失败，请检查配置并先运行 init-db。")
    finally:
        if client is not None:
            client.close()


live_tab, history_tab = st.tabs(["实时终端", "历史快照"])
with live_tab:
    live()
with history_tab:
    history()
