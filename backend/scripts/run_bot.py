#!/usr/bin/env python3
"""Arranca el bot TORI (escucha continua)."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.bot.__main__ import main

if __name__ == "__main__":
    main()
