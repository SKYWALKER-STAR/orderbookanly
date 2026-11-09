"""INI-style .conf loading; interpolation is disabled for logging formats."""
import configparser
import logging
import math
import os
import re
from pathlib import Path


def load_config(path):
    path = Path(path)
    if path.suffix != ".conf":
        raise ValueError("Configuration must use the .conf extension")
    cfg = configparser.ConfigParser(interpolation=None)
    with path.open(encoding="utf-8") as handle:
        cfg.read_file(handle)
    ids = market_ids(cfg)
    if not ids or any(not item.isdigit() for item in ids):
        raise ValueError("predict.market_ids must contain numeric market IDs")
    for section, option in [("predict", "reconnect_seconds"),
                            ("predict", "receive_timeout_seconds"),
                            ("snapshot", "interval_seconds"),
                            ("analysis", "depth_levels"),
                            ("redis", "stream_maxlen"),
                            ("visualization", "refresh_seconds"),
                            ("visualization", "history_limit"),
                            ("visualization", "stale_seconds")]:
        value = cfg.getfloat(section, option)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{section}.{option} must be positive")
    for section, option in [("analysis", "depth_levels"), ("redis", "stream_maxlen"),
                            ("visualization", "history_limit")]:
        cfg.getint(section, option)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", cfg["clickhouse"]["database"]):
        raise ValueError("Invalid ClickHouse database name")
    return cfg


def market_ids(cfg):
    return list(dict.fromkeys(x.strip() for x in cfg["predict"]["market_ids"].split(",") if x.strip()))


def secret(cfg, section, option):
    return os.environ.get(cfg[section][option], "")


def setup_logging(cfg):
    settings = cfg["logging"]
    level = settings.get("level", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError(f"Invalid logging level: {level}")
    handlers = [logging.StreamHandler()]
    if filename := settings.get("file", "").strip():
        from logging.handlers import RotatingFileHandler
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(filename, maxBytes=10_000_000, backupCount=3, encoding="utf-8"))
    logging.basicConfig(level=level, format=settings["format"],
                        datefmt=settings.get("date_format"), handlers=handlers, force=True)

    # websockets DEBUG logs handshake headers, including the API key.
    logging.getLogger("websockets").setLevel(logging.WARNING)
