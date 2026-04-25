# TRISHULA Composite Regime — 5-Day Validation Plan

**Deployed**: 2026-04-24 (commit `3c9b50f`)
**Validation window**: 2026-04-24 → 2026-04-29
**Baseline**: Old rule-based regime (88% choppy, never triggered risk_off during live trading)

## What Changed

Replaced rule-based HMM regime with a composite adaptive detector that combines:
- BTC 3-day momentum (35%)
- 7d/30d volatility ratio (20%)
- Fear & Greed Index — contrarian (20%)
- Market breadth — top 50 above 20d MA (25%)

The composite score (-1 to +1) now gates long/short directions and modulates position sizing (0.7x–1.5x).

## Validation Queries

### 1. Trade Count & Direction Gating

Are shorts being blocked in bull regimes? Are longs being blocked in bear regimes?

```sql
-- Trades opened since deployment
SELECT direction, COUNT(*), ROUND(AVG(pnl_pct)::numeric, 2) as avg_pnl_pct,
       SUM(pnl_usdt)::numeric(10,2) as total_pnl
FROM "ML_TRADES"
WHERE entry_time >= '2026-04-24'
GROUP BY direction;
```

**Pass criteria**: In a bull regime, short count should be 0. In a bear regime, long count should be 0. Mixed regime → both present but at reduced sizing.

### 2. Regime State Consistency

Is the regime classification stable and sensible given market conditions?

```sql
-- Check regime states logged in recent trades
SELECT DATE(entry_time), regime_state, COUNT(*),
       ROUND(AVG(pnl_pct)::numeric, 2) as avg_pnl
FROM "ML_TRADES"
WHERE entry_time >= '2026-04-24'
GROUP BY DATE(entry_time), regime_state
ORDER BY 1;
```

**Pass criteria**: Regime state should be consistent within each cycle (all trades in one cycle share the same regime). State should match observable BTC trend direction.

### 3. Position Sizing Validation

Is the size multiplier working?

```sql
-- Compare trade sizes under different regimes
SELECT regime_state, direction,
       ROUND(AVG(usdt_size)::numeric, 2) as avg_size,
       ROUND(MIN(usdt_size)::numeric, 2) as min_size,
       ROUND(MAX(usdt_size)::numeric, 2) as max_size,
       COUNT(*)
FROM "ML_TRADES"
WHERE entry_time >= '2026-04-24'
GROUP BY regime_state, direction;
```

**Pass criteria**:
- `bull_trend` longs: ~$238 (base $159 × 1.5)
- `bear_trend` shorts: ~$238 (base $159 × 1.5)
- `range_bound`: ~$111 (base $159 × 0.7)

### 4. P&L vs BTC Benchmark

Is the bot outperforming simple BTC buy-and-hold?

```sql
-- Bot P&L since deployment
SELECT SUM(pnl_usdt)::numeric(10,2) as bot_pnl,
       COUNT(*) as trades,
       ROUND(AVG(pnl_pct)::numeric, 2) as avg_trade_pnl
FROM "ML_TRADES"
WHERE exit_time >= '2026-04-24' AND status = 'closed';
```

```sql
-- BTC performance over same window
SELECT ROUND(((last_close - first_close) / first_close * 100)::numeric, 2) as btc_pct
FROM (
    SELECT
        (SELECT close FROM "1K_coins_ohlcv" WHERE slug='bitcoin' AND timestamp >= '2026-04-24' ORDER BY timestamp LIMIT 1) as first_close,
        (SELECT close FROM "1K_coins_ohlcv" WHERE slug='bitcoin' ORDER BY timestamp DESC LIMIT 1) as last_close
) x;
```

**Pass criteria**: Bot P&L should be positive, or at minimum less negative than BTC drawdown during bearish periods.

### 5. Ghost Position Check

Are positions being tracked correctly between cycles?

```sql
-- Open positions that should still exist
SELECT slug, direction, entry_price, usdt_size, entry_time
FROM "ML_TRADES"
WHERE status = 'open'
ORDER BY entry_time;
```

**Pass criteria**: Open position count should match Binance exchange state. No ghost positions accumulating.

### 6. Regime Component Sanity

Run locally or on VM to check current regime state:

```bash
cd ~/bot && source venv/bin/activate
python3 -m src.models.regime --check
```

**Pass criteria**: All 4 components should be in [-1, +1] range. Composite should match the weighted sum. Regime state should match the classification rules (bull > 0.15, bear < -0.15, high_vol if vol_ratio > 2).

## Red Flags (Fail Conditions)

| Signal | Meaning | Action |
|--------|---------|--------|
| All trades are longs for 5 days straight | Regime stuck in bull, not adapting | Check if FGI/breadth data is stale |
| P&L worse than -$50 in validation window | Regime sizing is amplifying losses | Reduce max multiplier from 1.5 to 1.2 |
| Ghost positions > 3 per cycle | Position sync broken | Debug exchange API / testnet state |
| Regime flips bull↔bear every cycle | Score near ±0.15 boundary, unstable | Add hysteresis (e.g., require 0.20 to flip) |
| BTC drops 10%+ but regime stays bull | Momentum lag or stale data | Check BTC OHLCV freshness in DB |
| Shorts blocked but BTC is falling | Composite still positive from FGI/breadth | Consider increasing momentum weight |

## Day-by-Day Checklist

| Day | Date | Check |
|-----|------|-------|
| 1 | Apr 25 | Run queries 1-3. Verify direction gating matches regime. Confirm sizing. |
| 2 | Apr 26 | Run query 4. First P&L comparison vs BTC. Check ghost positions. |
| 3 | Apr 27 | Run `--check` on VM. Verify components are tracking real market. Mid-point P&L. |
| 4 | Apr 28 | Full query suite. Look for any red flags. Compare trade-level P&L distribution. |
| 5 | Apr 29 | Final assessment. Run all queries. Decision: keep / tune / revert. |

## Decision Matrix (Day 5)

| Outcome | Action |
|---------|--------|
| P&L positive, gating working, no red flags | **KEEP** — promote to production |
| P&L neutral, gating working, minor issues | **TUNE** — adjust weights/thresholds |
| P&L negative but better than old regime baseline | **TUNE** — reduce sizing multiplier |
| P&L negative and worse than no-regime | **REVERT** — switch to NO_REGIME (pass-through) |
| Bugs found (ghost positions, stale data) | **FIX** — patch and restart validation window |
