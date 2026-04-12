"""
lunar.py
Lunar cycle features for ensemble model.
Uses the synodic month (~29.53 days) to compute sine/cosine phase encoding.

The hypothesis: crypto markets, being retail-driven, may exhibit sentiment cycles
that correlate with the lunar synodic period (~29.53 days). The 14-day half-cycle
aligns with observed signal periodicity in LSTM test windows.

Usage:
    from src.features.lunar import compute_lunar_features
    df["lunar_sin"], df["lunar_cos"] = compute_lunar_features(df["timestamp"])
"""

import numpy as np
import pandas as pd
from datetime import datetime, timezone

# Synodic month: average time between new moons
SYNODIC_MONTH = 29.53058770576  # days

# Reference new moon: January 6, 2000 18:14 UTC (well-known astronomical reference)
NEW_MOON_EPOCH = datetime(2000, 1, 6, 18, 14, 0, tzinfo=timezone.utc)


def lunar_phase_days(dt) -> float:
    """
    Compute days since last new moon for a given datetime.
    Returns value in [0, SYNODIC_MONTH).
    """
    if isinstance(dt, pd.Timestamp):
        if dt.tzinfo is None:
            dt = dt.tz_localize("UTC")
        dt = dt.to_pydatetime()
    elif not hasattr(dt, 'tzinfo') or dt.tzinfo is None:
        dt = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)

    delta = (dt - NEW_MOON_EPOCH).total_seconds() / 86400.0
    return delta % SYNODIC_MONTH


def compute_lunar_features(timestamps) -> tuple[np.ndarray, np.ndarray]:
    """
    Compute lunar cycle sine and cosine features for a series of timestamps.

    Returns:
        (lunar_sin, lunar_cos) arrays — both in [-1, 1]
        - lunar_sin peaks at first quarter (day ~7.4), troughs at third quarter (~22.1)
        - lunar_cos peaks at new moon (day 0), troughs at full moon (~14.8)
    """
    phases = np.array([lunar_phase_days(t) for t in timestamps])
    angle = 2 * np.pi * phases / SYNODIC_MONTH
    return np.sin(angle).round(6), np.cos(angle).round(6)
