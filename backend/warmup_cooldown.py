"""
Detect warm-up and cool-down phases in a steady run.

A warm-up / cool-down is the leading / trailing stretch run notably slower than
the session's main pace. Pure heuristic over the distance + time streams — no
I/O. Honest by design: an even-paced run simply reports no distinct phase rather
than inventing one.
"""
from __future__ import annotations

from typing import Any, Optional

WINDOW_SEC = 20          # rolling window for instantaneous pace
SLOWER_MARGIN = 1.12     # >12% slower than main pace counts as warm-up/cool-down
MIN_PHASE_SEC = 60       # at least a minute to count as a real phase
MAX_PHASE_SEC = 360      # cap — beyond this it isn't a warm-up any more
MIN_RUN_SEC = 300        # runs shorter than 5 min are too short to phase-split


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _rolling_pace(distance: list[float], time: list[float]) -> list[Optional[float]]:
    """sec/km at each index using a trailing ~WINDOW_SEC window."""
    pace: list[Optional[float]] = []
    j = 0
    for i in range(len(time)):
        while time[i] - time[j] > WINDOW_SEC and j < i:
            j += 1
        dt = time[i] - time[j]
        dd = distance[i] - distance[j]
        pace.append((dt / dd * 1000.0) if dd > 1 and dt > 0 else None)
    return pace


def detect_warmup_cooldown(distance_stream: Optional[list[float]],
                           time_stream: Optional[list[float]]) -> dict[str, Any]:
    """Return {warmup_sec, cooldown_sec, has_warmup, has_cooldown, main_pace_sec}.

    warmup/cooldown_sec are 0 when no distinct phase is found.
    """
    empty = {"warmup_sec": 0, "cooldown_sec": 0, "has_warmup": False,
             "has_cooldown": False, "main_pace_sec": None}
    if not distance_stream or not time_stream:
        return empty
    n = min(len(distance_stream), len(time_stream))
    d, t = distance_stream[:n], time_stream[:n]
    if n < 30 or (t[-1] - t[0]) < MIN_RUN_SEC:
        return empty

    pace = _rolling_pace(d, t)
    lo, hi = int(n * 0.30), int(n * 0.70)
    mids = [p for p in pace[lo:hi] if p]
    if not mids:
        return empty
    main = _median(mids)
    threshold = main * SLOWER_MARGIN

    # leading warm-up: contiguous slow block from the start
    warm_idx = 0
    for i in range(n):
        p = pace[i]
        if p is None or p > threshold:
            warm_idx = i
        else:
            break
    warmup_sec = min(int(t[warm_idx] - t[0]), MAX_PHASE_SEC)

    # trailing cool-down: contiguous slow block from the end
    cool_idx = n - 1
    for i in range(n - 1, -1, -1):
        p = pace[i]
        if p is None or p > threshold:
            cool_idx = i
        else:
            break
    cooldown_sec = min(int(t[-1] - t[cool_idx]), MAX_PHASE_SEC)

    return {
        "warmup_sec": warmup_sec if warmup_sec >= MIN_PHASE_SEC else 0,
        "cooldown_sec": cooldown_sec if cooldown_sec >= MIN_PHASE_SEC else 0,
        "has_warmup": warmup_sec >= MIN_PHASE_SEC,
        "has_cooldown": cooldown_sec >= MIN_PHASE_SEC,
        "main_pace_sec": round(main),
    }
