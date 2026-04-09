"""Tests for Temporal Conv Net."""
import numpy as np
import torch
import pytest


def test_tcn_residual_block():
    from src.models.tcn import TCNResidualBlock
    block = TCNResidualBlock(in_channels=8, out_channels=64, kernel_size=3, dilation=1, dropout=0.0)
    x = torch.randn(4, 8, 168)
    out = block(x)
    assert out.shape == (4, 64, 168)


def test_tcn_model_forward():
    from src.models.tcn import TCNModel
    model = TCNModel(input_channels=8, embed_dim=16, n_classes=3)
    x = torch.randn(4, 8, 168)
    emb, logits = model(x)
    assert emb.shape == (4, 16)
    assert logits.shape == (4, 3)


def test_tcn_batch_independence():
    """Different batch items produce different outputs."""
    from src.models.tcn import TCNModel
    torch.manual_seed(42)
    model = TCNModel(input_channels=8, embed_dim=16, n_classes=3)
    model.eval()
    x = torch.randn(2, 8, 168)
    emb, _ = model(x)
    assert not torch.allclose(emb[0], emb[1])  # different inputs -> different outputs
