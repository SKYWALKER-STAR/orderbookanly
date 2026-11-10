"""Read-only UI: run with streamlit run, independently of workers."""
import argparse
import json
import logging
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from orderbookanly.terminal import render_book, render_deltas
from orderbookanly.config import load_config, market_ids, setup_logging
from orderbookanly.storage import MarketMetadataStore, RedisStore, SnapshotStore, clickhouse_client, redis_client

parser = argparse.ArgumentParser()
parser.add_argument("--config", default="config/local.conf")
args = parser.parse_args()
cfg = load_config(args.config)
setup_logging(cfg)
log = logging.getLogger(__name__)
st.set_page_config(page_title="Predict.fun 订单簿", layout="wide")
st.html(Path(__file__).with_name("dashboard.css").read_text(encoding="utf-8"))
st.sidebar.markdown("### PREDICT.FUN")
st.sidebar.caption("ORDERBOOK TERMINAL")
st.sidebar.divider()
view = st.sidebar.radio("QUANTITATIVE VIEWS", ["实时监控看板", "深度与微观结构", "ClickHouse 快照"])
st.sidebar.divider()
visible_levels = st.sidebar.slider("每侧显示档位", 5, 50, 5, 5)
st.sidebar.caption("ENGINE TELEMETRY")
st.sidebar.caption(f"刷新周期 / {cfg.getfloat('visualization', 'refresh_seconds'):g}s")
st.sidebar.caption(f"分析深度 / 前 {cfg.getint('analysis', 'depth_levels')} 档")
st.sidebar.caption("MODE: READ-ONLY")


@st.cache_data(ttl=60, show_spinner=False)
def read_market_metadata(config_path, market_id):
    client = None
    try:
        settings = load_config(config_path)
        client = clickhouse_client(settings)
        return MarketMetadataStore(client, settings).latest(market_id), None
    except Exception as exc:
        log.warning("Metadata view unavailable (%s)", type(exc).__name__)
        return None, "市场元数据暂不可用，请检查 ClickHouse 并运行 collect 同步。"
    finally:
        if client is not None:
            client.close()


catalog = {market_id: read_market_metadata(str(Path(args.config).resolve()), market_id)
           for market_id in market_ids(cfg)}


def market_label(market_id):
    row, _ = catalog[market_id]
    title = (row or {}).get("metadata", {}).get("title")
    return f"{market_id} · {title or '标题未同步'}"


brand, selector, perspective = st.columns([2, 3, 2])
with brand:
    st.markdown("**PREDICT.FUN** · ORDERBOOK TERMINAL")
with selector:
    market = st.selectbox("市场", list(catalog), format_func=market_label, label_visibility="collapsed")
metadata_row, metadata_error = catalog[market]
market_metadata = metadata_row["metadata"] if metadata_row else {}
outcomes = {}
for item in market_metadata.get("outcomes") or []:
    if isinstance(item, dict) and str(item.get("indexSet")) in {"1", "2"}:
        index_set = int(item["indexSet"])
        if index_set in outcomes:
            # Ambiguous metadata must not label a book with the wrong token.
            outcomes = {}
            break
        outcomes[index_set] = {**item, "indexSet": index_set,
                               "name": str(item.get("name") or f"Outcome {index_set}（名称缺失）")}
if not outcomes:
    outcomes = {1: {"indexSet": 1, "name": "原始盘口（Outcome 未同步）", "onChainId": None}}
with perspective:
    selected_outcome = st.radio("Outcome", sorted(outcomes),
                                format_func=lambda index: outcomes[index]["name"],
                                key=f"outcome_{market}", horizontal=True)
outcome = outcomes[selected_outcome]
source_outcome_name = outcomes.get(1, {}).get("name", "原始盘口（indexSet=1）")
st.text(market_label(market))
st.caption(f"Outcome: {outcome['name']} · indexSet: {selected_outcome} · onChainId")
st.code(str(outcome.get("onChainId") or "未提供"), language=None)


def show_book(book, age=None, metadata=None):
    st.html(render_book(book, market, outcome,
                        cfg.getint("analysis", "depth_levels"), visible_levels, age,
                        market_metadata if metadata is None else metadata))


@st.fragment(run_every=cfg.getfloat("visualization", "refresh_seconds"))
def live():
    if metadata_error:
        st.caption(metadata_error)
    elif metadata_row is None:
        st.caption("尚未存储该市场元数据；下次启动 collect 时将自动获取。")
    else:
        with st.expander("市场元数据 / Market metadata"):
            st.caption(f"抓取时间 / {metadata_row['fetched_at']} UTC · 启动时快照，非实时状态 · 页面缓存 60 秒")
            st.json(metadata_row["metadata"])
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
        show_book(book, age, metadata_row["metadata"] if metadata_row else None)
        try:
            entries = client.xrevrange(store.key(market) + ":deltas", count=20)
            st.html(render_deltas(entries, outcome))
        except Exception as exc:
            log.error("Delta view failed (%s)", type(exc).__name__)
            st.warning("增量记录暂不可用，当前订单簿仍可查看。")
        st.markdown("### Raw Inspector `:latest`")
        raw = latest.get("raw", {})
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError):
                pass
        raw_text = json.dumps(raw, ensure_ascii=False, indent=2) if not isinstance(raw, str) else raw
        st.caption(f"上游原始 {source_outcome_name} 消息（indexSet=1） · {len(raw_text.encode('utf-8')):,} bytes · 不随视角转换 · 代码框右上角可复制")
        st.code(raw_text, language="json")
        with st.expander("已存储订单簿与分析"):
            st.json(latest)
        st.html('<footer class="terminal panel-foot">STORAGE / Redis + ClickHouse <span>MODE: READ-ONLY · NO EXECUTION</span></footer>')
    except Exception as exc:
        log.error("Redis view failed (%s)", type(exc).__name__)
        st.error("Redis 读取失败，请检查服务与配置。")
    finally:
        if client is not None:
            client.close()


@st.fragment(run_every=cfg.getfloat("visualization", "refresh_seconds"))
def history():
    st.subheader("ClickHouse 历史快照")
    client = None
    try:
        client = clickhouse_client(cfg)
        rows = list(SnapshotStore(client, cfg).history(market, cfg.getint("visualization", "history_limit")))
        if not rows:
            st.info("暂无历史快照，请启动 snapshot 进程。")
            return
        df = pd.DataFrame(rows).sort_values("snapshot_at")
        st.caption(f"历史趋势固定展示 {source_outcome_name}（indexSet=1）；快照深度与档位跟随顶部 Outcome。名称来自最新元数据。")
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


@st.fragment(run_every=cfg.getfloat("visualization", "refresh_seconds"))
def microstructure():
    from orderbookanly.microstructure import render
    render(cfg, market, outcome, catalog)


if view == "实时监控看板":
    live()
elif view == "深度与微观结构":
    microstructure()
else:
    history()
