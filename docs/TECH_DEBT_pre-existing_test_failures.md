# Test Technical Debt — Pre-existing failures (CP-016 B0)

**Recorded:** 2026-08-09, after CP-016 B0 merge (`d634dc3`).

These **9 test failures are pre-existing** on the `etl/ml` refactored layout and are
**unrelated to CP-016**. They did not block B0 (the mapper fix has its own 21 passing
regression tests in `tests/test_coin_mapper.py`). They are tracked here as debt to be
resolved separately.

## Failures

| Test | Class | Reason |
|---|---|---|
| `test_ensemble.py::test_regime_gating_risk_on` | assert drift | `assert 0.3 < 0.3` — threshold/expectation drifted after refactor |
| `test_ensemble.py::test_regime_gating_risk_off` | assert drift | same |
| `test_ensemble.py::test_regime_gating_choppy` | assert drift | same |
| `test_news_events.py::test_rule_based_classifier` | ImportError | `classify_event_rule_based` missing from `ml.features.news_events` after refactor |
| `test_news_events.py::test_compute_hours_since` | ImportError | `compute_hours_since` missing |
| `test_news_events.py::test_magnitude_lookup` | ImportError | `magnitude_lookup` missing |
| `test_regime.py::test_build_features_returns_expected_columns` | ImportError | refactor moved/dropped symbol |
| `test_regime.py::test_hmm_fit_predict` | ImportError | `hmm` symbol missing |
| `test_regime.py::test_label_states` | ImportError | symbol missing |

## Classification

- **3 × `test_ensemble`**: logic/threshold drift — needs a maintainer to reconcile the
  expected gating values with current code.
- **6 × ImportError**: the `etl/ml` refactor (PR #15) moved modules/symbols but the
  tests still import the old names — needs test imports updated to the new layout.

## Impact

- **None on production.** These tests exercise ensemble/regime/news-events internals,
  not the live ingestion path.
- **None on CP-016 B0** — the mapper fix is independently covered by
  `tests/test_coin_mapper.py` (21 tests, all passing).
- Full suite result: **49 passed, 9 failed (pre-existing), 1 warning**.

## Owner

CryptoPrism-io (news-fetcher maintainer). Suggested fix: update the six ImportError
tests to the `etl.*` / `ml.*` layout and re-baseline the three ensemble assertions
against current behavior.
