"""Compatibility entry point; configuration replaces embedded credentials."""
import sys
from orderbookanly.cli import main

if __name__ == "__main__":
    main(["collect", *sys.argv[1:]])
