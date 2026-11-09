"""Independent periodic worker; every snapshot reads one atomic Redis value."""
import logging
import time

from .config import market_ids

log = logging.getLogger(__name__)


def run(cfg, redis_store, snapshots):
    snapshots.initialize()
    interval = cfg.getfloat("snapshot", "interval_seconds")
    while True:
        start = time.monotonic()
        for market in market_ids(cfg):
            try:
                latest = redis_store.read(market)
                if latest is not None:
                    snapshots.write(latest)
                    log.info("Snapshot stored market=%s source_timestamp_ms=%s", market,
                             latest["book"]["source_timestamp_ms"])
                else:
                    log.warning("No book yet market=%s", market)
            except Exception as exc:
                log.error("Snapshot failed market=%s (%s)", market, type(exc).__name__)
        time.sleep(max(0, interval - (time.monotonic() - start)))
