"""
Test du PEAD (post-earnings announcement drift) — RÉSULTAT NÉGATIF.

Conservé pour ne pas refaire le test : la question « et si on utilisait
les résultats trimestriels ? » reviendra, et la réponse est mesurée.

CE QUI A ÉTÉ TESTÉ
    L'anomalie documentée depuis Ball & Brown (1968) : après une bonne
    surprise de résultats, le titre continuerait de dériver à la hausse
    pendant plusieurs semaines. Signal = surprise de la dernière
    publication, active de J+1 à J+60. Le décalage d'un jour n'est pas
    cosmétique : beaucoup de sociétés publient après la clôture, un
    signal actif le jour même supposerait qu'on ait lu le communiqué
    avant sa parution.

CE QUE ÇA DONNE (49 valeurs, 1 503 séances, 2020-09 → 2026-09)
    IC transversal        : -0,0048  (49 % de séances positives)
    PEAD top 5            : 51ᵉ percentile du null  → indistinct du hasard
    PEAD top 12           : 80ᵉ percentile          → indistinct
    Sharpe top 12         : 1,044 contre 1,119 pour le panier
    Rotation              : 39-41 % contre 14-19 % pour le momentum

    Le momentum 12-1, sur exactement la même période, tient à 99ᵉ.

POURQUOI ÇA NE MARCHE PAS ICI
    L'anomalie est documentée depuis 1968 et exploitée par les fonds
    quantitatifs depuis des décennies. Elle survit surtout sur les
    petites capitalisations peu suivies : sur les 49 plus grosses
    sociétés américaines, l'information est intégrée en séance. À quoi
    s'ajoute une rotation deux à trois fois supérieure, donc des frais
    que rien ne vient compenser.

USAGE
    python3 pead_test.py
"""
import warnings, json, os, sys
warnings.filterwarnings('ignore')
sys.path.insert(0, '/Users/Mathis/test2/trading-bot')
os.chdir('/Users/Mathis/test2/trading-bot')
import numpy as np, pandas as pd, yfinance as yf
import stock_eval as se, stock_engine as engine

CACHE = 'data/earnings_history.json'

def charger_surprises(tickers, force=False):
    if os.path.exists(CACHE) and not force:
        return json.load(open(CACHE))
    out = {}
    for t in tickers:
        try:
            ed = yf.Ticker(t).earnings_dates
            if ed is None or not len(ed): continue
            idx = ed.index
            if getattr(idx, 'tz', None) is not None: idx = idx.tz_localize(None)
            s = pd.Series(ed['Surprise(%)'].values, index=idx).dropna()
            out[t] = {d.strftime('%Y-%m-%d'): float(v) for d, v in s.items()}
        except Exception:
            continue
    json.dump(out, open(CACHE, 'w'), indent=2)
    return out

def matrice_pead(surprises, dates, fenetre=60):
    """
    Signal PEAD : la surprise de la dernière publication, si elle date
    de 1 à `fenetre` jours.

    DÉCALAGE D'UN JOUR MINIMUM : la surprise n'est connue qu'APRÈS la
    publication. Beaucoup de sociétés publient après la clôture — un
    signal actif le jour même supposerait qu'on ait lu le communiqué
    avant qu'il ne sorte.
    """
    cols = {}
    for t, hist in surprises.items():
        pubs = sorted((pd.Timestamp(d), v) for d, v in hist.items())
        serie = pd.Series(index=dates, dtype=float)
        for d in dates:
            recentes = [(pd, v) for pd, v in pubs if 1 <= (d - pd).days <= fenetre]
            if recentes:
                serie[d] = recentes[-1][1]
        cols[t] = serie
    return pd.DataFrame(cols)

tickers = engine.UNIVERSE_TICKERS
print('Récupération des surprises…')
surprises = charger_surprises(tickers)
print(f'  {len(surprises)} valeurs, {sum(len(v) for v in surprises.values())} publications\n')

# cours alignés
sd_mom, cd = se.build_matrices(tickers, '10y', False, 'mom_12_1')
dates = list(sd_mom.index)
pead = matrice_pead(surprises, dates)

# on se limite à la période où le PEAD existe
valides = pead.dropna(how='all').index
pead = pead.loc[valides]
cd2 = cd.reindex(valides)
couverture = pead.notna().sum(axis=1)
print(f'Période PEAD : {len(valides)} séances, {str(valides[0])[:10]} → {str(valides[-1])[:10]}')
print(f'Valeurs avec signal actif : médiane {couverture.median():.0f}/49, min {couverture.min()}, max {couverture.max()}\n')

ic = se.cross_sectional_ic(pead, cd2, 10)
print(f"IC transversal PEAD : {ic['ic_moyen']:+.4f}  ({ic['part_positive']:.0f}% de séances positives, n={ic['n_seances']})")

# comparaison : momentum sur la MÊME période
mom = sd_mom.loc[valides]
ic_m = se.cross_sectional_ic(mom, cd2, 10)
print(f"IC transversal momentum (même période) : {ic_m['ic_moyen']:+.4f}\n")

print(f"{'Stratégie':28s} {'Perf':>9s} {'Sharpe':>8s} {'Drawdown':>10s} {'Rotation':>9s} {'Percentile':>11s}")
print('-'*80)
for nom, df in (('PEAD', pead), ('Momentum 12-1', mom)):
    for n in (5, 12):
        bt = se.backtest_ranking(df, cd2, n, 10)
        if not bt: print(f'  {nom} top {n}: backtest impossible'); continue
        null = se.permutation_null(df, cd2, n, 10, n_trials=120)
        p = se.percentile_of(bt['perf_totale']-bt['perf_benchmark'], null['distribution']) if null else float('nan')
        print(f"{nom+' top '+str(n):28s} {bt['perf_totale']:+8.0f}% {bt['sharpe']:8.3f} "
              f"{bt['drawdown']:9.1f}% {bt['rotation_moyenne']:8.0f}% {p:10.0f}e")
bt_ref = se.backtest_ranking(mom, cd2, 12, 10)
print(f"{'Panier (49, équipondéré)':28s} {bt_ref['perf_benchmark']:+8.0f}% {bt_ref['sharpe_benchmark']:8.3f} "
      f"{bt_ref['drawdown_benchmark']:9.1f}% {0:8.0f}% {'—':>11s}")
