"""
regime.py
Composite adaptive regime detector for TRISHULA.

Combines BTC momentum, volatility, Fear & Greed, and market breadth
into a single composite score that modulates both trade direction
and position sizing.

Backtested April 2026: +$142.39 (composite) vs -$39.81 (no regime)
— a +$182.20 improvement (+457.7%).

Usage:
    python -m ml.models.regime --update         # scheduled: fill gaps, write today's row
    python -m ml.models.regime --backfill       # rewrite all of ML_REGIME
    python -m ml.models.regime --check           # show current regime state
"""

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from crypto_db import get_db_conn, get_backtest_conn

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

REGIME_NAMES = ["bull_trend", "bear_trend", "range_bound", "high_vol"]

# Rewrite this many days behind the newest row on each update, so a row stamped
# before its candle landed gets replaced by the candle-based value.
UPDATE_LOOKBACK_DAYS = 7

# OHLCV candles are stamped D-1 23:59:59 and load around 06:00-07:30 UTC, so a
# healthy feed can be two days behind early in the day. Older than that is a
# dead feed.
MAX_CANDLE_AGE_DAYS = 2


class StaleMarketData(RuntimeError):
    """The newest candle is too old to publish a regime for today."""


@dataclass
class RegimeDecision:
    """Output of the regime detector — tells the bot what to do."""
    regime_state: str          # bull_trend, bear_trend, range_bound, high_vol
    composite_score: float     # -1.0 to +1.0 (positive = bullish)
    allow_long: bool
    allow_short: bool
    long_size_mult: float      # 0.0 to 1.5 (multiply per-trade size)
    short_size_mult: float     # 0.0 to 1.5
    confidence: float          # 0.0 to 1.0
    components: dict           # breakdown of score components


def compute_composite_score(
    btc_mom_3d: float,
    btc_mom_7d: float,
    btc_vol_7d: float,
    btc_vol_30d: float,
    fear_greed: float,
    breadth: float,
) -> tuple[float, dict]:
    """
    Compute the composite regime score from market features.

    Components (each scaled to -1..+1):
      - Momentum (35%): BTC 3d return * 20, capped ±1
      - Volatility (20%): 1.5 - vol_ratio, capped ±1 (calm = positive)
      - FGI (20%): contrarian — extreme fear = buy, extreme greed = sell
      - Breadth (25%): % of top 50 above 20d MA, centered at 0.5

    Returns: (composite_score, component_dict)
    """
    # Momentum: positive = bullish, scaled so ±5% move → ±1
    mom_score = float(np.clip(btc_mom_3d * 20, -1, 1))

    # Volatility: low vol relative to 30d = calm = good for trend following
    vol_ratio = btc_vol_7d / btc_vol_30d if btc_vol_30d > 0 else 1.0
    vol_score = float(np.clip(1.5 - vol_ratio, -1, 1))

    # FGI: contrarian — extreme fear (low FGI) = bullish, extreme greed = bearish
    fgi_score = float(np.clip((50 - fear_greed) / 50, -1, 1))

    # Breadth: high breadth = broad participation = bullish
    breadth_score = float(np.clip((breadth - 0.5) * 4, -1, 1))

    composite = (
        0.35 * mom_score
        + 0.20 * vol_score
        + 0.20 * fgi_score
        + 0.25 * breadth_score
    )

    components = {
        "momentum": round(mom_score, 4),
        "volatility": round(vol_score, 4),
        "fgi": round(fgi_score, 4),
        "breadth": round(breadth_score, 4),
    }

    return round(composite, 4), components


def classify_regime(composite: float, vol_ratio: float) -> str:
    """Map composite score + volatility to a named regime state."""
    if vol_ratio > 2.0:
        return "high_vol"
    if composite > 0.15:
        return "bull_trend"
    if composite < -0.15:
        return "bear_trend"
    return "range_bound"


