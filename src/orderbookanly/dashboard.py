"""Read-only UI: run with streamlit run, independently of workers."""
import argparse
import json
import logging
import time

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
st.title("Predict.fun 订单簿分析")
market = st.sidebar.selectbox("市场", market_ids(cfg))
outcome = st.sidebar.radio("Outcome", ["Yes", "No"])
st.sidebar.caption("No 由 Yes 的价格取补数、买卖方向互换得出。时间使用 UTC。")


def show_book(book):
    display = no_book(book) if outcome == "No" else book
    st.json(analyze(display, cfg.getint("analysis", "depth_levels")))
    frames = []
    for side in ("bids", "asks"):
        frame = pd.DataFrame(display[side], columns=["price", "shares"]).astype(float)
        frame["side"] = side
        frame["cumulative_shares"] = frame["shares"].cumsum()
        frames.append(frame)
    levels = pd.concat(frames, ignore_index=True)
    if not levels.empty:
        st.line_chart(levels.pivot(index="price", columns="side", values="cumulative_shares"),
                      x_label="价格", y_label="累计份额")
    st.dataframe(levels, hide_index=True)


@st.fragment(run_every=cfg.getfloat("visualization", "refresh_seconds"))
def live():
    st.subheader("Redis 实时订单簿")
    client = redis_client(cfg)
    try:
        store = RedisStore(client, cfg)
        latest = store.read(market)
        if latest is None:
            st.info("暂无数据，请启动 collect 进程。")
            return
        book = latest["book"]
        age = (time.time() * 1000 - book["received_timestamp_ms"]) / 1000
        st.caption(f"距最后接收 {age:.1f} 秒；源时间戳 {book['source_timestamp_ms']}")
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
        st.line_chart(df.set_index("snapshot_at")[["best_bid", "best_ask", "mid_price"]])
        st.line_chart(df.set_index("snapshot_at")[["imbalance"]])
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


live()
history()
