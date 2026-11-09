"""Normalize full Yes books without rounding prices; derive No and metrics."""
from decimal import Decimal
import time


def decimal_text(value):
    return format(Decimal(str(value)).normalize(), "f")


def normalize(data, market_id):
    if str(data["marketId"]) != str(market_id):
        raise ValueError("Market ID mismatch")
    result = {"market_id": str(market_id), "source_timestamp_ms": int(data["updateTimestampMs"]),
              "received_timestamp_ms": time.time_ns() // 1_000_000}
    if result["source_timestamp_ms"] < 0:
        raise ValueError("Negative timestamp")
    for side in ("bids", "asks"):
        levels = {}
        for price, size in data[side]:
            price, size = Decimal(str(price)), Decimal(str(size))
            if not price.is_finite() or not size.is_finite() or not 0 <= price <= 1 or size < 0:
                raise ValueError("Invalid price or size")
            if price in levels:
                raise ValueError("Duplicate price level")
            levels[price] = size
        result[side] = [[decimal_text(p), decimal_text(q)] for p, q in
                        sorted(levels.items(), reverse=side == "bids") if q > 0]
    return result


def no_book(book):
    return {**book, **{target: [[decimal_text(Decimal(1) - Decimal(p)), q] for p, q in book[source]]
                      for target, source in [("asks", "bids"), ("bids", "asks")]}}


def analyze(book, depth=5):
    bids, asks = book["bids"], book["asks"]
    bid = float(bids[0][0]) if bids else None
    ask = float(asks[0][0]) if asks else None
    bid_size = sum(float(q) for _, q in bids[:depth])
    ask_size = sum(float(q) for _, q in asks[:depth])
    total = bid_size + ask_size
    return {"best_bid": bid, "best_ask": ask,
            "spread": ask - bid if bid is not None and ask is not None else None,
            "mid_price": (ask + bid) / 2 if bid is not None and ask is not None else None,
            "bid_depth": bid_size, "ask_depth": ask_size,
            "imbalance": (bid_size - ask_size) / total if total else None,
            "depth_levels": depth}


def diff(old, new):
    """Absolute quantities, zero means deletion; upstream messages are full books."""
    changes = {}
    for side in ("bids", "asks"):
        before, after = dict(old.get(side, [])), dict(new[side])
        changes[side] = {p: after.get(p, "0") for p in before.keys() | after.keys()
                         if before.get(p) != after.get(p)}
    return changes
