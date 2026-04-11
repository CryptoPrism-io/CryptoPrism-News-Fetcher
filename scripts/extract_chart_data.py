"""Extract quant data for the architecture report charts."""
from dotenv import load_dotenv; load_dotenv()
import os, json, pickle, numpy as np, pandas as pd, psycopg2, sys
from datetime import timedelta
from collections import defaultdict
from scipy.stats import spearmanr
from sklearn.metrics import confusion_matrix, roc_curve, auc

with open('artifacts/lgbm_ensemble_v1.pkl', 'rb') as f:
    artifact = pickle.load(f)
model = artifact['model']; features = artifact['features']; label_remap = artifact['label_remap']

bt = psycopg2.connect(host=os.environ['DB_HOST'], port='5432', dbname='cp_backtest',
    user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'], sslmode='require')
dbcp = psycopg2.connect(host=os.environ['DB_HOST'], port='5432', dbname='dbcp',
    user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'], sslmode='require')

# 1. BTC vs ETH residual decomposition
print('1. Residual decomposition...')
res = pd.read_sql("""
    SELECT DATE(timestamp) as d, AVG(residual_1h) as residual, AVG(beta_30d) as beta
    FROM "FE_BTC_RESIDUALS" WHERE slug='ethereum' AND residual_1h IS NOT NULL
    GROUP BY DATE(timestamp) ORDER BY d
""", bt)
btc_ret = pd.read_sql("""
    SELECT DATE(timestamp) as d, (close / LAG(close) OVER (ORDER BY timestamp) - 1) as ret
    FROM "1K_coins_ohlcv" WHERE slug='bitcoin' AND timestamp >= '2025-03-01' ORDER BY timestamp
""", bt)
eth_ret = pd.read_sql("""
    SELECT DATE(timestamp) as d, (close / LAG(close) OVER (ORDER BY timestamp) - 1) as ret
    FROM "1K_coins_ohlcv" WHERE slug='ethereum' AND timestamp >= '2025-03-01' ORDER BY timestamp
""", bt)
merged = btc_ret.merge(eth_ret, on='d', suffixes=('_btc','_eth')).merge(res, on='d', how='left').dropna().tail(60)
residual_data = {
    'dates': [str(d)[-5:] for d in merged['d']],
    'btc_ret': [round(float(r)*100, 2) for r in merged['ret_btc']],
    'eth_ret': [round(float(r)*100, 2) for r in merged['ret_eth']],
    'eth_residual': [round(float(r)*100, 2) for r in merged['residual']],
    'beta': [round(float(b), 2) for b in merged['beta']],
}
print(f'  {len(residual_data["dates"])} days')

# 2. Regime transitions
print('2. Regime states...')
regime_df = pd.read_sql("""
    SELECT DATE(timestamp) as d, regime_state, confidence
    FROM "ML_REGIME" WHERE timestamp >= '2025-06-01' ORDER BY timestamp
""", dbcp)
regime_data = {
    'dates': [str(d)[-5:] for d in regime_df['d']],
    'states': regime_df['regime_state'].tolist(),
    'confidence': [round(float(c), 2) if c else 0.5 for c in regime_df['confidence']],
}
state_counts = regime_df['regime_state'].value_counts().to_dict()
print(f'  {len(regime_data["dates"])} days, {state_counts}')

# 3. Test set predictions for ROC + confusion matrix
print('3. ROC + Confusion...')
from src.models.train_lgbm import compute_splits
split = compute_splits('news_augmented')

labels = pd.read_sql(f"""
    SELECT slug, timestamp, label_3d FROM "ML_LABELS"
    WHERE timestamp >= '{split["test_from"]} 00:00:00+00' AND timestamp <= '{split["test_to"]} 23:59:59+00'
    AND label_3d IS NOT NULL ORDER BY timestamp
""", dbcp)

price_df = pd.read_sql(f"""
    SELECT DISTINCT ON (pct.slug, DATE(pct.timestamp)) pct.slug, DATE(pct.timestamp) as fd,
        pct.m_pct_1d, pct.d_pct_cum_ret, pct.d_pct_var, pct.d_pct_cvar, pct.d_pct_vol_1d,
        mom.m_mom_roc_bin, mom."m_mom_williams_%_bin", mom.m_mom_smi_bin, mom.m_mom_cmo_bin, mom.m_mom_mom_bin,
        osc.m_osc_macd_crossover_bin, osc.m_osc_cci_bin, osc.m_osc_adx_bin, osc.m_osc_uo_bin, osc.m_osc_ao_bin, osc.m_osc_trix_bin,
        tvv.m_tvv_obv_1d_binary, tvv.d_tvv_sma9_18, tvv.d_tvv_ema9_18, tvv.d_tvv_sma21_108, tvv.d_tvv_ema21_108, tvv.m_tvv_cmf,
        rat.m_rat_alpha_bin, rat.d_rat_beta_bin, rat.v_rat_sharpe_bin, rat.v_rat_sortino_bin, rat.v_rat_teynor_bin, rat.v_rat_common_sense_bin, rat.v_rat_information_bin, rat.v_rat_win_loss_bin, rat.m_rat_win_rate_bin, rat.m_rat_ror_bin, rat.d_rat_pain_bin
    FROM "FE_PCT_CHANGE" pct
    LEFT JOIN "FE_MOMENTUM_SIGNALS" mom ON mom.slug=pct.slug AND DATE(mom.timestamp)=DATE(pct.timestamp)
    LEFT JOIN "FE_OSCILLATORS_SIGNALS" osc ON osc.slug=pct.slug AND DATE(osc.timestamp)=DATE(pct.timestamp)
    LEFT JOIN "FE_TVV_SIGNALS" tvv ON tvv.slug=pct.slug AND DATE(tvv.timestamp)=DATE(pct.timestamp)
    LEFT JOIN "FE_RATIOS_SIGNALS" rat ON rat.slug=pct.slug AND DATE(rat.timestamp)=DATE(pct.timestamp)
    WHERE pct.timestamp >= '{split["test_from"]} 00:00:00+00' AND pct.timestamp <= '{split["test_to"]} 23:59:59+00'
    ORDER BY pct.slug, DATE(pct.timestamp), pct.timestamp DESC
""", bt)

labels['fd'] = pd.to_datetime(labels['timestamp']).dt.date
test_df = labels.merge(price_df, on=['slug','fd'], how='left')
for f in features:
    if f not in test_df.columns: test_df[f] = np.nan

X_test = np.nan_to_num(test_df[features].values.astype(np.float32), nan=0.0)
y_true = test_df['label_3d'].map({-1:0, 0:1, 1:2}).values.astype(int)
y_true_buy = (test_df['label_3d'].values == 1).astype(int)

probs = model.predict_proba(X_test)
y_pred = np.argmax(probs, axis=1)

fpr, tpr, _ = roc_curve(y_true_buy, probs[:, 2])
roc_auc = auc(fpr, tpr)
step = max(1, len(fpr) // 80)
roc_data = {'fpr': [round(float(f),4) for f in fpr[::step]], 'tpr': [round(float(t),4) for t in tpr[::step]], 'auc': round(roc_auc, 4)}

cm = confusion_matrix(y_true, y_pred)
cm_data = {'matrix': cm.tolist(), 'labels': ['SELL','HOLD','BUY'], 'accuracy': round(float(np.trace(cm)/cm.sum()), 4)}
print(f'  ROC AUC: {roc_auc:.4f}, Accuracy: {cm_data["accuracy"]}')

# 4. Backtest daily IC + cumulative PnL
print('4. Backtest IC + PnL...')
prices_all = pd.read_sql("""
    SELECT slug, DATE(timestamp) as d, close FROM "1K_coins_ohlcv"
    WHERE timestamp >= '2026-03-20 00:00:00+00' AND timestamp <= '2026-04-12 23:59:59+00'
""", dbcp)
price_map = defaultdict(dict)
for _, r in prices_all.iterrows(): price_map[r['slug']][r['d']] = float(r['close'])

bt_signals = pd.read_sql(f"""
    SELECT DISTINCT ON (pct.slug, DATE(pct.timestamp)) pct.slug, DATE(pct.timestamp) as fd,
        pct.m_pct_1d, pct.d_pct_cum_ret, pct.d_pct_var, pct.d_pct_cvar, pct.d_pct_vol_1d,
        mom.m_mom_roc_bin, mom."m_mom_williams_%_bin", mom.m_mom_smi_bin, mom.m_mom_cmo_bin, mom.m_mom_mom_bin,
        osc.m_osc_macd_crossover_bin, osc.m_osc_cci_bin, osc.m_osc_adx_bin, osc.m_osc_uo_bin, osc.m_osc_ao_bin, osc.m_osc_trix_bin,
        tvv.m_tvv_obv_1d_binary, tvv.d_tvv_sma9_18, tvv.d_tvv_ema9_18, tvv.d_tvv_sma21_108, tvv.d_tvv_ema21_108, tvv.m_tvv_cmf,
        rat.m_rat_alpha_bin, rat.d_rat_beta_bin, rat.v_rat_sharpe_bin, rat.v_rat_sortino_bin, rat.v_rat_teynor_bin, rat.v_rat_common_sense_bin, rat.v_rat_information_bin, rat.v_rat_win_loss_bin, rat.m_rat_win_rate_bin, rat.m_rat_ror_bin, rat.d_rat_pain_bin
    FROM "FE_PCT_CHANGE" pct
    LEFT JOIN "FE_MOMENTUM_SIGNALS" mom ON mom.slug=pct.slug AND DATE(mom.timestamp)=DATE(pct.timestamp)
    LEFT JOIN "FE_OSCILLATORS_SIGNALS" osc ON osc.slug=pct.slug AND DATE(osc.timestamp)=DATE(pct.timestamp)
    LEFT JOIN "FE_TVV_SIGNALS" tvv ON tvv.slug=pct.slug AND DATE(tvv.timestamp)=DATE(pct.timestamp)
    LEFT JOIN "FE_RATIOS_SIGNALS" rat ON rat.slug=pct.slug AND DATE(rat.timestamp)=DATE(pct.timestamp)
    WHERE pct.timestamp >= '2026-03-25 00:00:00+00' AND pct.timestamp <= '2026-04-07 23:59:59+00'
    ORDER BY pct.slug, DATE(pct.timestamp), pct.timestamp DESC
""", bt)
for f in features:
    if f not in bt_signals.columns: bt_signals[f] = np.nan

bt_results = []
for _, row in bt_signals.iterrows():
    X = np.nan_to_num(np.array([[row.get(f, np.nan) for f in features]], dtype=np.float32), nan=0.0)
    p = model.predict_proba(X)
    score = float(p[0,2] - p[0,0])
    slug = row['slug']; d = row['fd']
    p0 = price_map.get(slug, {}).get(d)
    if not p0 or slug in ('tether','usd-coin'): continue
    r3 = None
    for delta in [3,4,5]:
        px = price_map.get(slug, {}).get(d + timedelta(days=delta))
        if px: r3 = (px - p0) / p0; break
    if r3 is not None:
        bt_results.append({'date': d, 'slug': slug, 'score': score, 'r3': r3})

bt_rdf = pd.DataFrame(bt_results)
daily_ic = []
cum_pnl = {'dates': [], 'long': [], 'short': [], 'net': []}
cl, cs = 0, 0
for d in sorted(bt_rdf['date'].unique()):
    day = bt_rdf[bt_rdf['date'] == d].dropna(subset=['r3'])
    if len(day) < 20: continue
    ic, _ = spearmanr(day['score'], day['r3'])
    daily_ic.append({'date': str(d)[-5:], 'ic': round(ic, 4)})
    day = day.sort_values('score', ascending=False)
    n = len(day); q = max(n//4, 1)
    tr = day.head(q)['r3'].mean() * 100
    br = day.tail(q)['r3'].mean() * 100
    cl += tr; cs -= br
    cum_pnl['dates'].append(str(d)[-5:])
    cum_pnl['long'].append(round(cl, 2))
    cum_pnl['short'].append(round(cs, 2))
    cum_pnl['net'].append(round(cl + cs, 2))
print(f'  {len(daily_ic)} IC days')

# 5. Feature importance
print('5. Feature importance...')
importances = model.feature_importances_
feat_imp = sorted(zip(features, importances), key=lambda x: -x[1])[:15]
feat_imp_data = {'features': [f[0] for f in feat_imp], 'importance': [int(f[1]) for f in feat_imp]}

# 6. IC by horizon
print('6. IC by horizon...')
ic_horizon = {}
for hz, days in [('1d', 1), ('3d', 3), ('7d', 7)]:
    rets = []
    for _, row in bt_signals.iterrows():
        slug = row['slug']; d = row['fd']
        p0 = price_map.get(slug, {}).get(d)
        if not p0 or slug in ('tether','usd-coin'): continue
        px = None
        for delta in range(days, days+4):
            px = price_map.get(slug, {}).get(d + timedelta(days=delta))
            if px: break
        if px:
            X = np.nan_to_num(np.array([[row.get(f, np.nan) for f in features]], dtype=np.float32), nan=0.0)
            p = model.predict_proba(X)
            rets.append({'score': float(p[0,2]-p[0,0]), 'ret': (px-p0)/p0})
    if rets:
        rdf = pd.DataFrame(rets)
        ic, _ = spearmanr(rdf['score'], rdf['ret'])
        ic_horizon[hz] = round(ic, 4)
print(f'  {ic_horizon}')

bt.close(); dbcp.close()

all_data = {
    'residual': residual_data, 'regime': regime_data, 'regime_counts': state_counts,
    'roc': roc_data, 'confusion': cm_data, 'daily_ic': daily_ic,
    'cum_pnl': cum_pnl, 'feat_importance': feat_imp_data, 'ic_horizon': ic_horizon,
}
with open('chart_data.json', 'w') as f:
    json.dump(all_data, f, indent=2)
print('\nSaved to chart_data.json')
