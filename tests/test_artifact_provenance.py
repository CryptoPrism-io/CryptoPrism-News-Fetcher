"""Tests for model-artifact provenance guards.

These guard against the production crash "X has 113 features, but
LGBMClassifier is expecting 95 features as input", whose root cause was a
shared, fixed artifact filename letting an active registry row drift from the
.pkl actually present at its path. See
artifacts/ml-retrain-feature-mismatch-handoff.md.
"""
import json
import pickle
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


# ── C: unique, immutable artifact paths ──────────────────────────────────────

def test_build_artifact_path_is_unique_and_timestamped():
    from src.models.train_lgbm import build_artifact_path

    t1 = datetime(2026, 6, 28, 6, 27, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 7, 5, 2, 0, 0, tzinfo=timezone.utc)

    p1 = build_artifact_path("trishula_v1", t1)
    p2 = build_artifact_path("trishula_v1", t2)

    assert p1 == "artifacts/trishula_v1_20260628_062700.pkl"
    assert p1 != p2  # different runs → different immutable files
    assert p1.startswith("artifacts/") and p1.endswith(".pkl")


# ── B: train-time consistency tripwire ───────────────────────────────────────

def test_assert_feature_consistency_passes_when_aligned():
    from src.models.train_lgbm import assert_feature_consistency

    assert_feature_consistency(SimpleNamespace(n_features_in_=3), ["a", "b", "c"])


def test_assert_feature_consistency_raises_on_mismatch():
    from src.models.train_lgbm import assert_feature_consistency

    with pytest.raises(ValueError):
        assert_feature_consistency(
            SimpleNamespace(n_features_in_=95), [f"f{i}" for i in range(113)]
        )


# ── B: activation-time guard (registry row ↔ artifact) ───────────────────────

def _write_artifact(path, n_features, feature_names=None):
    feature_names = feature_names or [f"f{i}" for i in range(n_features)]
    with open(path, "wb") as f:
        pickle.dump(
            {"model": SimpleNamespace(n_features_in_=n_features), "features": feature_names},
            f,
        )


def test_activation_guard_raises_on_row_artifact_skew(tmp_path):
    from src.models.registry import assert_registry_artifact_consistent

    art = tmp_path / "m.pkl"
    _write_artifact(art, 95)  # model fit on 95
    with pytest.raises(ValueError):
        # registry row declares 113 → must refuse to activate
        assert_registry_artifact_consistent(
            52, json.dumps([f"f{i}" for i in range(113)]), str(art)
        )


def test_activation_guard_passes_when_consistent(tmp_path):
    from src.models.registry import assert_registry_artifact_consistent

    art = tmp_path / "m.pkl"
    _write_artifact(art, 3, ["a", "b", "c"])
    assert_registry_artifact_consistent(1, json.dumps(["a", "b", "c"]), str(art))


def test_activation_guard_proceeds_when_artifact_missing():
    from src.models.registry import assert_registry_artifact_consistent

    # Missing file → best-effort: warn and proceed (never brick activation).
    assert_registry_artifact_consistent(
        7, json.dumps(["a", "b"]), "artifacts/does_not_exist_xyz.pkl"
    )


def test_activation_guard_accepts_list_valued_features_used(tmp_path):
    from src.models.registry import assert_registry_artifact_consistent

    art = tmp_path / "m.pkl"
    _write_artifact(art, 2, ["a", "b"])
    # features_used already a python list (not a JSON string)
    assert_registry_artifact_consistent(9, ["a", "b"], str(art))