def get_regime_decision(
    btc_mom_3d: float,
    btc_mom_7d: float,
    btc_vol_7d: float,
    btc_vol_30d: float,
    fear_greed: float,
    breadth: float,
) -> RegimeDecision:
    """
    Main entry point: compute regime and return trading decision.

    Long rules:
      composite > 0.2  → allow, size 1.0 + composite (up to 1.5x)
      composite > -0.1 → allow, size 0.7x
      composite ≤ -0.1 → block longs

    Short rules:
      composite < -0.2 → allow, size 1.0 + |composite| (up to 1.5x)
      composite < 0.1  → allow, size 0.7x
      composite ≥ 0.1  → block shorts
    """
    vol_ratio = btc_vol_7d / btc_vol_30d if btc_vol_30d > 0 else 1.0

    composite, components = compute_composite_score(
        btc_mom_3d, btc_mom_7d, btc_vol_7d, btc_vol_30d,
        fear_greed, breadth,
    )

    regime_state = classify_regime(composite, vol_ratio)
    confidence = min(abs(composite) * 2, 1.0)

    # Long decision
    if composite > 0.2:
        allow_long = True
        long_mult = min(1.0 + composite, 1.5)
    elif composite > -0.1:
        allow_long = True
        long_mult = 0.7
    else:
        allow_long = False
        long_mult = 0.0

    # Short decision
    if composite < -0.2:
        allow_short = True
        short_mult = min(1.0 + abs(composite), 1.5)
    elif composite < 0.1:
        allow_short = True
        short_mult = 0.7
    else:
        allow_short = False
        short_mult = 0.0

    return RegimeDecision(
        regime_state=regime_state,
        composite_score=composite,
        allow_long=allow_long,
        allow_short=allow_short,
        long_size_mult=round(long_mult, 3),
        short_size_mult=round(short_mult, 3),
        confidence=round(confidence, 3),
        components=components,
    )


# ── Data loading helpers ──

def load_btc_features(conn) -> pd.DataFrame:
    """Load BTC OHLCV and compute momentum/volatility features."""
    btc = pd.read_sql(
        'SELECT timestamp::date as date, close, volume '
        'FROM "1K_coins_ohlcv" '
        "WHERE slug = 'bitcoin' ORDER BY timestamp",
        conn,
    )
    btc["ret"] = btc["close"].pct_change()
    btc["vol_7d"] = btc["ret"].rolling(7).std()
    btc["vol_30d"] = btc["ret"].rolling(30).std()
    btc["mom_3d"] = btc["close"].pct_change(3)
    btc["mom_7d"] = btc["close"].pct_change(7)
    return btc


def load_fgi(conn) -> pd.DataFrame:
    """Load Fear & Greed Index."""
    return pd.read_sql(
        'SELECT timestamp::date as date, fear_greed_index as fgi '
        'FROM "FE_FEAR_GREED_CMC" ORDER BY timestamp',
        conn,
    )


def compute_market_breadth(conn, n_days: int = 730) -> pd.DataFrame:
    """Compute % of top 50 coins above 20d MA."""
    return pd.read_sql("""
        WITH daily AS (
            SELECT slug, timestamp::date as d, close, market_cap,
                   AVG(close) OVER (PARTITION BY slug ORDER BY timestamp
                                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as ma20
            FROM "1K_coins_ohlcv"
            WHERE timestamp >= CURRENT_DATE - %(n_days)s
        ),
        top50 AS (
            SELECT d, slug, close, ma20,
                   ROW_NUMBER() OVER (PARTITION BY d ORDER BY market_cap DESC NULLS LAST) as rn
            FROM daily WHERE market_cap IS NOT NULL
        )
        SELECT d as date,
               COUNT(*) FILTER (WHERE close > ma20)::float / NULLIF(COUNT(*), 0) as breadth
        FROM top50 WHERE rn <= 50
        GROUP BY d ORDER BY d
    """, conn, params={"n_days": n_days})


