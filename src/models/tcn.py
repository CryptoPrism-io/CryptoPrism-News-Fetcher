"""
tcn.py
Temporal Conv Net — 1D causal CNN with dilated convolutions.
168h hourly window, 4 residual blocks, dilation [1, 2, 4, 8].

Usage:
    python -m src.models.tcn --train
    python -m src.models.tcn --export-onnx
"""

import argparse
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from src.db import get_backtest_conn, get_backtest_h_conn, get_db_conn

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SEQ_LEN = 168  # 7 days hourly
INPUT_CHANNELS = 8
EMBED_DIM = 16
N_CLASSES = 3
HIDDEN_CHANNELS = 64
ARTIFACT_PATH = "artifacts/tcn_model.pt"
ONNX_PATH = "artifacts/tcn_model.onnx"

FEATURE_COLS = [
    "residual_1h", "residual_volume",
    "price_spread", "close_open_dir",
    "volume_zscore_7d",
    "hour_sin", "hour_cos",
    "residual_vol_24h",
]


class TCNResidualBlock(nn.Module):
    """Single residual block with dilated causal convolution."""

    def __init__(self, in_channels: int, out_channels: int, kernel_size: int = 3,
                 dilation: int = 1, dropout: float = 0.3):
        super().__init__()
        self.padding = (kernel_size - 1) * dilation
        self.conv1 = nn.Conv1d(in_channels, out_channels, kernel_size,
                               padding=self.padding, dilation=dilation)
        self.bn1 = nn.BatchNorm1d(out_channels)
        self.conv2 = nn.Conv1d(out_channels, out_channels, kernel_size,
                               padding=self.padding, dilation=dilation)
        self.bn2 = nn.BatchNorm1d(out_channels)
        self.dropout = nn.Dropout(dropout)
        self.residual = nn.Conv1d(in_channels, out_channels, 1) if in_channels != out_channels else nn.Identity()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        res = self.residual(x)
        out = self.conv1(x)
        if self.padding > 0:
            out = out[:, :, :-self.padding]
        out = F.relu(self.bn1(out))
        out = self.dropout(out)
        out = self.conv2(out)
        if self.padding > 0:
            out = out[:, :, :-self.padding]
        out = self.bn2(out)
        return F.relu(out + res)


class TCNModel(nn.Module):
    """Full TCN with 4 residual blocks + embedding and classification heads."""

    def __init__(self, input_channels: int = INPUT_CHANNELS,
                 hidden_channels: int = HIDDEN_CHANNELS,
                 embed_dim: int = EMBED_DIM, n_classes: int = N_CLASSES):
        super().__init__()
        self.blocks = nn.Sequential(
            TCNResidualBlock(input_channels, hidden_channels, dilation=1),
            TCNResidualBlock(hidden_channels, hidden_channels, dilation=2),
            TCNResidualBlock(hidden_channels, hidden_channels, dilation=4),
            TCNResidualBlock(hidden_channels, hidden_channels, dilation=8),
        )
        self.embed_head = nn.Linear(hidden_channels, embed_dim)
        self.class_head = nn.Linear(hidden_channels, n_classes)

    def forward(self, x: torch.Tensor):
        features = self.blocks(x)
        pooled = features.mean(dim=2)  # global average pool
        return self.embed_head(pooled), self.class_head(pooled)


def build_hourly_features(coin_df: pd.DataFrame, residuals_df: pd.DataFrame) -> pd.DataFrame:
    """Build 8 TCN input features per hourly timestep for one coin."""
    df = coin_df.sort_values("timestamp").copy()
    df = df.merge(residuals_df[["timestamp", "residual_1h"]], on="timestamp", how="left")

    df["residual_volume"] = df["volume"].pct_change().fillna(0)
    df["price_spread"] = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)
    df["close_open_dir"] = np.sign(df["close"] - df["open"]).fillna(0)

    vol_mean = df["volume"].rolling(168, min_periods=1).mean()
    vol_std = df["volume"].rolling(168, min_periods=1).std().replace(0, np.nan)
    df["volume_zscore_7d"] = (df["volume"] - vol_mean) / vol_std

    hours = pd.to_datetime(df["timestamp"]).dt.hour
    df["hour_sin"] = np.sin(2 * np.pi * hours / 24)
    df["hour_cos"] = np.cos(2 * np.pi * hours / 24)
    df["residual_vol_24h"] = df["residual_1h"].rolling(24, min_periods=1).std()

    return df[["timestamp"] + FEATURE_COLS]


