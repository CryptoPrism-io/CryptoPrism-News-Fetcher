"""
backfill_embeddings.py
Backfill LSTM and TCN embedding tables from trained models.
Designed to run on GitHub Actions (GCP-to-GCP for fast DB access).

Usage:
    python -m src.inference.backfill_embeddings --model lstm
    python -m src.inference.backfill_embeddings --model tcn
    python -m src.inference.backfill_embeddings --model both
"""

import argparse
import io
import logging
import sys

import numpy as np
import pandas as pd
import torch
from dotenv import load_dotenv

from src.db import get_backtest_conn, get_backtest_h_conn, get_db_conn

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


def backfill_lstm():
    """Backfill ML_LSTM_EMBEDDINGS from trained LSTM model."""
    from src.models.lstm_extractor import (
        LSTMExtractor, FEATURE_COLS, SEQ_LEN,
        INPUT_DIM, HIDDEN_DIM, EMBED_DIM, N_CLASSES,
    )

    model = LSTMExtractor(INPUT_DIM, HIDDEN_DIM, EMBED_DIM, N_CLASSES)
    model.load_state_dict(torch.load("artifacts/lstm_extractor.pt", map_location="cpu", weights_only=True))
    model.eval()
    log.info("LSTM loaded on CPU")

    bt = get_backtest_conn()
    dbcp = get_db_conn()

    slugs = pd.read_sql('SELECT DISTINCT slug FROM "FE_BTC_RESIDUALS"', bt)["slug"].tolist()
    log.info(f"{len(slugs)} coins with residuals")

    fg_df = pd.read_sql('SELECT timestamp, fear_greed_index FROM "FE_FEAR_GREED_CMC"', dbcp)
    fg_map = dict(zip(pd.to_datetime(fg_df["timestamp"]).dt.date, fg_df["fear_greed_index"]))
    dbcp.close()

    all_rows = []
    for i, slug in enumerate(slugs):
        res = pd.read_sql(
            f"SELECT DATE(timestamp) as d, SUM(residual_1h) as residual_1d, "
            f"AVG(residual_vol_ratio) as residual_vol_ratio "
            f'FROM "FE_BTC_RESIDUALS" WHERE slug=\'{slug}\' AND residual_1h IS NOT NULL '
            f"GROUP BY DATE(timestamp) ORDER BY d", bt)
        ohlcv = pd.read_sql(
            f"SELECT DATE(timestamp) as d, close, volume, high, low "
            f'FROM "1K_coins_ohlcv" WHERE slug=\'{slug}\' ORDER BY timestamp', bt)
        ohlcv = ohlcv.drop_duplicates(subset=["d"], keep="last")
        coin = res.merge(ohlcv, on="d", how="left").sort_values("d")
        if len(coin) < SEQ_LEN + 5:
            continue

        coin["close_ret"] = coin["close"].pct_change().fillna(0)
        coin["daily_range"] = (coin["high"] - coin["low"]) / coin["close"].replace(0, np.nan)
        coin["volume_zscore"] = (coin["volume"] - coin["volume"].rolling(30).mean()) / coin["volume"].rolling(30).std().replace(0, np.nan)
        coin["residual_vol_7d"] = coin["residual_1d"].rolling(7).std()
        coin["residual_vol_14d"] = coin["residual_1d"].rolling(14).std()
        coin["residual_volume_1d"] = coin["volume"].pct_change().fillna(0)
        coin["momentum_rank"] = 0.5
        coin["news_sentiment_1d"] = 0.0
        coin["news_volume_1d"] = 0.0
        coin["fear_greed_index"] = coin["d"].map(fg_map).fillna(50)
        coin["market_breadth"] = 0.5

        values = coin[FEATURE_COLS].values.astype(np.float32)
        values = np.nan_to_num(values, nan=0.0)

        seqs = [values[j - SEQ_LEN:j] for j in range(SEQ_LEN, len(values))]
        ts_list = [coin.iloc[j]["d"] for j in range(SEQ_LEN, len(values))]
        if not seqs:
            continue

        X = torch.from_numpy(np.array(seqs, dtype=np.float32))
        with torch.no_grad():
            embs, logits = model(X)
        embs = embs.numpy()
        probs = torch.softmax(logits, dim=1).numpy()

        for j in range(len(seqs)):
            row = {"slug": slug, "timestamp": str(ts_list[j])}
            for k in range(12):
                row[f"lemb_{k}"] = round(float(embs[j, k]), 6)
            row["lstm_prob_buy"] = round(float(probs[j, 2]), 6)
            row["lstm_prob_sell"] = round(float(probs[j, 0]), 6)
            all_rows.append(row)

        if (i + 1) % 50 == 0:
            log.info(f"  {i+1}/{len(slugs)} coins, {len(all_rows):,} embeddings")

    log.info(f"Total: {len(all_rows):,} LSTM embeddings. Writing...")
    _write_lstm_embeddings(bt, all_rows)
    bt.close()
    log.info("LSTM embeddings done!")


