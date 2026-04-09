"""
lstm_extractor.py
2-layer LSTM on 30-day daily sequences for temporal feature extraction.
Outputs 12-dim embedding per coin per day.

Usage:
    python -m src.models.lstm_extractor --train
    python -m src.models.lstm_extractor --export-onnx
"""

import argparse
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
import torch
import torch.nn as nn
from dotenv import load_dotenv

from src.db import get_db_conn, get_backtest_conn

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SEQ_LEN = 30
INPUT_DIM = 12
HIDDEN_DIM = 64
EMBED_DIM = 12
N_CLASSES = 3
ARTIFACT_PATH = "artifacts/lstm_extractor.pt"
ONNX_PATH = "artifacts/lstm_extractor.onnx"

FEATURE_COLS = [
    "residual_1d", "residual_volume_1d",
    "close_ret", "daily_range",
    "volume_zscore",
    "residual_vol_7d", "residual_vol_14d",
    "momentum_rank",
    "news_sentiment_1d", "news_volume_1d",
    "fear_greed_index",
    "market_breadth",
]


class LSTMExtractor(nn.Module):
    """2-layer LSTM with embedding + classification heads."""

    def __init__(self, input_dim: int = INPUT_DIM, hidden_dim: int = HIDDEN_DIM,
                 embed_dim: int = EMBED_DIM, n_classes: int = N_CLASSES):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_dim, hidden_size=hidden_dim,
            num_layers=2, dropout=0.3, batch_first=True,
        )
        self.embed_head = nn.Linear(hidden_dim, embed_dim)
        self.class_head = nn.Linear(hidden_dim, n_classes)

    def forward(self, x: torch.Tensor):
        _, (h_n, _) = self.lstm(x)
        hidden = h_n[-1]  # last layer's hidden state
        return self.embed_head(hidden), self.class_head(hidden)


def build_sequences(df: pd.DataFrame, seq_len: int = SEQ_LEN,
                    feature_cols: list[str] = FEATURE_COLS) -> tuple[np.ndarray, list]:
    """Build sliding window sequences from per-coin daily DataFrame."""
    values = df[feature_cols].values.astype(np.float32)
    timestamps_raw = df["timestamp"].values
    n = len(values)
    if n < seq_len:
        return np.empty((0, seq_len, len(feature_cols)), dtype=np.float32), []

    n_seq = n - seq_len + 1
    sequences = np.zeros((n_seq, seq_len, len(feature_cols)), dtype=np.float32)
    timestamps = []
    for i in range(n_seq):
        sequences[i] = values[i:i + seq_len]
        timestamps.append(timestamps_raw[i + seq_len - 1])

    sequences = np.nan_to_num(sequences, nan=0.0)
    return sequences, timestamps


def _build_coin_features(coin_ohlcv: pd.DataFrame, coin_res: pd.DataFrame,
                         fg_map: dict, label_map: dict) -> tuple[np.ndarray, np.ndarray, list]:
    """Build sequences + labels for one coin. Returns (X, y, timestamps)."""
    ohlcv_sub = coin_ohlcv[["slug", "timestamp", "close", "volume", "high", "low"]].copy()
    ohlcv_sub["_date"] = pd.to_datetime(ohlcv_sub["timestamp"]).dt.date
    ohlcv_sub = ohlcv_sub.drop_duplicates(subset=["slug", "_date"], keep="last")
    res_sub = coin_res.copy()
    res_sub["_date"] = pd.to_datetime(res_sub["timestamp"]).dt.date
    coin = res_sub.merge(
        ohlcv_sub[["slug", "_date", "close", "volume", "high", "low"]],
        on=["slug", "_date"], how="left",
    ).sort_values("_date").drop_duplicates(subset=["slug", "_date"], keep="last")

    if len(coin) < SEQ_LEN + 5:
        return np.empty((0,)), np.empty((0,)), []

    coin["close_ret"] = coin["close"].pct_change().fillna(0)
    coin["daily_range"] = (coin["high"] - coin["low"]) / coin["close"].replace(0, np.nan)
    vol_mean = coin["volume"].rolling(30).mean()
    vol_std = coin["volume"].rolling(30).std().replace(0, np.nan)
    coin["volume_zscore"] = (coin["volume"] - vol_mean) / vol_std
    coin["residual_vol_7d"] = coin["residual_1d"].rolling(7).std()
    coin["residual_vol_14d"] = coin["residual_1d"].rolling(14).std()
    coin["residual_volume_1d"] = coin["volume"].pct_change().fillna(0)
    coin["momentum_rank"] = 0.5
    coin["news_sentiment_1d"] = 0.0
    coin["news_volume_1d"] = 0.0
    coin["fear_greed_index"] = coin["_date"].map(fg_map).fillna(50)
    coin["market_breadth"] = 0.5

    sequences, ts = build_sequences(coin, SEQ_LEN, FEATURE_COLS)
    if len(sequences) == 0:
        return np.empty((0,)), np.empty((0,)), []

    slug = coin["slug"].iloc[0]
    lbl_map_3d = {-1: 0, 0: 1, 1: 2}
    seq_labels = []
    for t in ts:
        key = (slug, pd.Timestamp(t).date() if hasattr(pd.Timestamp(t), 'date') else t)
        lbl = label_map.get(key)
        seq_labels.append(lbl_map_3d.get(lbl, -1) if lbl is not None else -1)

    # Filter valid
    valid_idx = [i for i, l in enumerate(seq_labels) if l >= 0]
    if not valid_idx:
        return np.empty((0,)), np.empty((0,)), []

    X = sequences[valid_idx]
    y = np.array([seq_labels[i] for i in valid_idx], dtype=np.int64)
    valid_ts = [ts[i] for i in valid_idx]
    return X, y, valid_ts


