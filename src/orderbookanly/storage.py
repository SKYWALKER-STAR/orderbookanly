"""Redis atomic delta updates and ClickHouse append-only snapshots."""
import json
from datetime import datetime, timezone

from .book import analyze, diff
from .config import secret


def redis_client(cfg):
    import redis
    return redis.Redis.from_url(cfg["redis"]["url"], decode_responses=True,
                               socket_connect_timeout=5, socket_timeout=5)


class RedisStore:
    def __init__(self, client, cfg):
        self.client = client
        self.prefix = cfg["redis"]["prefix"]
        self.maxlen = cfg.getint("redis", "stream_maxlen")
        self.depth = cfg.getint("analysis", "depth_levels")

    def key(self, market):
        return f"{self.prefix}:orderbook:{market}"

    def read(self, market):
        raw = self.client.get(self.key(market) + ":latest")
        return json.loads(raw) if raw else None

    def update(self, book, raw):
        from redis.exceptions import WatchError
        base = self.key(book["market_id"])
        # WATCH handles another writer/restart without an in-memory baseline.
        while True:
            with self.client.pipeline() as pipe:
                try:
                    pipe.watch(base + ":latest")
                    previous = pipe.get(base + ":latest")
                    previous = json.loads(previous)["book"] if previous else {}
                    if previous and book["source_timestamp_ms"] < previous["source_timestamp_ms"]:
                        return False
                    delta = diff(previous, book)
                    latest = {"book": book, "analysis": analyze(book, self.depth), "raw": raw}
                    pipe.multi()
                    for side in ("bids", "asks"):
                        updates = {p: q for p, q in delta[side].items() if q != "0"}
                        deletes = [p for p, q in delta[side].items() if q == "0"]
                        if updates:
                            pipe.hset(base + ":" + side, mapping=updates)
                        if deletes:
                            pipe.hdel(base + ":" + side, *deletes)
                    pipe.set(base + ":latest", json.dumps(latest))
                    pipe.xadd(base + ":deltas", {"data": json.dumps({
                        "timestamp_ms": book["source_timestamp_ms"], "changes": delta})},
                        maxlen=self.maxlen, approximate=True)
                    pipe.execute()
                    return True
                except WatchError:
                    continue


def clickhouse_client(cfg):
    import clickhouse_connect
    return clickhouse_connect.get_client(
        host=cfg["clickhouse"]["host"], port=cfg.getint("clickhouse", "port"),
        username=cfg["clickhouse"]["username"], password=secret(cfg, "clickhouse", "password_env"),
        secure=cfg.getboolean("clickhouse", "secure"), connect_timeout=5, send_receive_timeout=15)


class MarketMetadataStore:
    """Append-only metadata revisions; readers explicitly select the latest."""

    def __init__(self, client, cfg):
        self.client = client
        self.database = cfg["clickhouse"]["database"]
        self.table = f"{self.database}.market_metadata"

    def initialize(self):
        self.client.command(f"CREATE DATABASE IF NOT EXISTS {self.database}")
        self.client.command(f"""CREATE TABLE IF NOT EXISTS {self.table} (
            market_id String, fetched_at DateTime64(6, 'UTC'),
            title String, question String, description String,
            trading_status String, status String, metadata_json String
        ) ENGINE = MergeTree PARTITION BY toYYYYMM(fetched_at)
        ORDER BY (market_id, fetched_at)""")

    def write(self, market, metadata):
        if str(metadata.get("id")) != str(market):
            raise ValueError("Market metadata ID mismatch")
        names = ["market_id", "fetched_at", "title", "question", "description",
                 "trading_status", "status", "metadata_json"]
        row = [str(market), datetime.now(timezone.utc)]
        row.extend(str(metadata.get(key) or "") for key in
                   ("title", "question", "description", "tradingStatus", "status"))
        row.append(json.dumps(metadata, ensure_ascii=False))
        self.client.insert(self.table, [row], column_names=names)

    def latest(self, market):
        rows = list(self.client.query(
            f"SELECT * FROM {self.table} WHERE market_id = {{market:String}} "
            "ORDER BY fetched_at DESC LIMIT 1", parameters={"market": str(market)}
        ).named_results())
        if not rows:
            return None
        row = dict(rows[0])
        row["metadata"] = json.loads(row.pop("metadata_json"))
        return row


class SnapshotStore:
    def __init__(self, client, cfg):
        self.client = client
        self.database = cfg["clickhouse"]["database"]
        self.table = f"{self.database}.snapshots"

    def initialize(self):
        self.client.command(f"CREATE DATABASE IF NOT EXISTS {self.database}")
        self.client.command(f"""CREATE TABLE IF NOT EXISTS {self.table} (
            market_id String, snapshot_at DateTime64(3, 'UTC'),
            source_timestamp_ms UInt64, received_timestamp_ms UInt64,
            bids String, asks String, raw String, analysis String,
            best_bid Nullable(Float64), best_ask Nullable(Float64),
            spread Nullable(Float64), mid_price Nullable(Float64),
            bid_depth Float64, ask_depth Float64, imbalance Nullable(Float64)
        ) ENGINE = MergeTree PARTITION BY toYYYYMM(snapshot_at)
        ORDER BY (market_id, snapshot_at)""")

    def write(self, latest):
        book, metrics = latest["book"], latest["analysis"]
        names = ["market_id", "snapshot_at", "source_timestamp_ms", "received_timestamp_ms",
                 "bids", "asks", "raw", "analysis", "best_bid", "best_ask", "spread",
                 "mid_price", "bid_depth", "ask_depth", "imbalance"]
        row = [book["market_id"], datetime.now(timezone.utc), book["source_timestamp_ms"],
               book["received_timestamp_ms"], json.dumps(book["bids"]), json.dumps(book["asks"]),
               latest["raw"], json.dumps(metrics)]
        row.extend(metrics[name] for name in names[8:])
        self.client.insert(self.table, [row], column_names=names)

    def history(self, market, limit):
        return self.client.query(f"SELECT * FROM {self.table} WHERE market_id = {{market:String}} "
                                 "ORDER BY snapshot_at DESC LIMIT {limit:UInt32}",
                                 parameters={"market": market, "limit": limit}).named_results()

    def history_window(self, market, since, limit):
        return self.client.query(
            f"SELECT snapshot_at, bids, asks FROM {self.table} "
            "WHERE market_id = {market:String} AND snapshot_at >= {since:DateTime64(3)} "
            "ORDER BY snapshot_at DESC LIMIT {limit:UInt32}",
            parameters={"market": market, "since": since, "limit": limit}).named_results()
