"""
lunar.py
Lunar cycle features for ensemble model.

Encodes the synodic month (~29.53 days) at TWO resolutions:
  1. Full cycle  (29.53d): sin/cos — captures full-moon-to-full-moon patterns
  2. Half cycle  (14.77d): sin/cos — captures new→full and full→new separately
  3. Phase flag:  waxing (new→full) vs waning (full→new)

The hypothesis: crypto markets, being retail-driven, exhibit sentiment cycles
that correlate with the lunar synodic period. The 14-day half-cycle aligns
with observed signal periodicity in LSTM test windows. Waxing (growing moon)
and waning (shrinking moon) phases may drive different risk appetites.

Usage:
    from src.features.lunar import compute_lunar_features
    features = compute_lunar_features(df["timestamp"])
    # Returns dict with: lunar_sin, lunar_cos, lunar_half_sin, lunar_half_cos, lunar_waxing
"""

import numpy as np
import pandas as pd
from datetime import datetime, timezone

# Synodic month: average time between new moons
SYNODIC_MONTH = 29.53058770576  # days
HALF_SYNODIC = SYNODIC_MONTH / 2  # ~14.765 days

# Reference new moon: January 6, 2000 18:14 UTC (well-known astronomical reference)
NEW_MOON_EPOCH = datetime(2000, 1, 6, 18, 14, 0, tzinfo=timezone.utc)


def lunar_phase_days(dt) -> float:
    """
    Compute days since last new moon for a given datetime.
    Returns value in [0, SYNODIC_MONTH).

    Phase mapping:
      0.0          = New Moon
      ~7.4         = First Quarter
      ~14.8        = Full Moon
      ~22.1        = Third Quarter
      ~29.5        = Next New Moon
    """
    if isinstance(dt, pd.Timestamp):
        if dt.tzinfo is None:
            dt = dt.tz_localize("UTC")
        dt = dt.to_pydatetime()
    elif not hasattr(dt, 'tzinfo') or dt.tzinfo is None:
        dt = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)

    delta = (dt - NEW_MOON_EPOCH).total_seconds() / 86400.0
    return delta % SYNODIC_MONTH


def compute_lunar_features(timestamps) -> dict[str, np.ndarray]:
    """
    Compute lunar cycle features at two resolutions.

    Returns dict with 5 arrays:
        lunar_sin:       sin of full 29.53d cycle (peaks at first quarter)
        lunar_cos:       cos of full 29.53d cycle (peaks at new moon, troughs at full moon)
        lunar_half_sin:  sin of 14.77d half-cycle (new→full and full→new)
        lunar_half_cos:  cos of 14.77d half-cycle
        lunar_waxing:    1.0 if waxing (new→full), 0.0 if waning (full→new)
    """
    phases = np.array([lunar_phase_days(t) for t in timestamps])

    # Full cycle encoding (29.53 days)
    full_angle = 2 * np.pi * phases / SYNODIC_MONTH
    lunar_sin = np.sin(full_angle).round(6)
    lunar_cos = np.cos(full_angle).round(6)

    # Half cycle encoding (14.77 days) — new→full and full→new as separate cycles
    half_phase = phases % HALF_SYNODIC  # resets at both new moon and full moon
    half_angle = 2 * np.pi * half_phase / HALF_SYNODIC
    lunar_half_sin = np.sin(half_angle).round(6)
    lunar_half_cos = np.cos(half_angle).round(6)

    # Waxing flag: 1 during new→full (phase 0 to ~14.8), 0 during full→new
    lunar_waxing = (phases < HALF_SYNODIC).astype(float)

    return {
        "lunar_sin": lunar_sin,
        "lunar_cos": lunar_cos,
        "lunar_half_sin": lunar_half_sin,
        "lunar_half_cos": lunar_half_cos,
        "lunar_waxing": lunar_waxing,
    }