def get_current_regime(conn=None) -> RegimeDecision:
    """Fetch latest market data and return current regime decision."""
    own_conn = conn is None
    if own_conn:
        conn = get_db_conn()

    btc = load_btc_features(conn)
    fgi_df = load_fgi(conn)
    breadth_df = compute_market_breadth(conn, n_days=60)

    if own_conn:
        conn.close()

    latest_btc = btc.iloc[-1]
    latest_fgi = float(fgi_df.iloc[-1]["fgi"]) if len(fgi_df) > 0 else 50.0
    latest_breadth = float(breadth_df.iloc[-1]["breadth"]) if len(breadth_df) > 0 else 0.5

    return get_regime_decision(
        btc_mom_3d=float(latest_btc.get("mom_3d", 0) or 0),
        btc_mom_7d=float(latest_btc.get("mom_7d", 0) or 0),
        btc_vol_7d=float(latest_btc.get("vol_7d", 0.02) or 0.02),
        btc_vol_30d=float(latest_btc.get("vol_30d", 0.02) or 0.02),
        fear_greed=latest_fgi,
        breadth=latest_breadth,
    )


# ── Backfill ──

def upsert_regime(conn, rows: list[dict]):
    """Write regime predictions to ML_REGIME."""
    sql = """
        INSERT INTO "ML_REGIME" (
            timestamp, regime_state, confidence,
            trans_prob_risk_on, trans_prob_risk_off,
            trans_prob_choppy, trans_prob_breakout
        ) VALUES (
            %(timestamp)s, %(regime_state)s, %(confidence)s,
            %(trans_prob_risk_on)s, %(trans_prob_risk_off)s,
            %(trans_prob_choppy)s, %(trans_prob_breakout)s
        )
        ON CONFLICT (timestamp) DO UPDATE SET
            regime_state       = EXCLUDED.regime_state,
            confidence         = EXCLUDED.confidence,
            trans_prob_risk_on  = EXCLUDED.trans_prob_risk_on,
            trans_prob_risk_off = EXCLUDED.trans_prob_risk_off,
            trans_prob_choppy   = EXCLUDED.trans_prob_choppy,
            trans_prob_breakout = EXCLUDED.trans_prob_breakout
    """
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)
    conn.commit()


def regime_row(timestamp: pd.Timestamp, decision: RegimeDecision) -> dict:
    """Map a decision onto ML_REGIME's columns.

    The trans_prob_* columns are left over from the HMM. They now hold the
    composite score and the long/short size multipliers.
    """
    return {
        "timestamp": timestamp,
        "regime_state": decision.regime_state,
        "confidence": decision.confidence,
        "trans_prob_risk_on": decision.composite_score,
        "trans_prob_risk_off": decision.long_size_mult,
        "trans_prob_choppy": decision.short_size_mult,
        "trans_prob_breakout": 0.0,
    }


def build_regime_rows(btc: pd.DataFrame, fgi_df: pd.DataFrame, breadth_df: pd.DataFrame) -> list[dict]:
    """One regime row per BTC candle date, oldest first."""
    fgi_map = dict(zip(fgi_df["date"], fgi_df["fgi"]))
    breadth_map = dict(zip(breadth_df["date"], breadth_df["breadth"]))

    rows = []
    for _, r in btc.iterrows():
        d = r["date"]
        mom3 = float(r.get("mom_3d", 0) or 0)
        mom7 = float(r.get("mom_7d", 0) or 0)
        v7 = float(r.get("vol_7d", 0) or 0)
        v30 = float(r.get("vol_30d", 0) or 0)

        if np.isnan(mom3) or np.isnan(v7) or v30 == 0:
            continue

        fg = float(fgi_map.get(d, 50))
        b = float(breadth_map.get(d, 0.5))

        decision = get_regime_decision(mom3, mom7, v7, v30, fg, b)
        rows.append(regime_row(pd.Timestamp(d, tz="UTC"), decision))

    return rows


