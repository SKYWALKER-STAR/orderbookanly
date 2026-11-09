import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from orderbookanly.book import analyze, diff, no_book, normalize
from orderbookanly.config import load_config, setup_logging
from orderbookanly.storage import RedisStore, SnapshotStore

ROOT = Path(__file__).resolve().parents[1]


def book(bids=None, asks=None, timestamp=100):
    return normalize({"marketId": 1, "updateTimestampMs": timestamp,
                      "bids": bids or [], "asks": asks or []}, "1")


def test_full_replacement_and_decimal_keys():
    old = book([[0.4, 10], [0.3, 20]], [[0.6, 4]])
    new = book([["0.40", 15]], [])
    assert diff(old, new) == {"bids": {"0.4": "15", "0.3": "0"}, "asks": {"0.6": "0"}}
    assert no_book(old)["asks"] == [["0.6", "10"], ["0.7", "20"]]
    assert no_book(no_book(old)) == old
    assert analyze(book())["mid_price"] is None
    assert analyze(old, 1)["imbalance"] == pytest.approx(6 / 14)


@pytest.mark.parametrize("levels", [[["NaN", 1]], [[1.1, 1]], [[0.5, -1]], [[0.5, 1], ["0.50", 2]]])
def test_invalid_levels(levels):
    with pytest.raises(ValueError):
        book(levels)


def test_config(tmp_path):
    cfg = load_config(ROOT / "config/example.conf")
    assert "%(asctime)s" in cfg["logging"]["format"]
    setup_logging(cfg)
    path = tmp_path / "bad.conf"
    path.write_text((ROOT / "config/example.conf").read_text().replace("interval_seconds = 30", "interval_seconds = 0"))
    with pytest.raises(ValueError):
        load_config(path)


@pytest.fixture
def redis_connection(tmp_path):
    import redis
    if not shutil.which("redis-server"):
        pytest.skip("redis-server executable unavailable")
    path = str(tmp_path / "redis.sock")
    process = subprocess.Popen(["redis-server", "--port", "0", "--unixsocket", path,
                                "--save", "", "--appendonly", "no"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client = redis.Redis(unix_socket_path=path, decode_responses=True)
    try:
        for _ in range(100):
            try:
                if client.ping():
                    break
            except redis.ConnectionError:
                time.sleep(0.02)
        else:
            pytest.fail("Local Redis did not start")
        yield client
    finally:
        client.close()
        process.terminate()
        process.wait(timeout=5)


def test_redis_restart_deletion_stale_and_raw(redis_connection):
    cfg = load_config(ROOT / "config/example.conf")
    store = RedisStore(redis_connection, cfg)
    first = book([[0.4, 10], [0.3, 20]], [[0.6, 4]])
    assert store.update(first, '{"test": 1}')
    store = RedisStore(redis_connection, cfg)  # new collector has no in-memory state
    second = book([[0.4, 15]], timestamp=101)
    assert store.update(second, '{"test": 2}')
    assert redis_connection.hgetall(store.key("1") + ":bids") == {"0.4": "15"}
    assert redis_connection.hgetall(store.key("1") + ":asks") == {}
    assert not store.update(first, "old")
    assert store.read("1")["raw"] == '{"test": 2}'
    assert store.read("1")["analysis"]["best_ask"] is None
    assert redis_connection.xlen(store.key("1") + ":deltas") == 2
    assert store.update(book(timestamp=102), "{}")
    assert redis_connection.hgetall(store.key("1") + ":bids") == {}


def test_clickhouse_snapshot_contract():
    class Client:
        def insert(self, table, rows, column_names):
            self.table, self.row = table, dict(zip(column_names, rows[0], strict=True))
    cfg = load_config(ROOT / "config/example.conf")
    client = Client()
    current = book([[0.4, 2]], [[0.6, 3]])
    SnapshotStore(client, cfg).write({"book": current, "analysis": analyze(current), "raw": "{}"})
    assert client.table == "orderbook.snapshots"
    assert client.row["spread"] == pytest.approx(0.2)
    assert json.loads(client.row["bids"]) == [["0.4", "2"]]


def test_collector_heartbeat_subscription_and_reconnect(monkeypatch):
    import asyncio
    import websockets
    from orderbookanly.collector import collect
    cfg = load_config(ROOT / "config/example.conf")
    cfg["predict"]["market_ids"] = "1"
    cfg["predict"]["reconnect_seconds"] = "0.01"
    monkeypatch.setenv("PREDICT_API_KEY", "test-only")
    sent, saved = [], []
    connections = []

    class Socket:
        def __init__(self):
            self.messages = iter([
                {"topic": "heartbeat", "data": 123},
                {"topic": "predictOrderbook/1", "data": {
                    "marketId": 1, "updateTimestampMs": 100, "bids": [[0.4, 2]], "asks": []}}])
        async def __aenter__(self):
            connections.append(self)
            if len(connections) == 2:
                raise asyncio.CancelledError
            return self
        async def __aexit__(self, *args):
            pass
        async def send(self, raw):
            sent.append(json.loads(raw))
        async def recv(self):
            try:
                return json.dumps(next(self.messages))
            except StopIteration:
                raise ConnectionError("Disconnected")

    class Store:
        def update(self, current, raw):
            saved.append((current, raw))

    monkeypatch.setattr(websockets, "connect", lambda *args, **kwargs: Socket())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(asyncio.wait_for(collect(cfg, Store()), timeout=3))
    assert sent[0]["params"] == ["predictOrderbook/1"]
    assert sent[1] == {"method": "heartbeat", "data": 123}
    assert saved[0][0]["bids"] == [["0.4", "2"]]
    assert len(connections) == 2
