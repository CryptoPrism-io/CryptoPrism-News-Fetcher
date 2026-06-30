"""Regression tests for daily inference feature-contract resolution.

Reproduces the production crash:

    ValueError: X has 113 features, but LGBMClassifier is expecting 95 features
    as input.

Root cause: src/inference/daily_signals.py built the feature matrix from the
registry row's ``features_used`` (113) instead of the loaded model artifact's
own feature list (95). The pickled ``{model, features}`` pair is internally
consistent, whereas ``ML_MODEL_REGISTRY.features_used`` can drift from the .pkl
actually present at ``artifact_path`` (shared artifact filename + run-scoped GH
caches). See artifacts/ml-retrain-feature-mismatch-handoff.md.

The fix aligns daily_signals with the already-correct hourly_signals.py, which
reads ``features = artifact["features"]``.
"""
import json

import numpy as np
import pytest


def test_resolve_features_prefers_artifact_over_registry():
    """Registry declares 113 features; the model artifact was fit on 95.

    Inference must build X from the artifact's 95, not the registry's 113.
    """
    from src.inference.daily_signals import resolve_inference_features

    artifact_features = [f"f{i}" for i in range(95)]
    artifact = {"features": artifact_features}
    active = {
        "model_id": 52,
        "artifact_path": "artifacts/lgbm_ensemble_v1.pkl",
        "features_used": json.dumps([f"f{i}" for i in range(113)]),
    }

    resolved = resolve_inference_features(active, artifact)

    assert resolved == artifact_features
    assert len(resolved) == 95


def test_resolve_features_accepts_list_valued_registry():
    """features_used may already be a python list (not a JSON string)."""
    from src.inference.daily_signals import resolve_inference_features

    artifact = {"features": ["a", "b", "c"]}
    active = {"model_id": 1, "artifact_path": "x.pkl", "features_used": ["a", "b", "c"]}

    assert resolve_inference_features(active, artifact) == ["a", "b", "c"]


def test_resolve_features_falls_back_to_model_when_artifact_lacks_list():
    """Older artifacts without a 'features' key fall back to the model's own
    recorded feature names rather than the registry list."""
    from src.inference.daily_signals import resolve_inference_features

    class _FakeModel:
        feature_name_ = ["m0", "m1", "m2", "m3"]

    artifact = {"model": _FakeModel()}
    active = {
        "model_id": 7,
        "artifact_path": "x.pkl",
        "features_used": json.dumps(["m0", "m1", "m2", "m3", "extra"]),
    }

    assert resolve_inference_features(active, artifact) == ["m0", "m1", "m2", "m3"]


def test_inference_matrix_width_matches_model_and_does_not_crash():
    """End-to-end reproduction of the exact ValueError.

    Train a real LGBMClassifier on 95 features, declare 113 in the registry,
    then verify the resolved feature contract produces an X whose width matches
    the model so predict_proba does NOT raise.
    """
    lgb = pytest.importorskip("lightgbm")
    import pandas as pd

    from src.inference.daily_signals import resolve_inference_features

    rng = np.random.default_rng(0)
    n_model_feats = 95
    feat_names = [f"f{i}" for i in range(n_model_feats)]
    X_train = rng.standard_normal((300, n_model_feats)).astype(np.float32)
    y_train = rng.integers(0, 3, size=300)

    model = lgb.LGBMClassifier(
        objective="multiclass", num_class=3, n_estimators=5, verbose=-1
    )
    model.fit(X_train, y_train)
    assert model.n_features_in_ == n_model_feats

    artifact = {"model": model, "features": feat_names, "label_remap": {0: -1, 1: 0, 2: 1}}
    # Registry row drifted to 113 declared features (the bug's trigger).
    active = {
        "model_id": 52,
        "artifact_path": "artifacts/lgbm_ensemble_v1.pkl",
        "features_used": json.dumps(feat_names + [f"extra_{i}" for i in range(18)]),
    }

    features = resolve_inference_features(active, artifact)

    # fetch_today_features pads any missing feature with NaN and returns exactly
    # `features` columns, so emulate a today-matrix of that width.
    df = pd.DataFrame({c: rng.standard_normal(10) for c in features})
    X = df[features].values.astype(np.float32)

    assert X.shape[1] == model.n_features_in_ == n_model_feats
    probs = model.predict_proba(X)  # would raise ValueError under the old code
    assert probs.shape == (10, 3)
