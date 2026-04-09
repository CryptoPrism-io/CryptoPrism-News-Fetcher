"""Tests for macro regime HMM model."""
import numpy as np
import pandas as pd
import pytest


def test_build_features_returns_expected_columns():
    from src.models.regime import build_regime_features

    np.random.seed(42)
    n = 100
    dates = pd.date_range("2025-06-01", periods=n, freq="D", tz="UTC")
    btc_df = pd.DataFrame({
        "timestamp": dates,
        "close": 50000 * np.cumprod(1 + np.random.normal(0, 0.02, n)),
        "volume": np.random.uniform(1e9, 5e9, n),
    })
    fg_df = pd.DataFrame({
        "timestamp": dates,
        "fear_greed_index": np.random.uniform(20, 80, n),
    })
    breadth = np.random.uniform(0.3, 0.8, n)

    features = build_regime_features(btc_df, fg_df, breadth)
    for col in ["fear_greed", "btc_vol_7d", "btc_vol_30d",
                "btc_vol_ratio", "btc_mom_24h", "btc_mom_72h", "breadth"]:
        assert col in features.columns, f"Missing column: {col}"


def test_hmm_fit_predict():
    from src.models.regime import fit_regime_hmm, predict_regime

    np.random.seed(42)
    X = np.random.randn(500, 7)
    model = fit_regime_hmm(X, n_states=4)
    assert model is not None

    states, probs = predict_regime(model, X)
    assert len(states) == 500
    assert all(s in range(4) for s in states)
    assert probs.shape == (500, 4)
    assert np.allclose(probs.sum(axis=1), 1.0, atol=1e-6)


def test_label_states():
    from src.models.regime import label_states

    np.random.seed(42)
    X = np.random.randn(100, 7)
    states = np.array([0] * 40 + [1] * 30 + [2] * 20 + [3] * 10)
    labels = label_states(states, X)
    assert set(labels).issubset({"risk_on", "risk_off", "choppy", "breakout"})
    assert len(labels) == 100