def train_model(epochs: int = 30, lr: float = 1e-3, batch_size: int = 256):
    """Train LSTM on historical daily residual data."""
    bt_conn = get_backtest_conn()
    dbcp_conn = get_db_conn()

    log.info("Loading hourly residuals and aggregating to daily...")
    res_hourly = pd.read_sql(
        'SELECT slug, DATE(timestamp) as date, '
        '  SUM(residual_1h) as residual_1d, '
        '  AVG(residual_vol_ratio) as residual_vol_ratio, '
        '  AVG(beta_30d) as beta_30d '
        'FROM "FE_BTC_RESIDUALS" '
        'WHERE residual_1h IS NOT NULL '
        'GROUP BY slug, DATE(timestamp) '
        'ORDER BY slug, date',
        bt_conn,
    )
    res_hourly = res_hourly.rename(columns={"date": "timestamp"})
    res_hourly["timestamp"] = pd.to_datetime(res_hourly["timestamp"], utc=True)
    res_df = res_hourly
    log.info(f"Loaded {len(res_df):,} daily residual rows, {res_df['slug'].nunique()} coins")

    log.info("Loading daily OHLCV...")
    ohlcv_df = pd.read_sql(
        'SELECT slug, timestamp, close, volume, high, low FROM "1K_coins_ohlcv" ORDER BY slug, timestamp',
        bt_conn,
    )

    fg_df = pd.read_sql(
        'SELECT timestamp, fear_greed_index FROM "FE_FEAR_GREED_CMC"', dbcp_conn,
    )
    fg_map = dict(zip(pd.to_datetime(fg_df["timestamp"]).dt.date, fg_df["fear_greed_index"]))

    labels_df = pd.read_sql(
        'SELECT slug, timestamp, label_3d FROM "ML_LABELS" WHERE label_3d IS NOT NULL', dbcp_conn,
    )
    label_map = {}
    for _, r in labels_df.iterrows():
        label_map[(r["slug"], pd.to_datetime(r["timestamp"]).date())] = int(r["label_3d"])

    bt_conn.close()
    dbcp_conn.close()

    log.info("Building sequences per coin...")
    all_X, all_y = [], []
    slugs = res_df["slug"].unique()

    for i, slug in enumerate(slugs):
        X, y, _ = _build_coin_features(
            ohlcv_df[ohlcv_df["slug"] == slug],
            res_df[res_df["slug"] == slug],
            fg_map, label_map,
        )
        if len(X) > 0:
            all_X.append(X)
            all_y.append(y)
        if (i + 1) % 100 == 0:
            log.info(f"  {i+1}/{len(slugs)} coins processed")

    X = np.concatenate(all_X, axis=0)
    y = np.concatenate(all_y, axis=0)
    log.info(f"Training data: {X.shape[0]:,} sequences, {X.shape[1]} steps, {X.shape[2]} features")

    # Walk-forward split (80/20)
    split = int(len(X) * 0.8)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log.info(f"Training on {device}")

    model = LSTMExtractor(INPUT_DIM, HIDDEN_DIM, EMBED_DIM, N_CLASSES).to(device)
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
                val_x = torch.from_numpy(X_val).to(device)
                val_y = torch.from_numpy(y_val).to(device)
                _, val_logits = model(val_x)
                val_loss = criterion(val_logits, val_y).item()
                val_acc = (val_logits.argmax(dim=1) == val_y).float().mean().item()
            log.info(f"Epoch {epoch+1}/{epochs} — train_loss={total_loss/len(train_loader):.4f} "
                     f"val_loss={val_loss:.4f} val_acc={val_acc:.3f}")

    # Save
    artifact_dir = Path("artifacts")
    artifact_dir.mkdir(exist_ok=True)
    torch.save(model.state_dict(), ARTIFACT_PATH)
    log.info(f"Model saved to {ARTIFACT_PATH}")

    # Export ONNX (optional — requires onnxscript)
    try:
        model.eval()
        model.cpu()
        dummy = torch.randn(1, SEQ_LEN, INPUT_DIM)
        torch.onnx.export(
            model, dummy, ONNX_PATH,
            input_names=["input"], output_names=["embedding", "logits"],
            dynamic_axes={"input": {0: "batch"}, "embedding": {0: "batch"}, "logits": {0: "batch"}},
        )
        log.info(f"ONNX exported to {ONNX_PATH}")
    except Exception as e:
        log.warning(f"ONNX export skipped: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LSTM Feature Extractor")
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--export-onnx", action="store_true")
    args = parser.parse_args()

    if args.train:
        train_model()
