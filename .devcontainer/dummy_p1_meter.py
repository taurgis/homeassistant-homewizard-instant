#!/usr/bin/env python3
"""CLI wrapper for the refactored dummy P1 meter server."""

from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.dummy_p1_meter.cli import main


if __name__ == "__main__":
    main()
