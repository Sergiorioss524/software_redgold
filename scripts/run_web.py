#!/usr/bin/env python3
"""Launch the local web dashboard.

Usage:
    python scripts/run_web.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redgold.webapp import app

if __name__ == "__main__":
    app.run(debug=True)