def backfill_tcn():
    """Backfill ML_TCN_EMBEDDINGS from trained TCN model."""
    from src.models.tcn import (
        TCNModel, FEATURE_COLS, SEQ_LEN,
        INPUT_CHANNELS, HIDDEN_CHANNELS, EMBED_DIM, N_CLASSES,
        build_hourly_features,
    )

    model = TCNModel(INPUT_CHANNELS, HIDDEN_CHANNELS, EMBED_DIM, N_CLASSES)
    model.load_state_dict(torch.load("artifacts/tcn_model.pt", map_location="cpu", weights_only=True))
    model.eval()
    log.info("TCN loaded on CPU")

    h_conn = get_backtest_h_conn()
    bt_conn = get_backtest_conn()

    # Load hourly OHLCV
    ohlcv = pd.read_sql(
        'SELECT slug, timestamp, open, high, low, close, volume '
        'FROM "ohlcv_1h_250_coins" ORDER BY slug, timestamp', h_conn)
    h_conn.close()
    log.info(f"Loaded {len(ohlcv):,} hourly rows")

    # Load residuals
    residuals = pd.read_sql(
        'SELECT slug, timestamp, residual_1h FROM "FE_BTC_RESIDUALS" '
        'WHERE residual_1h IS NOT NULL ORDER BY slug, timestamp', bt_conn)
    log.info(f"Loaded {len(residuals):,} residual rows")

    slugs = [s for s in ohlcv["slug"].unique() if s != "bitcoin"]
    log.info(f"Processing {len(slugs)} coins...")

    all_rows = []
    for i, slug in enumerate(slugs):
        coin = ohlcv[ohlcv["slug"] == slug].copy()
        coin_res = residuals[residuals["slug"] == slug].copy()
        if len(coin) < SEQ_LEN + 10 or len(coin_res) < SEQ_LEN:
            continue

        features = build_hourly_features(coin, coin_res)
        values = features[FEATURE_COLS].values.astype(np.float32)
        values = np.nan_to_num(values, nan=0.0)

        # Normalize (same as training)
        values_t = values.T  # (8, T)
        for ch in range(values_t.shape[0]):
            ch_mean = np.nanmean(values_t[ch])
            ch_std = np.nanstd(values_t[ch])
            if ch_std > 0:
                values_t[ch] = (values_t[ch] - ch_mean) / ch_std
        values_t = np.clip(values_t, -10, 10)
        values_t = np.nan_to_num(values_t, nan=0.0)

        timestamps = features["timestamp"].values

        # Build sequences in batch
        seqs = []
        ts_list = []
        for j in range(SEQ_LEN, len(values_t[0])):
            seqs.append(values_t[:, j - SEQ_LEN:j])  # (8, 168)
            ts_list.append(timestamps[j])

        if not seqs:
            continue

        # Process in chunks to avoid OOM
        chunk_size = 512
        for ci in range(0, len(seqs), chunk_size):
            batch = np.array(seqs[ci:ci + chunk_size], dtype=np.float32)
            X = torch.from_numpy(batch)
            with torch.no_grad():
                embs, logits = model(X)
            embs_np = embs.numpy()
            probs = torch.softmax(logits, dim=1).numpy()

            for j in range(len(batch)):
                idx = ci + j
                row = {"slug": slug, "timestamp": ts_list[idx]}
                for k in range(16):
                    row[f"emb_{k}"] = round(float(embs_np[j, k]), 6)
                row["tcn_prob_buy"] = round(float(probs[j, 2]), 6)
                row["tcn_prob_sell"] = round(float(probs[j, 0]), 6)
                row["tcn_direction"] = int(np.argmax(probs[j]) - 1)  # map 0/1/2 to -1/0/1
                all_rows.append(row)

        if (i + 1) % 50 == 0:
            log.info(f"  {i+1}/{len(slugs)} coins, {len(all_rows):,} embeddings")

    log.info(f"Total: {len(all_rows):,} TCN embeddings. Writing...")
    _write_tcn_embeddings(bt_conn, all_rows)
    bt_conn.close()
    log.info("TCN embeddings done!")


