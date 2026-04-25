#!/bin/bash
# Regime validation startup check
# Validation window: 2026-04-24 → 2026-04-29

DEPLOY_DATE="20260424"
END_DATE="20260429"
TODAY=$(date +%Y%m%d)

if [ "$TODAY" -gt "$END_DATE" ]; then
    echo "REGIME_VALIDATION=EXPIRED"
    echo "Regime validation window (Apr 24-29) has passed. Run final assessment and remove this hook."
elif [ "$TODAY" -ge "$DEPLOY_DATE" ]; then
    DAY_NUM=$(( (TODAY - DEPLOY_DATE) + 1 ))
    echo "REGIME_VALIDATION=ACTIVE"
    echo "Day ${DAY_NUM}/5 of composite regime validation (deployed 2026-04-24)."
    echo "Run validation queries from docs/regime-validation-plan.md"
    echo "Quick check: SELECT direction, COUNT(*), SUM(pnl_usdt)::numeric(10,2) FROM \"ML_TRADES\" WHERE entry_time >= '2026-04-24' GROUP BY direction;"
fi