def load_regime_rows(conn) -> list[dict]:
    """Build regime rows from the warehouse."""
    return build_regime_rows(
        load_btc_features(conn),
        load_fgi(conn),
        compute_market_breadth(conn, n_days=730),
    )


def plan_update(
    history: list[dict],
    current: RegimeDecision,
    last_written: date | None,
    today: date,
) -> list[dict]:
    """Pick the rows a scheduled run should write.

    Candle-based rows from just before last_written onwards, plus a row dated
    today holding the current decision. The newest candle is never today's, so
    without that row ML_REGIME would always read a day old. A later run replaces
    it with the candle-based value once that candle exists.
    """
    newest_candle = history[-1]["timestamp"].date() if history else None
    if newest_candle is None or (today - newest_candle).days > MAX_CANDLE_AGE_DAYS:
        raise StaleMarketData(f"Newest BTC candle is {newest_candle}, today is {today}")

    rows = history
    if last_written is not None:
        since = last_written - timedelta(days=UPDATE_LOOKBACK_DAYS)
        rows = [r for r in history if r["timestamp"].date() >= since]

    if newest_candle < today:
        rows = rows + [regime_row(pd.Timestamp(today, tz="UTC"), current)]
    return rows


def last_written_date(conn) -> date | None:
    """UTC date of the newest ML_REGIME row, or None if the table is empty."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT (MAX(timestamp) AT TIME ZONE 'UTC')::date
            FROM "ML_REGIME"
        """)
        return cur.fetchone()[0]


def update(conn=None):
    """Scheduled writer: fill any gap in ML_REGIME and write today's row."""
    own_conn = conn is None
    if own_conn:
        conn = get_db_conn()

    rows = plan_update(
        load_regime_rows(conn),
        get_current_regime(conn),
        last_written_date(conn),
        datetime.now(timezone.utc).date(),
    )
    upsert_regime(conn, rows)

    if own_conn:
        conn.close()

    latest = rows[-1]
    log.info(
        f"Wrote {len(rows)} ML_REGIME rows from {rows[0]['timestamp'].date()} "
        f"to {latest['timestamp'].date()} (latest: {latest['regime_state']})"
    )


def backfill():
    """Rewrite ML_REGIME from all available candles."""
    conn = get_db_conn()
    rows = load_regime_rows(conn)
    upsert_regime(conn, rows)
    conn.close()

    total = len(rows)
    log.info(f"Backfilled {total} composite regime rows to ML_REGIME")
    for state in REGIME_NAMES:
        count = sum(1 for r in rows if r["regime_state"] == state)
        pct = count / total * 100 if total > 0 else 0
        log.info(f"  {state}: {count:,} ({pct:.1f}%)")


def check_current():
    """Print current regime state."""
    decision = get_current_regime()
    print(f"\n{'='*50}")
    print(f"  TRISHULA Regime — {decision.regime_state.upper()}")
    print(f"{'='*50}")
    print(f"  Composite score: {decision.composite_score:+.4f}")
    print(f"  Confidence:      {decision.confidence:.3f}")
    print()
    print(f"  Components:")
    for k, v in decision.components.items():
        print(f"    {k:<12s}: {v:+.4f}")
    print()
    print(f"  Decisions:")
    print(f"    Longs:  {'ALLOWED' if decision.allow_long else 'BLOCKED'} (size: {decision.long_size_mult:.2f}x)")
    print(f"    Shorts: {'ALLOWED' if decision.allow_short else 'BLOCKED'} (size: {decision.short_size_mult:.2f}x)")
    print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TRISHULA Composite Regime Detector")
    parser.add_argument("--update", action="store_true", help="Fill gaps in ML_REGIME and write today's row")
    parser.add_argument("--backfill", action="store_true", help="Backfill ML_REGIME with composite regime")
    parser.add_argument("--check", action="store_true", help="Show current regime state")
    return parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()

    if args.update:
        update()
    elif args.backfill:
        backfill()
    elif args.check:
        check_current()
    else:
        parser.print_help()
