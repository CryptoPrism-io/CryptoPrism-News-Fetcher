"""Tests for BTC residual decomposition."""
import numpy as np
import pandas as pd
import pytest


def test_rolling_ols_basic():
    """Rolling OLS on synthetic data recovers known beta."""
    from src.features.btc_residuals import rolling_ols

    np.random.seed(42)
    n = 60 * 24  # 60 days hourly
    btc_ret = np.random.normal(0, 0.01, n)
    true_beta = 1.5
    true_alpha = 0.0002
    noise = np.random.normal(0, 0.005, n)
    coin_ret = true_alpha + true_beta * btc_ret + noise

    result = rolling_ols(coin_ret, btc_ret, window=30 * 24)
    valid = result[~np.isnan(result["beta"])]
    assert len(valid) > 0
    assert abs(valid["beta"].iloc[-1] - true_beta) < 0.3
    assert "residual" in result.columns
    assert "alpha" in result.columns


def test_rolling_ols_short_series():
    """Returns NaN for windows shorter than lookback."""
    from src.features.btc_residuals import rolling_ols

    btc_ret = np.random.normal(0, 0.01, 100)
    coin_ret = np.random.normal(0, 0.01, 100)
    result = rolling_ols(coin_ret, btc_ret, window=720)
    assert result["beta"].isna().all()


def test_residual_vol_ratio():
    """Residual vol ratio between 0 and 1."""
    from src.features.btc_residuals import compute_residual_vol_ratio

    np.random.seed(42)
    residuals = np.random.normal(0, 0.005, 720)
    total_rets = np.random.normal(0, 0.01, 720)
    ratio = compute_residual_vol_ratio(residuals, total_rets, window=720)
    assert 0 <= ratio <= 1


def test_compute_for_slug():
    """End-to-end: compute residuals for a single coin."""
    from src.features.btc_residuals import compute_for_slug

    np.random.seed(42)
    n = 60 * 24
    dates = pd.date_range("2025-06-01", periods=n, freq="h", tz="UTC")
    btc_close = 50000 * np.cumprod(1 + np.random.normal(0, 0.005, n))
    coin_close = 100 * np.cumprod(1 + 1.3 * np.random.normal(0, 0.005, n) + np.random.normal(0, 0.003, n))

    btc_df = pd.DataFrame({"timestamp": dates, "close": btc_close})
    coin_df = pd.DataFrame({"timestamp": dates, "close": coin_close})

    result = compute_for_slug(coin_df, btc_df, window_hours=30 * 24)
    assert len(result) == n
    assert "beta_30d" in result.columns
    assert "residual_1h" in result.columns
    assert "residual_vol_ratio" in result.columns
    valid = result.dropna(subset=["beta_30d"])
    assert len(valid) > n * 0.4
