"""Exercise the actual Streamlit script with representative stored data."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from orderbookanly.book import analyze, normalize


def test_dashboard_live_and_history():
    root = Path(__file__).resolve().parents[1]
    book = normalize({"marketId": 3862211, "updateTimestampMs": 100,
                      "bids": [[0.4, 10]], "asks": [[0.6, 20]]}, "3862211")
    metrics = analyze(book)
    latest = {"book": book, "analysis": metrics, "raw": "{}"}
    row = {"snapshot_at": datetime.now(timezone.utc), "market_id": "3862211",
           "bids": json.dumps(book["bids"]), "asks": json.dumps(book["asks"]),
           "raw": "{}", "analysis": json.dumps(metrics), **metrics}
    redis = MagicMock()
    redis.get.return_value = json.dumps(latest)
    redis.xrevrange.return_value = []
    with patch.object(sys, "argv", ["dashboard.py", "--config", str(root / "config/example.conf")]), \
         patch("orderbookanly.storage.redis_client", return_value=redis), \
         patch("orderbookanly.storage.clickhouse_client", return_value=MagicMock()), \
         patch("orderbookanly.storage.SnapshotStore.history", return_value=[row]):
        app = AppTest.from_file(str(root / "src/orderbookanly/dashboard.py")).run(timeout=20)
        assert not app.exception
        assert not app.error
        assert len(app.dataframe) == 5
        assert app.metric[0].value == "0.4000"
        assert app.metric[4].value == "-33.33%"
        app.sidebar.radio[0].set_value("No").run()
        assert not app.exception
        assert not app.error
        assert app.metric[4].value == "+33.33%"


@pytest.mark.parametrize("bids,asks,expected", [([], [], "—"), ([[0.4, 10]], [], "+100.00%")])
def test_dashboard_empty_and_one_sided_book(bids, asks, expected):
    root = Path(__file__).resolve().parents[1]
    book = normalize({"marketId": 3862211, "updateTimestampMs": 100,
                      "bids": bids, "asks": asks}, "3862211")
    redis = MagicMock()
    redis.get.return_value = json.dumps({"book": book})
    redis.xrevrange.return_value = []
    with patch.object(sys, "argv", ["dashboard.py", "--config", str(root / "config/example.conf")]), \
         patch("orderbookanly.storage.redis_client", return_value=redis), \
         patch("orderbookanly.storage.clickhouse_client", return_value=MagicMock()), \
         patch("orderbookanly.storage.SnapshotStore.history", return_value=[]):
        app = AppTest.from_file(str(root / "src/orderbookanly/dashboard.py")).run(timeout=20)
        assert not app.exception
        assert not app.error
        assert app.metric[2].value == "—"
        assert app.metric[4].value == expected
