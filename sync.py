#!/usr/bin/env python3
"""Compatibility wrapper for users who expect a sync.py script."""

import sys

from lab5_flasher.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["sync", *sys.argv[1:]]))
