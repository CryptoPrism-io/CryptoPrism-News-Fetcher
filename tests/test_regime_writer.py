"""The ML_REGIME writer: what a scheduled run must leave in the table.

ML_REGIME froze at 2026-04-11 because nothing ever wrote it on a schedule, and
the only workflow that could still called CLI flags regime.py no longer had.
"""
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from ml.models import regime

WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "backfill-regime.yml"
TODAY = date(2026, 11, 6)
CURRENT = regime.get_regime_decision(0.03, 0.05, 0.02, 0.02, 40, 0.6)


def _history(last_candle: date, days: int = 400) -> list[dict]:
    """Regime rows built from BTC candles ending at last_candle."""
    dates = [last_candle - timedelta(days=i) for i in reversed(range(days))]
    closes = [100 + (i % 11) * 2 + i * 0.05 for i in range(days)]
    btc = pd.DataFrame({"date": dates, "close": closes})
    btc["ret"] = btc["close"].pct_change()
    btc["vol_7d"] = btc["ret"].rolling(7).std()
    btc["vol_30d"] = btc["ret"].rolling(30).std()
    btc["mom_3d"] = btc["close"].pct_change(3)
    btc["mom_7d"] = btc["close"].pct_change(7)
    fgi = pd.DataFrame({"date": dates, "fgi": [50] * days})
    breadth = pd.DataFrame({"date": dates, "breadth": [0.5] * days})
    return regime.build_regime_rows(btc, fgi, breadth)


def _dates(rows: list[dict]) -> list[date]:
    return [r["timestamp"].date() for r in rows]


def test_workflow_runs_on_a_schedule():
    text = WORKFLOW.read_text()
    assert re.search(r"^\s+schedule:", text, re.M)
    assert re.search(r"^\s+- cron:", text, re.M)


def test_every_regime_command_in_the_workflow_is_a_real_cli_flag():
    commands = re.findall(r"python -m ml\.models\.regime (.+)", WORKFLOW.read_text())
    assert commands
    for args in commands:
        regime.build_parser().parse_args(args.split())


def test_scheduled_run_writes_a_row_dated_today():
    """Candles are stamped D-1 23:59:59, so the newest candle is never today."""
    rows = regime.plan_update(_history(TODAY - timedelta(days=1)), CURRENT, None, TODAY)

    today_row = rows[-1]
    assert today_row["timestamp"] == datetime(2026, 11, 6, tzinfo=timezone.utc)
    assert today_row["regime_state"] == CURRENT.regime_state
    assert today_row["confidence"] == CURRENT.confidence


def test_first_run_after_the_freeze_fills_the_whole_gap():
    frozen_at = date(2026, 4, 11)
    rows = regime.plan_update(_history(TODAY - timedelta(days=1)), CURRENT, frozen_at, TODAY)

    written = set(_dates(rows))
    gap = {frozen_at + timedelta(days=i) for i in range((TODAY - frozen_at).days + 1)}
    assert gap <= written


def test_candle_based_row_replaces_a_late_provisional_row():
    """Yesterday's row was stamped provisionally; its candle has since landed.

    It must be rewritten even if the candle was late by a run or two.
    """
    history = _history(TODAY - timedelta(days=1))
    rows = regime.plan_update(history, CURRENT, TODAY, TODAY)

    assert TODAY - timedelta(days=1) in _dates(rows)


def test_rerun_never_writes_a_timestamp_twice():
    rows = regime.plan_update(_history(TODAY), CURRENT, TODAY, TODAY)

    assert len(_dates(rows)) == len(set(_dates(rows)))


def test_stale_candles_are_not_published_as_todays_regime():
    """A dead OHLCV feed must fail the run, not pass old data off as current."""
    history = _history(TODAY - timedelta(days=5))

    with pytest.raises(regime.StaleMarketData):
        regime.plan_update(history, CURRENT, date(2026, 10, 30), TODAY)


def test_candles_from_before_the_morning_ohlcv_load_are_still_fresh():
    """The first run of the day can land before the D-1 candle does."""
    rows = regime.plan_update(_history(TODAY - timedelta(days=2)), CURRENT, None, TODAY)

    assert _dates(rows)[-1] == TODAY
