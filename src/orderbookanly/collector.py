"""WebSocket collector. Each received order book is a complete replacement."""
import asyncio
import json
import logging
from urllib.request import Request, urlopen

from .book import normalize
from .config import market_ids, secret

log = logging.getLogger(__name__)


def rest_book(market, api_key):
    request = Request(f"https://api.predict.fun/v1/markets/{market}/orderbook",
                      headers={"x-api-key": api_key})
    with urlopen(request, timeout=10) as response:
        data = json.load(response)["data"]
    data.setdefault("marketId", int(market))
    return data


async def collect(cfg, store):
    import websockets
    api_key = secret(cfg, "predict", "api_key_env")
    if not api_key:
        raise ValueError(f"Set environment variable {cfg['predict']['api_key_env']}")
    from .metadata import sync_markets
    # Startup prerequisite, outside the WebSocket reconnect loop. A failed sync
    # aborts collection; reconnects do not repeatedly fetch the metadata.
    await asyncio.to_thread(sync_markets, cfg, api_key)
    markets = market_ids(cfg)
    delay = cfg.getfloat("predict", "reconnect_seconds")
    timeout = cfg.getfloat("predict", "receive_timeout_seconds")
    while True:
        try:
            async with websockets.connect(cfg["predict"]["websocket_url"],
                                          additional_headers={"x-api-key": api_key},
                                          open_timeout=15, max_size=16 * 1024 * 1024) as ws:
                for index, market in enumerate(markets, 1):
                    await ws.send(json.dumps({"method": "subscribe", "requestId": index,
                                              "params": [f"predictOrderbook/{market}"]}))
                log.info("Connected; subscribed markets=%s", markets)
                missing = set(markets)
                deadline = asyncio.get_running_loop().time() + 10
                while True:
                    wait = min(timeout, max(0.01, deadline - asyncio.get_running_loop().time())) if missing else timeout
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=wait)
                    except asyncio.TimeoutError:
                        if not missing:
                            raise
                        # Fallback only for markets that haven't sent an initial book.
                        for market in list(missing):
                            data = await asyncio.to_thread(rest_book, market, api_key)
                            await asyncio.to_thread(store.update, normalize(data, market),
                                                    json.dumps({"source": "rest", "data": data}))
                            missing.remove(market)
                        continue
                    message = json.loads(raw)
                    if message.get("topic") == "heartbeat":
                        await ws.send(json.dumps({"method": "heartbeat", "data": message["data"]}))
                    elif message.get("type") == "R" and not message.get("success"):
                        raise RuntimeError("Subscription rejected")
                    elif message.get("topic", "").startswith("predictOrderbook/"):
                        market = message["topic"].split("/", 1)[1]
                        if market in markets:
                            book = normalize(message["data"], market)
                            await asyncio.to_thread(store.update, book, raw)
                            missing.discard(market)
                    # Heartbeats must not indefinitely postpone the initial-book fallback.
                    if missing and asyncio.get_running_loop().time() >= deadline:
                        for market in list(missing):
                            data = await asyncio.to_thread(rest_book, market, api_key)
                            await asyncio.to_thread(store.update, normalize(data, market),
                                                    json.dumps({"source": "rest", "data": data}))
                            missing.remove(market)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Avoid logging request headers, URLs with credentials, or payloads.
            log.warning("Collector interrupted (%s); reconnecting in %.1fs", type(exc).__name__, delay)
            await asyncio.sleep(delay)
