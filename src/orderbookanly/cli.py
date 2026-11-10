import argparse
import asyncio
import logging

from .config import load_config, setup_logging
from .storage import MarketMetadataStore, RedisStore, SnapshotStore, clickhouse_client, redis_client


def main(argv=None):
    parser = argparse.ArgumentParser(description="Predict.fun order book system")
    parser.add_argument("command", choices=["collect", "snapshot", "init-db"])
    parser.add_argument("--config", default="config/local.conf")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg)
    redis = ch = None
    try:
        if args.command in {"init-db", "snapshot"}:
            ch = clickhouse_client(cfg)
            snapshots = SnapshotStore(ch, cfg)
        if args.command == "init-db":
            snapshots.initialize()
            MarketMetadataStore(ch, cfg).initialize()
        else:
            redis = redis_client(cfg)
            redis.ping()
            store = RedisStore(redis, cfg)
            if args.command == "collect":
                from .collector import collect
                asyncio.run(collect(cfg, store))
            else:
                from .snapshot import run
                run(cfg, store, snapshots)
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Stopped")
    finally:
        if redis is not None:
            redis.close()
        if ch is not None:
            ch.close()
