"""Tests for ensemble training and inference."""
import numpy as np
import pytest


def test_feature_set_count():
    from src.models.train_ensemble import FEATURES_ENSEMBLE
    assert len(FEATURES_ENSEMBLE) >= 80, f"Expected 80+, got {len(FEATURES_ENSEMBLE)}"


def test_regime_gating_risk_on():
    from src.models.train_ensemble import apply_regime_gating
    # Risk-on: discount SELL signals
    adjusted = apply_regime_gating(-0.3, "risk_on", 0.8)
    assert abs(adjusted) < 0.3

def test_regime_gating_risk_off():
    from src.models.train_ensemble import apply_regime_gating
    # Risk-off: discount BUY signals
    adjusted = apply_regime_gating(0.3, "risk_off", 0.8)
    assert abs(adjusted) < 0.3

def test_regime_gating_choppy():
    from src.models.train_ensemble import apply_regime_gating
    adjusted = apply_regime_gating(0.3, "choppy", 0.8)
    assert abs(adjusted) < 0.3

def test_regime_gating_breakout():
    from src.models.train_ensemble import apply_regime_gating
    # Breakout: amplify
    adjusted = apply_regime_gating(0.3, "breakout", 0.8)
    assert abs(adjusted) >= 0.3
