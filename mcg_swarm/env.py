"""Durable .env loader — tolerates spaces, quotes, missing trailing newline."""
from __future__ import annotations
import os
import re


def load_dotenv(path: str = ".env") -> None:
    """Parse a .env file and set env vars not already in os.environ.

    Handles:
    - KEY = "value"  (spaces around =, double-quoted)
    - KEY="value"    (no spaces, double-quoted)
    - KEY='value'    (single-quoted)
    - KEY=value      (unquoted)
    - Blank lines and # comments are ignored.
    - Missing file is a no-op (no exception raised).
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return

    for line in content.splitlines():
        line = line.strip()
        # Skip blank lines and comments
        if not line or line.startswith("#"):
            continue
        # Match KEY = value patterns (tolerant of spaces around =)
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)', line)
        if not m:
            continue
        key = m.group(1)
        val = m.group(2).strip()
        # Strip surrounding quotes (single or double)
        if (val.startswith('"') and val.endswith('"')) or \
           (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        # Only set if not already in environment
        if key not in os.environ:
            os.environ[key] = val
