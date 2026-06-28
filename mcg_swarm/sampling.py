"""Adaptive row sampling: full scan for small tables, a spread (head/middle/tail)
sample for large ones so anomalies anywhere in the column are caught — not just the
first rows (which let late-row dtype drift slip past static and the gate)."""
from __future__ import annotations
import os

DEFAULT_FULL_THRESHOLD = 300
DEFAULT_SAMPLE_SIZE = 300


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default


def select_sample(row_keys, *, full_threshold=None, sample_size=None):
    if full_threshold is None:
        full_threshold = _env_int("MCG_SAMPLE_FULL_THRESHOLD", DEFAULT_FULL_THRESHOLD)
    if sample_size is None:
        sample_size = _env_int("MCG_SAMPLE_SIZE", DEFAULT_SAMPLE_SIZE)
    n = len(row_keys)
    if n <= full_threshold or n <= sample_size:
        return list(row_keys)
    # Guard against sample_size=1 causing ZeroDivisionError (reachable via MCG_SAMPLE_SIZE=1).
    if sample_size <= 1:
        return [row_keys[0], row_keys[-1]]
    # Even stride across the whole range; force-include first and last; dedupe; keep order.
    idxs = {0, n - 1}
    step = (n - 1) / (sample_size - 1)
    for i in range(sample_size):
        idxs.add(int(round(i * step)))
    return [row_keys[i] for i in sorted(idxs) if 0 <= i < n]
