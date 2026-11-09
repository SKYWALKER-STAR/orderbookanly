"""Compatibility entry point for the Redis delta collector."""
import sys
from orderbookanly.cli import main

if __name__ == "__main__":
    main(["collect", *sys.argv[1:]])