def _write_lstm_embeddings(conn, rows):
    cols = ["slug", "timestamp"] + [f"lemb_{k}" for k in range(12)] + ["lstm_prob_buy", "lstm_prob_sell"]
    _bulk_upsert(conn, "ML_LSTM_EMBEDDINGS", cols, rows,
                 conflict_cols=["slug", "timestamp"])


def _write_tcn_embeddings(conn, rows):
    cols = ["slug", "timestamp"] + [f"emb_{k}" for k in range(16)] + ["tcn_prob_buy", "tcn_prob_sell", "tcn_direction"]
    _bulk_upsert(conn, "ML_TCN_EMBEDDINGS", cols, rows,
                 conflict_cols=["slug", "timestamp"])


def _bulk_upsert(conn, table, cols, rows, conflict_cols, chunk_size=50000):
    """COPY + upsert in chunks."""
    update_cols = [c for c in cols if c not in conflict_cols]
    for start in range(0, len(rows), chunk_size):
        chunk = rows[start:start + chunk_size]
        df = pd.DataFrame(chunk)
        cur = conn.cursor()

        type_map = {c: "TEXT" if c == "slug" else "TIMESTAMPTZ" if c == "timestamp"
                    else "SMALLINT" if c == "tcn_direction" else "DOUBLE PRECISION"
                    for c in cols}
        col_defs = ", ".join(f"{c} {type_map[c]}" for c in cols)
        cur.execute(f"CREATE TEMP TABLE _staging ({col_defs}) ON COMMIT DROP")

        buf = io.StringIO()
        for _, r in df[cols].iterrows():
            vals = []
            for c in cols:
                v = r[c]
                if v is None or (isinstance(v, float) and np.isnan(v)):
                    vals.append("\\N")
                else:
                    vals.append(str(v))
            buf.write("\t".join(vals) + "\n")
        buf.seek(0)
        cur.copy_from(buf, "_staging", columns=cols, null="\\N")

        update_set = ", ".join(f"{c}=EXCLUDED.{c}" for c in update_cols)
        conflict = ", ".join(conflict_cols)
        cur.execute(
            f'INSERT INTO "{table}" ({",".join(cols)}) '
            f"SELECT {','.join(cols)} FROM _staging "
            f"ON CONFLICT ({conflict}) DO UPDATE SET {update_set}"
        )
        conn.commit()
        log.info(f"  Written {min(start + chunk_size, len(rows)):,}/{len(rows):,}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill neural embeddings")
    parser.add_argument("--model", choices=["lstm", "tcn", "both"], required=True)
    args = parser.parse_args()

    if args.model in ("lstm", "both"):
        backfill_lstm()
    if args.model in ("tcn", "both"):
        backfill_tcn()
