"""Tests for LSTM feature extractor."""
import numpy as np
import torch
import pytest


def test_lstm_model_forward():
    from src.models.lstm_extractor import LSTMExtractor
    model = LSTMExtractor(input_dim=12, hidden_dim=64, embed_dim=12, n_classes=3)
    x = torch.randn(8, 30, 12)
    emb, logits = model(x)
    assert emb.shape == (8, 12)
    assert logits.shape == (8, 3)


def test_lstm_model_deterministic():
    from src.models.lstm_extractor import LSTMExtractor
    torch.manual_seed(42)
    model = LSTMExtractor(input_dim=12, hidden_dim=64, embed_dim=12, n_classes=3)
    model.eval()
    x = torch.randn(4, 30, 12)
    emb1, _ = model(x)
    emb2, _ = model(x)
    assert torch.allclose(emb1, emb2)


def test_build_sequences():
    from src.models.lstm_extractor import build_sequences
    import pandas as pd
    np.random.seed(42)
    n = 60
    dates = pd.date_range("2025-06-01", periods=n, freq="D")
    df = pd.DataFrame({
        "timestamp": dates,
        **{f"feat_{i}": np.random.randn(n) for i in range(12)},
    })
    sequences, timestamps = build_sequences(df, seq_len=30, feature_cols=[f"feat_{i}" for i in range(12)])
    assert sequences.shape == (31, 30, 12)
    assert len(timestamps) == 31
