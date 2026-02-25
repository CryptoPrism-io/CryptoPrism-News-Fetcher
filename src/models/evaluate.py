"""
evaluate.py
Evaluation metrics for ML trading models.
  - Information Coefficient (IC): Spearman rank correlation of signal vs forward return
  - ICIR: IC / std(IC) — measures consistency
  - Classification: accuracy, precision, recall, F1
  - Portfolio simulation: Sharpe, max drawdown, win rate
"""

import logging
import numpy as np
from scipy import stats

log = logging.getLogger(__name__)


def information_coefficient(signal: np.ndarray, forward_return: np.ndarray) -> float:
    """
    Spearman rank IC between signal scores and actual forward returns.
    Industry standard: IC > 0.05 = useful, > 0.10 = strong.
    """
    mask = ~(np.isnan(signal) | np.isnan(forward_return))
    if mask.sum() < 10:
        return float("nan")
    ic, _ = stats.spearmanr(signal[mask], forward_return[mask])
    return round(float(ic), 6)


def rolling_ic(
    signal: np.ndarray,
    forward_return: np.ndarray,
    dates: np.ndarray,
    window_days: int = 20,
) -> dict:
    """
    Compute rolling IC over time windows and return mean/std/ICIR.
    dates: array of date strings or timestamps, same length as signal.
    """
    unique_dates = sorted(set(dates))
    ic_series = []

    for i in range(window_days, len(unique_dates)):
        window_dates = set(unique_dates[i - window_days:i])
        mask = np.array([d in window_dates for d in dates])
        if mask.sum() < 10:
            continue
        ic = information_coefficient(signal[mask], forward_return[mask])
        if not np.isnan(ic):
            ic_series.append(ic)

    if not ic_series:
        return {"ic_mean": float("nan"), "ic_std": float("nan"), "icir": float("nan")}

    ic_arr = np.array(ic_series)
    mean = float(np.mean(ic_arr))
    std  = float(np.std(ic_arr))
    icir = mean / std if std > 0 else float("nan")

    return {
        "ic_mean": round(mean, 6),
        "ic_std":  round(std, 6),
        "icir":    round(icir, 4),
    }


def classification_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """
    3-class classification metrics focused on BUY class (label=1).
    """
    from sklearn.metrics import accuracy_score, precision_recall_fscore_support, confusion_matrix

    mask = ~np.isnan(y_true.astype(float))
    yt = y_true[mask].astype(int)
    yp = y_pred[mask].astype(int)

    acc = accuracy_score(yt, yp)
    prec, rec, f1, _ = precision_recall_fscore_support(
        yt, yp, labels=[1], average=None, zero_division=0
    )
    prec, rec, f1 = float(prec[0]), float(rec[0]), float(f1[0])

    return {
        "accuracy":      round(float(acc), 4),
        "precision_buy": round(float(prec), 4),
        "recall_buy":    round(float(rec), 4),
        "f1_buy":        round(float(f1), 4),
    }


def portfolio_simulation(
    signal: np.ndarray,
    forward_return: np.ndarray,
    dates: np.ndarray,
    top_n: int = 20,
    hold_days: int = 3,
) -> dict:
    """
    Simple long-only portfolio: each day go long top_n coins by signal score.
    Returns Sharpe, max drawdown, total return, win rate.
    """
    unique_dates = sorted(set(dates))
    daily_pnl = []

    for d in unique_dates:
        mask = np.array([x == d for x in dates])
        if mask.sum() < top_n:
            continue
        sig_d = signal[mask]
        ret_d = forward_return[mask]

        # Drop NaN returns
        valid = ~np.isnan(ret_d)
        if valid.sum() < top_n:
            continue

        # Rank by signal, take top_n
        top_idx = np.argsort(sig_d[valid])[-top_n:]
        pnl = float(np.mean(ret_d[valid][top_idx]))
        daily_pnl.append(pnl)

    if len(daily_pnl) < 5:
        return {
            "sharpe": float("nan"), "max_drawdown": float("nan"),
            "total_return": float("nan"), "win_rate": float("nan"),
            "total_trades": 0, "avg_holding_days": hold_days,
        }

    pnl_arr  = np.array(daily_pnl)
    mean_ret = np.mean(pnl_arr)
    std_ret  = np.std(pnl_arr)
    sharpe   = (mean_ret / std_ret * np.sqrt(252)) if std_ret > 0 else float("nan")

    cum   = np.cumprod(1 + pnl_arr)
    peak  = np.maximum.accumulate(cum)
    dd    = (cum - peak) / peak
    max_dd = float(np.min(dd))

    return {
        "sharpe":           round(float(sharpe), 4),
        "max_drawdown":     round(max_dd, 4),
        "total_return":     round(float(cum[-1] - 1), 4),
        "win_rate":         round(float(np.mean(pnl_arr > 0)), 4),
        "total_trades":     int(len(daily_pnl) * top_n),
        "avg_holding_days": hold_days,
    }


def full_eval(
    signal: np.ndarray,
    y_true_label: np.ndarray,
    y_pred_label: np.ndarray,
    forward_ret_1d: np.ndarray,
    forward_ret_3d: np.ndarray,
    forward_ret_7d: np.ndarray,
    dates: np.ndarray,
) -> dict:
    """Run all metrics and return combined dict for ML_BACKTEST_RESULTS."""
    ic_1d = information_coefficient(signal, forward_ret_1d)
    ic_3d = information_coefficient(signal, forward_ret_3d)
    ic_7d = information_coefficient(signal, forward_ret_7d)

    roll  = rolling_ic(signal, forward_ret_3d, dates)
    clf   = classification_metrics(y_true_label, y_pred_label)
    port  = portfolio_simulation(signal, forward_ret_3d, dates)

    log.info(
        f"IC: 1d={ic_1d:.4f} 3d={ic_3d:.4f} 7d={ic_7d:.4f} | "
        f"ICIR={roll['icir']:.2f} | Acc={clf['accuracy']:.3f} | "
        f"Sharpe={port['sharpe']:.2f} | MaxDD={port['max_drawdown']:.2%}"
    )

    return {
        "ic_1d":        ic_1d,
        "ic_3d":        ic_3d,
        "ic_7d":        ic_7d,
        "ic_3d_mean":   roll["ic_mean"],
        "ic_3d_std":    roll["ic_std"],
        "icir":         roll["icir"],
        **clf,
        **port,
    }
