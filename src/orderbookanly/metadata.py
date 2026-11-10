"""Fetch and persist market descriptions before opening the order-book stream."""
import json
import logging
from urllib.request import Request, urlopen

from .config import market_ids
from .storage import MarketMetadataStore, clickhouse_client

log = logging.getLogger(__name__)


def fetch_market(market, api_key):
    if not str(market).isdigit():
        raise ValueError("Market ID must be numeric")
    request = Request(f"https://api.predict.fun/v1/markets/{market}?includeStats=true",
                      headers={"x-api-key": api_key, "Accept": "application/json"})
    with urlopen(request, timeout=15) as response:
        payload = json.load(response)
        log.info(payload=payload)
    data = payload.get("data")
    print(data)
    if payload.get("success") is not True or not isinstance(data, dict):
        raise ValueError("Invalid market metadata response")
    if str(data.get("id")) != str(market):
        raise ValueError("Market metadata ID mismatch")
    return data


def sync_markets(cfg, api_key):
    client = clickhouse_client(cfg)
    try:
        store = MarketMetadataStore(client, cfg)
        store.initialize()
        for market in market_ids(cfg):
            try:
                store.write(market, fetch_market(market, api_key))
            except Exception as exc:
                # Do not surface HTTP headers, credentials or response payloads.
                raise RuntimeError(f"Market metadata sync failed for {market} ({type(exc).__name__})") from None
            log.info("Market metadata saved market=%s", market)
    finally:
        client.close()