def train_model(epochs: int = 30, lr: float = 1e-3, batch_size: int = 128):
    """Train TCN on hourly residual sequences."""
    log.info("TCN training — loading data...")

    h_conn = get_backtest_h_conn()
    bt_conn = get_backtest_conn()
    dbcp_conn = get_db_conn()

    ohlcv = pd.read_sql(
        'SELECT slug, timestamp, open, high, low, close, volume '
        'FROM "ohlcv_1h_250_coins" ORDER BY slug, timestamp', h_conn,
    )
    h_conn.close()
    log.info(f"Loaded {len(ohlcv):,} hourly OHLCV rows")

    residuals = pd.read_sql(
        'SELECT slug, timestamp, residual_1h FROM "FE_BTC_RESIDUALS" '
        'WHERE residual_1h IS NOT NULL ORDER BY slug, timestamp', bt_conn,
    )
    log.info(f"Loaded {len(residuals):,} residual rows")

    labels_df = pd.read_sql(
        'SELECT slug, timestamp, label_3d FROM "ML_LABELS" WHERE label_3d IS NOT NULL', dbcp_conn,
    )
    bt_conn.close()
    dbcp_conn.close()

    # Build label lookup: (slug, date) -> label
    label_map = {-1: 0, 0: 1, 1: 2}
    labels_lookup = {}
    for _, r in labels_df.iterrows():
        labels_lookup[(r["slug"], pd.to_datetime(r["timestamp"]).date())] = label_map.get(int(r["label_3d"]), -1)

    slugs = [s for s in ohlcv["slug"].unique() if s != "bitcoin"]
    log.info(f"Building sequences for {len(slugs)} coins...")

    all_X, all_y = [], []

    for i, slug in enumerate(slugs):
        coin = ohlcv[ohlcv["slug"] == slug].copy()
        coin_res = residuals[residuals["slug"] == slug].copy()

        if len(coin) < SEQ_LEN + 10 or len(coin_res) < SEQ_LEN:
            continue

        features = build_hourly_features(coin, coin_res)
        values = features[FEATURE_COLS].values.astype(np.float32)
        values = np.nan_to_num(values, nan=0.0)
        timestamps = features["timestamp"].values

        for j in range(SEQ_LEN, len(values)):
            ts_date = pd.Timestamp(timestamps[j]).date()
            lbl = labels_lookup.get((slug, ts_date), -1)
            if lbl >= 0:
                seq = values[j - SEQ_LEN:j].T  # (8, 168) channels first
                all_X.append(seq)
                all_y.append(lbl)

        if (i + 1) % 50 == 0:
            log.info(f"  {i+1}/{len(slugs)} coins, {len(all_X):,} sequences so far")

    X = np.array(all_X, dtype=np.float32)
    y = np.array(all_y, dtype=np.int64)
    log.info(f"Training data: {X.shape[0]:,} sequences")

    # Walk-forward split
    split = int(len(X) * 0.8)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log.info(f"Training on {device}")

    model = TCNModel(INPUT_CHANNELS, HIDDEN_CHANNELS, EMBED_DIM, N_CLASSES).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss()

    train_ds = torch.utils.data.TensorDataset(
        torch.from_numpy(X_train), torch.from_numpy(y_train),
    )
    train_loader = torch.utils.data.DataLoader(train_ds, batch_size=batch_size, shuffle=True)

    for epoch in range(epochs):
        model.train()
        total_loss = 0
        for batch_x, batch_y in train_loader:
            batch_x, batch_y = batch_x.to(device), batch_y.to(device)
            optimizer.zero_grad()
            _, logits = model(batch_x)
            loss = criterion(logits, batch_y)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()

        if (epoch + 1) % 5 == 0:
            model.eval()
            with torch.no_grad():
                # Val in chunks to avoid OOM
                val_logits_list = []
                chunk = 512
                for ci in range(0, len(X_val), chunk):
                    vx = torch.from_numpy(X_val[ci:ci+chunk]).to(device)
                    _, vl = model(vx)
                    val_logits_list.append(vl.cpu())
                val_logits = torch.cat(val_logits_list, dim=0)
                val_y_t = torch.from_numpy(y_val)
                val_loss = criterion(val_logits, val_y_t).item()
                val_acc = (val_logits.argmax(dim=1) == val_y_t).float().mean().item()
            log.info(f"Epoch {epoch+1}/{epochs} — train_loss={total_loss/len(train_loader):.4f} "
                     f"val_loss={val_loss:.4f} val_acc={val_acc:.3f}")

    # Save
    artifact_dir = Path("artifacts")
    artifact_dir.mkdir(exist_ok=True)
    torch.save(model.state_dict(), ARTIFACT_PATH)
    log.info(f"Saved to {ARTIFACT_PATH}")

    # Export ONNX (optional — requires onnxscript)
    try:
        model.eval()
        model.cpu()
        dummy = torch.randn(1, INPUT_CHANNELS, SEQ_LEN)
        torch.onnx.export(
            model, dummy, ONNX_PATH,
            input_names=["input"], output_names=["embedding", "logits"],
            dynamic_axes={"input": {0: "batch"}, "embedding": {0: "batch"}, "logits": {0: "batch"}},
        )
        log.info(f"ONNX exported to {ONNX_PATH}")
    except Exception as e:
        log.warning(f"ONNX export skipped: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TCN Trainer")
    parser.add_argument("--train", action="store_true")
    args = parser.parse_args()

    if args.train:
        train_model()
