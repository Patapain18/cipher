"""
Évaluation des algorithmes de signal — le juge de paix.

CE QUE CE FICHIER RÉPOND
    « Est-ce qu'un score élevé annonce vraiment une hausse ? »

    Jusqu'ici, rien dans le projet ne permettait de le savoir. Le score
    sur 5 était calculé, affiché, notifié — mais jamais confronté à ce
    qui s'est réellement passé les jours suivants. Un algorithme qu'on
    ne mesure pas ne s'améliore pas : on ne peut que le remplacer par un
    autre auquel on croit un peu plus.

COMMENT ON MESURE
    Pour chaque séance passée, on calcule le score que l'algorithme
    AURAIT donné ce jour-là (avec les seules données disponibles à cette
    date), puis on regarde le rendement effectif des N séances
    suivantes. Trois lectures complémentaires :

    1. RENDEMENT PAR NIVEAU DE SCORE
       Si l'algorithme vaut quelque chose, la colonne doit monter : un
       5/5 doit rapporter davantage qu'un 1/5. C'est la lecture la plus
       parlante, et la plus impitoyable.

    2. INFORMATION COEFFICIENT (IC)
       La corrélation de rang (Spearman) entre le score et le rendement
       qui a suivi. C'est la mesure standard du métier pour juger un
       signal. Ordres de grandeur, à garder en tête :
           IC ≈ 0      → le score n'apprend rien, autant tirer à pile ou face
           IC ≈ 0,02-0,05 → faible mais réel ; c'est le régime dans lequel
                            travaillent la plupart des fonds quantitatifs
           IC > 0,10   → très fort ; sur des données publiques et un
                         algorithme simple, un tel chiffre doit d'abord
                         faire soupçonner une erreur de méthode
       On mesure aussi la corrélation de Spearman plutôt que de Pearson
       parce que seul l'ORDRE nous intéresse : on veut savoir si les
       meilleurs scores correspondent aux meilleurs rendements, pas si
       la relation est une droite.

    3. COMPARAISON À LA RÉFÉRENCE
       Un algorithme ne se juge jamais dans l'absolu, mais contre l'
       alternative gratuite : acheter et ne rien faire. Sur les valeurs
       technologiques de la liste, cette référence est redoutable.

CE QUE ÇA NE DIT PAS
    Le passé n'est pas une promesse. Ces mesures disent si un signal
    aurait été informatif sur la période testée — pas s'il le restera.
    Et les séances se ressemblent d'un jour à l'autre : les
    observations ne sont pas indépendantes, donc l'IC est plus fragile
    que son nombre de décimales ne le suggère.

USAGE
    python3 stock_eval.py                       # les 9 valeurs, 3 ans, horizon 5 j
    python3 stock_eval.py --horizon 10
    python3 stock_eval.py --period 5y --tickers NVDA,AMD
"""

import argparse
import json
import os
import sys
import warnings

import numpy as np
import pandas as pd
import yfinance as yf
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

import stock_engine as engine

warnings.filterwarnings("ignore")
console = Console()

CACHE_DIR = os.path.join("data", "market")

# L'univers vit dans stock_engine : le bot, le serveur et cet
# évaluateur doivent tous parler de la même liste.
DEFAULT_TICKERS = ",".join(engine.UNIVERSE_TICKERS)
LEGACY_TICKERS = ",".join(engine.LEGACY_TICKERS)


# ══════════════════════════════════════════════════════════════════
#  DONNÉES
# ══════════════════════════════════════════════════════════════════

def load_prices(ticker, period="3y", refresh=False):
    """
    Historique quotidien, mis en cache sur le disque.

    Une évaluation se relance des dizaines de fois pendant qu'on ajuste
    l'algorithme. Retélécharger les mêmes trois ans à chaque essai est
    lent et finit par se faire limiter par Yahoo.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{ticker}_{period}.csv")

    if os.path.exists(path) and not refresh:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if len(df) > 60:
            return df

    df = yf.download(ticker, period=period, interval="1d",
                     progress=False, auto_adjust=True)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.to_csv(path)
    return df


def forward_returns(df, horizon):
    """
    Rendement des `horizon` séances suivant chaque date, en pourcentage.

    C'est la seule série du fichier qui regarde vers l'avant — c'est ce
    qu'on cherche à prédire, jamais ce qu'on donne à l'algorithme.
    """
    close = engine.normalize(df)["close"]
    return (close.shift(-horizon) / close - 1.0) * 100.0


# ══════════════════════════════════════════════════════════════════
#  MESURES
# ══════════════════════════════════════════════════════════════════

def spearman_ic(scores, returns):
    """
    Corrélation de rang entre score et rendement futur.

    Écrite à la main (rangs moyens puis corrélation linéaire) pour ne
    pas ajouter scipy aux dépendances du projet pour une seule formule.
    """
    s = pd.Series(scores, dtype=float)
    r = pd.Series(returns, dtype=float)
    mask = s.notna() & r.notna()
    if mask.sum() < 30:
        return None
    rs, rr = s[mask].rank(), r[mask].rank()
    if rs.std() == 0 or rr.std() == 0:
        return None      # un score constant ne classe rien
    return float(np.corrcoef(rs, rr)[0, 1])


def evaluate_algo(rows, fwd, score_key="buy_score"):
    """
    Confronte une liste de scores datés aux rendements qui ont suivi.

    Renvoie le détail par niveau de score, l'IC, et la référence
    « acheter et ne rien faire » calculée sur les mêmes dates — pour que
    la comparaison porte sur la même période et les mêmes séances.
    """
    paired = [(r[score_key], fwd.get(r["date"])) for r in rows]
    paired = [(s, f) for s, f in paired if f is not None and np.isfinite(f)]
    if not paired:
        return None

    scores = [s for s, _ in paired]
    rets = [f for _, f in paired]

    buckets = {}
    for score, ret in paired:
        buckets.setdefault(int(score), []).append(ret)

    per_score = {
        score: {
            "n": len(vals),
            "moyenne": float(np.mean(vals)),
            "mediane": float(np.median(vals)),
            "taux_hausse": float(np.mean([v > 0 for v in vals]) * 100),
        }
        for score, vals in sorted(buckets.items())
    }

    return {
        "n": len(paired),
        "ic": spearman_ic(scores, rets),
        "per_score": per_score,
        "reference": float(np.mean(rets)),          # toutes séances confondues
        "taux_hausse_reference": float(np.mean([r > 0 for r in rets]) * 100),
    }


# ══════════════════════════════════════════════════════════════════
#  CLASSEMENT RELATIF
# ══════════════════════════════════════════════════════════════════
#
#  L'évaluation au-dessus juge chaque valeur contre SON PROPRE passé :
#  « NVDA est-elle mieux orientée que d'habitude ? ». C'est une lecture
#  dans le temps, et elle a un angle mort — quand tout le marché monte,
#  tout paraît bien orienté, et le signal se contente de suivre la
#  marée.
#
#  Le classement relatif pose une autre question, plus utile quand on a
#  un capital limité : « parmi ces neuf valeurs, lesquelles sont les
#  mieux placées AUJOURD'HUI ? ». On les compare entre elles à chaque
#  séance, on détient les meilleures, et le reste du marché sort de
#  l'équation. C'est l'approche dominante des fonds quantitatifs, pour
#  une raison simple : elle neutralise ce que les valeurs ont en commun
#  (la tendance générale) pour ne garder que ce qui les distingue.
#
#  Le point de comparaison change en conséquence : plus une valeur
#  isolée, mais le panier équipondéré des neuf. Battre « acheter les
#  neuf » est le seul résultat qui justifierait de choisir.

def build_matrices(tickers, period, refresh, score_key="strength"):
    """
    Construit deux tableaux alignés dates × valeurs : les scores et les
    cours de clôture.

    L'alignement est la partie délicate : les valeurs n'ont pas
    exactement les mêmes séances (introductions, suspensions). On ne
    garde que les dates où TOUTES sont cotées, sinon un classement
    porterait un jour sur neuf valeurs et le lendemain sur sept, et les
    rangs ne seraient plus comparables.
    """
    scores, closes = {}, {}
    for ticker in tickers:
        df = load_prices(ticker, period, refresh)
        if df is None or len(df) < 300:
            console.print(f"[red]{ticker} — historique insuffisant, écarté[/red]")
            continue
        rows = engine.analyze_series(df)
        if not rows:
            continue
        scores[ticker] = pd.Series({r["date"]: r[score_key] for r in rows})
        closes[ticker] = engine.normalize(df)["close"]

    if len(scores) < 3:
        return None, None

    score_df = pd.DataFrame(scores).dropna()
    close_df = pd.DataFrame(closes).reindex(score_df.index)
    return score_df, close_df.dropna()


def cross_sectional_ic(score_df, close_df, horizon):
    """
    IC transversal : pour CHAQUE séance, la corrélation de rang entre
    les scores du jour et les rendements qui ont suivi, puis la moyenne
    de ces corrélations quotidiennes.

    C'est la bonne façon de mesurer un signal de classement, et elle
    diffère de l'IC calculé plus haut. Là-bas on mélangeait toutes les
    dates et toutes les valeurs dans un seul nuage de points, ce qui
    laissait la tendance générale du marché dominer la corrélation.
    Ici, chaque journée est jugée séparément : seul compte le fait
    d'avoir correctement ORDONNÉ les valeurs ce jour-là.

    On renvoie aussi l'écart-type des IC quotidiens, dont se déduit le
    « ratio d'information » du signal — sa régularité compte autant que
    sa moyenne.
    """
    fwd = close_df.shift(-horizon) / close_df - 1.0
    daily = []
    for date in score_df.index:
        if date not in fwd.index:
            continue
        s, f = score_df.loc[date], fwd.loc[date]
        pair = pd.concat([s, f], axis=1).dropna()
        if len(pair) < 4 or pair.iloc[:, 0].std() == 0:
            continue
        rs, rf = pair.iloc[:, 0].rank(), pair.iloc[:, 1].rank()
        if rs.std() == 0 or rf.std() == 0:
            continue
        daily.append(float(np.corrcoef(rs, rf)[0, 1]))

    if len(daily) < 30:
        return None
    arr = np.array(daily)
    return {
        "ic_moyen": float(arr.mean()),
        "ic_ecart_type": float(arr.std()),
        "part_positive": float((arr > 0).mean() * 100),
        "n_seances": len(arr),
    }


def backtest_ranking(score_df, close_df, top_n, horizon, fee=0.0005, quotidien=True):
    """
    Portefeuille équipondéré des `top_n` meilleures valeurs, rééquilibré
    tous les `horizon` jours, contre le panier des neuf.

    DÉCALAGE D'UNE SÉANCE
        Le score d'une journée se calcule sur sa clôture ; on ne peut
        donc pas acheter à cette même clôture. Le portefeuille entre à
        la clôture SUIVANTE. Sans ce décalage, le backtest achèterait à
        un cours déjà connu au moment de la décision et afficherait des
        performances impossibles à reproduire.

    Les frais sont prélevés sur la part du portefeuille effectivement
    remplacée à chaque rééquilibrage — pas sur la totalité, puisqu'une
    valeur qui reste sélectionnée n'est pas revendue.
    """
    dates = list(score_df.index)
    index_de = {d: k for k, d in enumerate(dates)}   # date -> position
    equity, bench = [1.0], [1.0]
    equity_j, bench_j = [1.0], [1.0]                 # marquage quotidien
    stamps = [dates[0]]
    held = set()
    turnovers = []

    for i in range(0, len(dates) - horizon - 1, horizon):
        decision = dates[i]          # séance où le signal est lu
        entry = dates[i + 1]         # séance où l'on entre réellement
        exit_ = dates[min(i + 1 + horizon, len(dates) - 1)]

        row = score_df.loc[decision].dropna()
        if len(row) < top_n:
            continue
        picks = list(row.sort_values(ascending=False).head(top_n).index)

        try:
            p_in = close_df.loc[entry, picks]
            p_out = close_df.loc[exit_, picks]
            all_in = close_df.loc[entry]
            all_out = close_df.loc[exit_]
        except KeyError:
            continue
        if p_in.isna().any() or p_out.isna().any():
            continue

        gross = float((p_out / p_in).mean())

        # Rotation : proportion du portefeuille réellement changée.
        new = set(picks)
        turnover = len(new - held) / top_n if held else 1.0
        turnovers.append(turnover)
        held = new

        # --- Courbe de rééquilibrage (une valeur par période) ---
        debut_strat = equity[-1] * (1 - fee * turnover * 2)   # frais payés à l'entrée
        debut_bench = bench[-1]
        equity.append(debut_strat * gross)
        bench.append(debut_bench * float((all_out / all_in).mean()))
        stamps.append(exit_)

        # --- Courbe QUOTIDIENNE ---
        # Le portefeuille est marqué au marché à chaque séance de la
        # période de détention, et pas seulement à ses deux extrémités.
        #
        # Sans cela, le drawdown est mesuré sur une courbe qui n'a qu'un
        # point tous les `horizon` jours : tout creux qui se forme et se
        # referme entre deux rééquilibrages est invisible. L'audit a
        # chiffré l'écart — -19,7 % annoncés contre -29,4 % réels. Un
        # investisseur ne vit pas les creux aux dates de rééquilibrage,
        # il les vit tous les jours.
        if not quotidien:
            continue
        i_in, i_out = index_de[entry], index_de[exit_]
        for t in dates[i_in:i_out + 1]:
            try:
                pt = close_df.loc[t, picks]
                at = close_df.loc[t]
            except KeyError:
                continue
            if pt.isna().any():
                continue
            equity_j.append(debut_strat * float((pt / p_in).mean()))
            bench_j.append(debut_bench * float((at / all_in).mean()))

    if len(equity) < 3:
        return None

    def annualise(curve, days):
        years = days / 252.0
        return (curve[-1] ** (1 / years) - 1) * 100 if years > 0 else 0.0

    def max_drawdown(curve):
        peak, worst = curve[0], 0.0
        for v in curve:
            peak = max(peak, v)
            worst = min(worst, v / peak - 1)
        return worst * 100

    span = len(dates)

    def sharpe_of(curve, periodes_par_an):
        """
        Rendement rapporté à sa propre irrégularité.

        TAUX SANS RISQUE NON DÉDUIT. C'est donc un Sharpe BRUT : sur une
        période où le monétaire rapportait 2 à 5 % par an, il surestime
        les deux termes de la comparaison. Comme stratégie et référence
        subissent le même traitement, leur ÉCART reste interprétable —
        mais le niveau, lui, ne doit pas être lu comme un Sharpe publié
        par un fonds.
        """
        r = np.diff(np.log(curve))
        if len(r) < 3 or r.std() == 0:
            return None
        return float(r.mean()) / float(r.std()) * np.sqrt(periodes_par_an)

    def ecart_sharpe_ic95(curve_a, curve_b, periodes_par_an, tirages=400, seed=11):
        """
        Intervalle de confiance de l'ÉCART de Sharpe, par bootstrap
        apparié en blocs.

        POURQUOI C'EST INDISPENSABLE
            Le projet a longtemps annoncé « Sharpe 1,23 contre 1,22 »
            comme une victoire. L'audit a montré que cet écart est
            environ vingt-cinq fois plus petit que son propre bruit
            d'échantillonnage : P(écart > 0) ≈ 52 %, autrement dit un
            pile ou face. Un ratio sans intervalle de confiance invite
            à lire une différence là où il n'y a que du hasard.

            Le tirage est APPARIÉ (les deux courbes sont rééchantillonnées
            aux mêmes indices) parce que stratégie et référence subissent
            les mêmes journées de marché : les comparer sur des tirages
            indépendants gonflerait artificiellement l'incertitude.

            Le tirage se fait par BLOCS contigus, car les rendements se
            suivent et se ressemblent ; un tirage point par point
            supposerait une indépendance qui n'existe pas et donnerait un
            intervalle trop étroit.
        """
        ra = np.diff(np.log(curve_a))
        rb = np.diff(np.log(curve_b))
        n = min(len(ra), len(rb))
        if n < 20:
            return None
        ra, rb = ra[:n], rb[:n]
        taille_bloc = max(1, int(np.sqrt(n)))
        n_blocs = int(np.ceil(n / taille_bloc))
        rng = np.random.default_rng(seed)

        ecarts = []
        for _ in range(tirages):
            debuts = rng.integers(0, n - taille_bloc + 1, size=n_blocs)
            idx = np.concatenate([np.arange(d, d + taille_bloc) for d in debuts])[:n]
            a, b = ra[idx], rb[idx]
            if a.std() == 0 or b.std() == 0:
                continue
            sa = a.mean() / a.std() * np.sqrt(periodes_par_an)
            sb = b.mean() / b.std() * np.sqrt(periodes_par_an)
            ecarts.append(sa - sb)

        if len(ecarts) < 50:
            return None
        arr = np.array(ecarts)
        return {
            "bas": float(np.percentile(arr, 2.5)),
            "haut": float(np.percentile(arr, 97.5)),
            "p_superieur": float((arr > 0).mean() * 100),
        }

    # Le Sharpe se lit sur la courbe QUOTIDIENNE : une courbe échantillonnée
    # tous les 10 jours lisse la volatilité et flatte le ratio.
    sharpe = sharpe_of(equity_j, 252)
    sharpe_bench = sharpe_of(bench_j, 252)
    ic_sharpe = ecart_sharpe_ic95(equity_j, bench_j, 252)

    return {
        "top_n": top_n,
        "rebalancements": len(equity) - 1,
        "perf_totale": (equity[-1] - 1) * 100,
        "perf_benchmark": (bench[-1] - 1) * 100,
        "annualise": annualise(equity, span),
        "annualise_benchmark": annualise(bench, span),
        # Le drawdown se lit sur la courbe quotidienne, seule à voir les
        # creux qui se referment entre deux rééquilibrages.
        "drawdown": max_drawdown(equity_j),
        "drawdown_benchmark": max_drawdown(bench_j),
        "drawdown_reequilibrage": max_drawdown(equity),   # l'ancien chiffre, minorant
        "sharpe": sharpe,
        "sharpe_benchmark": sharpe_bench,
        "sharpe_ecart_ic95": ic_sharpe,
        "sharpe_taux_sans_risque_deduit": False,
        "rotation_moyenne": float(np.mean(turnovers)) * 100 if turnovers else 0.0,
        "courbe": equity,
        "courbe_benchmark": bench,
        "dates": [str(s)[:10] for s in stamps],
    }


def random_baseline(close_df, dates, top_n, horizon, n_trials=300, fee=0.0005, seed=7):
    """
    La même stratégie, mais en tirant les valeurs AU HASARD.

    POURQUOI CE TEST EST INDISPENSABLE
        Sur un panier où une valeur a fait ×10, n'importe quelle
        sélection concentrée a de bonnes chances de battre la moyenne :
        il suffit d'avoir tiré la bonne. Une performance flatteuse ne
        prouve donc rien à elle seule — elle peut n'être que le fruit de
        la concentration, pas du classement.

        En rejouant des centaines de portefeuilles tirés au sort, on
        obtient la distribution de ce que le hasard produit dans ces
        conditions. Le signal n'a de valeur que s'il se situe nettement
        dans le haut de cette distribution. S'il tombe au milieu, il
        n'apporte rien qu'un tirage à la courte paille n'apporterait.

        C'est le même raisonnement qu'un test de significativité : on
        compare le résultat obtenu à ce qu'on aurait obtenu sans
        compétence particulière.
    """
    rng = np.random.default_rng(seed)
    n_assets = close_df.shape[1]

    # On sort de pandas avant de boucler : 300 tirages × des centaines de
    # rééquilibrages, c'est plus de cent mille accès. En `.loc` cela prend
    # des minutes, en tableau numpy quelques secondes — et un test qu'on
    # renonce à lancer parce qu'il est trop lent ne sert à rien.
    prices = close_df.reindex(dates).to_numpy(dtype=float)

    idx = [(i + 1, min(i + 1 + horizon, len(dates) - 1))
           for i in range(0, len(dates) - horizon - 1, horizon)]

    perfs = []
    for _ in range(n_trials):
        equity = 1.0
        held = None
        for i_in, i_out in idx:
            picks = rng.choice(n_assets, size=top_n, replace=False)
            p_in, p_out = prices[i_in, picks], prices[i_out, picks]
            if np.isnan(p_in).any() or np.isnan(p_out).any():
                continue
            new = set(picks.tolist())
            turnover = len(new - held) / top_n if held is not None else 1.0
            held = new
            equity *= float((p_out / p_in).mean()) * (1 - fee * turnover * 2)
        perfs.append((equity - 1) * 100)

    arr = np.array(perfs)
    return {
        "median": float(np.median(arr)),
        "p25": float(np.percentile(arr, 25)),
        "p75": float(np.percentile(arr, 75)),
        "p95": float(np.percentile(arr, 95)),
        "n": n_trials,
        "distribution": arr,
    }


def benchmark_etf(ticker, dates, period, refresh=False):
    """
    Achat-conservation d'un ETF réellement investissable, sur EXACTEMENT
    les mêmes dates que la stratégie.

    POURQUOI CETTE RÉFÉRENCE EST LA SEULE HONNÊTE
        Le panier équipondéré des 49 valeurs partage un défaut avec la
        stratégie qu'il sert à juger : sa composition a été écrite en
        2026 et rejouée depuis 2016. Les deux termes de la comparaison
        sont contaminés par le même biais de survie, qui s'annule donc
        dans l'écart — et devient invisible.

        Un ETF, lui, existait déjà à la date de départ, il a subi les
        faillites et les sorties d'indice au fil de l'eau, et surtout on
        peut l'acheter chez son courtier. Sa performance passée n'a pas
        été choisie après coup.

        RSP (S&P 500 équipondéré) est le comparable direct d'un
        portefeuille équipondéré ; SPY (pondéré par capitalisation) est
        la référence que tout le monde connaît. Les deux cours sont
        ajustés des dividendes, donc comparables en rendement total, et
        les frais de gestion sont déjà déduits du cours.
    """
    df = load_prices(ticker, period, refresh)
    if df is None or df.empty:
        return None
    close = engine.normalize(df)["close"].reindex(dates).ffill().dropna()
    if len(close) < 100:
        return None
    courbe = (close / close.iloc[0]).tolist()
    return {"ticker": ticker, "courbe": courbe, "dates": list(close.index)}


def permutation_null(score_df, close_df, top_n, horizon, n_trials=200, fee=0.0005, seed=7):
    """
    Témoin par PERMUTATION DES ÉTIQUETTES, et non par tirage au sort.

    POURQUOI L'ANCIEN TÉMOIN ÉTAIT FAUSSÉ
        Il retirait un portefeuille entièrement neuf à chaque
        rééquilibrage : sa rotation valait 100 % là où la stratégie
        tourne à 15 %. Il payait donc cinq à six fois plus de frais que
        ce qu'il servait à juger. Une partie du « 100ᵉ percentile »
        n'était pas de la compétence, seulement une note de courtage
        que l'adversaire réglait à notre place.

    CE QUE FAIT CELUI-CI
        Il redistribue au hasard les colonnes du tableau de scores : la
        série de scores qui appartenait à NVDA se retrouve attribuée à
        KO, et ainsi de suite. Toute la structure temporelle du signal
        est conservée — sa persistance, donc sa rotation, donc ses
        frais — et l'on ne casse que le lien entre le signal et la
        valeur qu'il désigne. C'est exactement l'hypothèse à tester :
        « ce classement désigne-t-il les bonnes valeurs, ou n'importe
        lesquelles ? »

    On percentile l'EXCÈS sur le panier équipondéré, pas la performance
    brute : le témoin est alors centré sur zéro par construction, et le
    50ᵉ percentile redevient interprétable comme « aucune compétence ».
    """
    rng = np.random.default_rng(seed)
    colonnes = list(score_df.columns)
    excess, rotations = [], []

    for _ in range(n_trials):
        melange = score_df.copy()
        melange.columns = [colonnes[i] for i in rng.permutation(len(colonnes))]
        bt = backtest_ranking(melange[colonnes], close_df, top_n, horizon,
                              fee, quotidien=False)
        if bt:
            excess.append(bt["perf_totale"] - bt["perf_benchmark"])
            rotations.append(bt["rotation_moyenne"])

    if len(excess) < 30:
        return None
    arr = np.array(excess)
    return {
        "median": float(np.median(arr)),
        "p75": float(np.percentile(arr, 75)),
        "p95": float(np.percentile(arr, 95)),
        "rotation_moyenne": float(np.mean(rotations)),
        "n": len(excess),
        "distribution": arr,
    }


def percentile_of(value, distribution):
    """Où se situe notre stratégie dans la distribution du hasard."""
    return float((distribution < value).mean() * 100)


def signal_edge(result, threshold):
    """
    L'écart entre « ce que rapportent les séances au-dessus du seuil »
    et « ce que rapporte une séance quelconque ».

    C'est la seule question qui compte en pratique : est-ce que suivre
    le signal fait mieux que ne pas le suivre ? Un algorithme peut avoir
    un bel IC et n'apporter aucun gain exploitable à son seuil de
    déclenchement.
    """
    if not result:
        return None
    hits = {s: d for s, d in result["per_score"].items() if s >= threshold}
    if not hits:
        return {"n": 0}

    total = sum(d["n"] for d in hits.values())
    weighted = sum(d["moyenne"] * d["n"] for d in hits.values()) / total
    hausse = sum(d["taux_hausse"] * d["n"] for d in hits.values()) / total
    return {
        "n": total,
        "part_du_temps": total / result["n"] * 100,
        "rendement": weighted,
        "taux_hausse": hausse,
        "ecart": weighted - result["reference"],
    }


# ══════════════════════════════════════════════════════════════════
#  RAPPORT
# ══════════════════════════════════════════════════════════════════

def render_per_score(title, result, threshold):
    table = Table(title=title, title_style="bold", header_style="dim")
    table.add_column("Score", justify="center")
    table.add_column("Séances", justify="right")
    table.add_column("Rendement moyen", justify="right")
    table.add_column("Médiane", justify="right")
    table.add_column("% en hausse", justify="right")

    for score, d in result["per_score"].items():
        colour = "green" if d["moyenne"] > result["reference"] else "red"
        marker = " ←" if score >= threshold else ""
        table.add_row(
            f"{score}/5{marker}",
            f"{d['n']}",
            f"[{colour}]{d['moyenne']:+.2f} %[/{colour}]",
            f"{d['mediane']:+.2f} %",
            f"{d['taux_hausse']:.0f} %",
        )
    table.add_row(
        "[dim]toutes[/dim]", f"[dim]{result['n']}[/dim]",
        f"[dim]{result['reference']:+.2f} %[/dim]", "[dim]—[/dim]",
        f"[dim]{result['taux_hausse_reference']:.0f} %[/dim]",
    )
    console.print(table)


def interpret_ic(ic):
    if ic is None:
        return "[dim]non calculable[/dim]"
    # Le seuil porte sur la valeur SIGNÉE, pas sur sa valeur absolue.
    # Avec abs(), un IC de -0,06 — un signal qui pointe franchement à
    # l'envers — était qualifié de « correct pour un signal technique ».
    # Le défaut n'a pas mordu ici par chance (les IC mesurés sont proches
    # de zéro), mais il aurait validé n'importe quel signal inversé.
    if ic < -0.02:
        return "[red]corrélé À L'ENVERS — le score pointe dans le mauvais sens[/red]"
    if ic < 0.02:
        return "[red]pas de pouvoir prédictif décelable[/red]"
    if ic < 0.05:
        return "[yellow]faible, mais dans la zone exploitable[/yellow]"
    if ic < 0.10:
        return "[green]correct pour un signal technique[/green]"
    return "[bold green]très élevé — vérifier qu'il n'y a pas d'erreur de méthode[/bold green]"


def run_ranking(args, tickers):
    """Mode classement relatif : IC transversal + backtest de portefeuille."""
    console.print(Panel(
        f"[bold]Classement relatif[/bold]\n"
        f"{len(tickers)} valeurs comparées entre elles à chaque séance · "
        f"{args.period} d'historique · rééquilibrage tous les {args.horizon} j\n"
        f"[dim]Référence : le panier équipondéré des mêmes valeurs. Entrée "
        f"décalée d'une séance après le signal.[/dim]",
        border_style="cyan"))

    # La taille de la sélection s'adapte à l'univers : prendre 2 valeurs
    # sur 48 serait une loterie, et prendre 4 sur 9 n'est presque plus
    # une sélection. On vise grossièrement le quart supérieur.
    sizes = (2, 3, 4) if len(tickers) <= 12 else (5, 8, 12)

    # Le score calibré a été écarté : mesuré deux fois sous le hasard,
    # il n'a pas besoin d'un troisième tableau. Restent la note brute du
    # moteur, comme point de comparaison, et les trois profondeurs de
    # momentum à départager.
    variants = [("raw_score", "note brute du moteur"),
                ("mom_12_1", "momentum 12-1"),
                ("mom_6_1", "momentum 6-1"),
                ("mom_3_1", "momentum 3-1")]

    matrices = {}
    for key, _ in variants:
        score_df, close_df = build_matrices(tickers, args.period, args.refresh, key)
        if score_df is None:
            console.print("[red]Pas assez de valeurs exploitables.[/red]")
            return 1
        matrices[key] = (score_df, close_df)

    # --- Alignement des périodes ---
    # Chaque critère a besoin d'un historique différent avant de produire
    # sa première valeur : 50 séances pour les moyennes du moteur, 273
    # pour le momentum 12-1. Évaluées chacune sur sa propre plage, les
    # variantes ne seraient pas comparables — et la référence
    # « équipondéré » changerait d'un tableau à l'autre, ce qui suffit à
    # fausser toute conclusion.
    #
    # On restreint donc tout le monde aux dates communes.
    common = None
    for score_df, _ in matrices.values():
        common = score_df.index if common is None else common.intersection(score_df.index)

    if common is None or len(common) < 120:
        console.print("[red]Trop peu de dates communes pour comparer.[/red]")
        return 1

    console.print(f"[dim]Période commune aux trois critères : {len(common)} séances, "
                  f"du {str(common[0])[:10]} au {str(common[-1])[:10]}[/dim]")

    results = {}
    for key, label in variants:
        score_df, close_df = matrices[key]
        score_df = score_df.loc[common]
        close_df = close_df.reindex(common)
        matrices[key] = (score_df, close_df)
        results[key] = {
            "label": label,
            "ic": cross_sectional_ic(score_df, close_df, args.horizon),
            "backtests": {n: backtest_ranking(score_df, close_df, n, args.horizon)
                          for n in sizes},
            "n_dates": len(score_df),
        }

    # ---- IC transversal ----
    ic_table = Table(title="Information Coefficient transversal",
                     title_style="bold", header_style="dim")
    ic_table.add_column("Variante du score")
    ic_table.add_column("IC moyen", justify="right")
    ic_table.add_column("Écart-type", justify="right")
    ic_table.add_column("Séances positives", justify="right")

    for key, res in results.items():
        ic = res["ic"]
        if not ic:
            ic_table.add_row(res["label"], "—", "—", "—")
            continue
        colour = "green" if ic["ic_moyen"] > 0.02 else "yellow" if ic["ic_moyen"] > 0 else "red"
        ic_table.add_row(
            res["label"],
            f"[{colour}]{ic['ic_moyen']:+.4f}[/{colour}]",
            f"{ic['ic_ecart_type']:.3f}",
            f"{ic['part_positive']:.0f} %",
        )
    console.print()
    console.print(ic_table)

    # ---- Backtests ----
    for key, res in results.items():
        table = Table(title=f"Portefeuille — {res['label']}",
                      title_style="bold", header_style="dim")
        table.add_column("Sélection")
        table.add_column("Perf. totale", justify="right")
        table.add_column("Annualisé", justify="right")
        table.add_column("Drawdown max", justify="right")
        table.add_column("Sharpe", justify="right")
        table.add_column("Rotation", justify="right")

        bench_done = False
        for n, bt in res["backtests"].items():
            if not bt:
                continue
            better = bt["perf_totale"] > bt["perf_benchmark"]
            colour = "green" if better else "red"
            table.add_row(
                f"top {n} / {len(tickers)}",
                f"[{colour}]{bt['perf_totale']:+.1f} %[/{colour}]",
                f"{bt['annualise']:+.1f} %",
                f"{bt['drawdown']:.1f} %",
                f"{bt['sharpe']:.2f}" if bt["sharpe"] is not None else "—",
                f"{bt['rotation_moyenne']:.0f} %",
            )
            if not bench_done:
                table.add_row(
                    f"[dim]les {len(tickers)}, équipondéré[/dim]",
                    f"[dim]{bt['perf_benchmark']:+.1f} %[/dim]",
                    f"[dim]{bt['annualise_benchmark']:+.1f} %[/dim]",
                    f"[dim]{bt['drawdown_benchmark']:.1f} %[/dim]",
                    f"[dim]{bt['sharpe_benchmark']:.2f}[/dim]"
                    if bt["sharpe_benchmark"] is not None else "[dim]—[/dim]",
                    "[dim]0 %[/dim]",
                )
                bench_done = True
        console.print()
        console.print(table)

    # ---- Le test qui tranche : et si on tirait au hasard ? ----
    # Sur les mêmes dates que les stratégies, sinon la comparaison n'a
    # aucun sens.
    score_ref, close_df = matrices["mom_12_1"]
    dates = list(common)

    console.print()
    rnd_table = Table(title="Confrontation au null par permutation (rotation appariée)",
                      title_style="bold", header_style="dim")
    rnd_table.add_column("Sélection")
    rnd_table.add_column("Signal", justify="right")
    rnd_table.add_column("Hasard médian", justify="right")
    rnd_table.add_column("Null 75ᵉ / 95ᵉ", justify="right")
    rnd_table.add_column("Percentile", justify="right")
    rnd_table.add_column("Verdict", justify="center")

    short = {"raw_score": "moteur", "mom_12_1": "mom 12-1",
             "mom_6_1": "mom 6-1", "mom_3_1": "mom 3-1"}
    for n in sizes:
        # Le témoin est un null par PERMUTATION, à rotation appariée, et
        # l'on percentile l'EXCÈS sur le panier — pas la performance
        # brute. L'ancien tirage au sort renouvelait tout le portefeuille
        # à chaque rééquilibrage et payait cinq à six fois plus de frais
        # que la stratégie qu'il jugeait : une partie du percentile était
        # une note de courtage, pas de la compétence.
        base = permutation_null(score_ref, close_df, n, args.horizon)
        if not base:
            continue
        for key, res in results.items():
            bt = res["backtests"].get(n)
            if not bt:
                continue
            p = percentile_of(bt["perf_totale"] - bt["perf_benchmark"],
                              base["distribution"])
            res.setdefault("percentiles", {})[n] = p
            verdict = ("[green]au-dessus[/green]" if p >= 90
                       else "[yellow]indistinct[/yellow]" if p >= 60
                       else "[red]sous le hasard[/red]")
            rnd_table.add_row(
                f"top {n} · {short.get(key, key)}",
                f"{bt['perf_totale'] - bt['perf_benchmark']:+.0f} pts",
                f"{base['median']:+.0f} pts",
                f"{base['p75']:+.0f} % / {base['p95']:+.0f} %",
                f"{p:.0f}ᵉ",
                verdict,
            )
    console.print(rnd_table)

    # ---- Face à ce qu'on peut vraiment acheter ----
    etfs = {}
    etf_table = Table(title="Face aux ETF réellement investissables",
                      title_style="bold", header_style="dim")
    etf_table.add_column("Référence")
    etf_table.add_column("Perf. totale", justify="right")
    etf_table.add_column("Annualisé", justify="right")
    etf_table.add_column("Sharpe", justify="right")
    etf_table.add_column("Drawdown max", justify="right")

    meilleur = results.get("mom_12_1", {}).get("backtests", {})
    ref_bt = meilleur.get(sizes[-1]) or next(iter(meilleur.values()), None)
    if ref_bt:
        etf_table.add_row(
            f"[bold]Momentum 12-1 top {sizes[-1]}[/bold]",
            f"{ref_bt['perf_totale']:+.0f} %", f"{ref_bt['annualise']:+.1f} %",
            f"{ref_bt['sharpe']:.3f}" if ref_bt["sharpe"] else "—",
            f"{ref_bt['drawdown']:.1f} %")
        etf_table.add_row(
            f"[dim]Panier maison ({len(tickers)}, équipondéré)[/dim]",
            f"[dim]{ref_bt['perf_benchmark']:+.0f} %[/dim]",
            f"[dim]{ref_bt['annualise_benchmark']:+.1f} %[/dim]",
            f"[dim]{ref_bt['sharpe_benchmark']:.3f}[/dim]" if ref_bt["sharpe_benchmark"] else "—",
            f"[dim]{ref_bt['drawdown_benchmark']:.1f} %[/dim]")

    for etf, libelle in (("RSP", "RSP — S&P 500 équipondéré"), ("SPY", "SPY — S&P 500")):
        b = benchmark_etf(etf, list(common), args.period, args.refresh)
        if not b:
            continue
        c = np.asarray(b["courbe"], dtype=float)
        r = np.diff(np.log(c))
        sh = float(r.mean() / r.std() * np.sqrt(252)) if r.std() else None
        pic, dd = c[0], 0.0
        for v in c:
            pic = max(pic, v)
            dd = min(dd, v / pic - 1)
        annees = len(c) / 252
        etfs[etf] = {"perf": (c[-1] - 1) * 100, "annualise": (c[-1] ** (1 / annees) - 1) * 100,
                     "sharpe": sh, "drawdown": dd * 100}
        etf_table.add_row(f"[cyan]{libelle}[/cyan]",
                          f"{etfs[etf]['perf']:+.0f} %", f"{etfs[etf]['annualise']:+.1f} %",
                          f"{sh:.3f}" if sh else "—", f"{etfs[etf]['drawdown']:.1f} %")
    console.print()
    console.print(etf_table)

    console.print(Panel(
        "[dim]Un backtest reste une reconstitution : il ignore les écarts de "
        "cotation, suppose que tout s'exécute au cours de clôture, et ne teste "
        "qu'une période. Les frais sont comptés (0,05 % par transaction), pas "
        "la fiscalité.\n"
        "Le percentile est la lecture décisive : à 50, le signal fait ce que "
        "ferait un tirage au sort. Il faut viser 90 et plus pour parler d'un "
        "vrai pouvoir de sélection.\n"
        "Les ETF sont la seule référence non choisie après coup : le panier "
        "maison partage avec la stratégie le biais d'avoir été constitué en "
        "2026, biais qui s'annule dans leur écart et devient invisible.[/dim]",
        border_style="dim"))

    if args.save:
        _save_ranking(args, tickers, results, sizes, common, etfs)
    return 0


def _save_ranking(args, tickers, results, sizes, common, etfs=None):
    """
    Écrit data/ranking_report.json, lu par la page d'explications.

    Les chiffres avancés pour justifier un choix doivent venir de la
    dernière mesure, pas d'une valeur recopiée à la main dans une page
    HTML : recopiée, elle cesse d'être vraie au premier réexamen sans
    que personne ne s'en aperçoive.
    """
    from datetime import datetime

    os.makedirs("data", exist_ok=True)
    etfs = etfs or {}
    payload = {
        "genere_le": datetime.now().isoformat(),
        "parametres": {
            "periode": args.period,
            "horizon": args.horizon,
            "valeurs": len(tickers),
            "seances": len(common),
            "du": str(common[0])[:10],
            "au": str(common[-1])[:10],
            "tailles": list(sizes),
        },
        "criteres": {},
        "etfs": etfs,
    }

    for key, res in results.items():
        backtests = {}
        for n, bt in res["backtests"].items():
            if not bt:
                continue
            backtests[str(n)] = {
                "perf": bt["perf_totale"],
                "perf_benchmark": bt["perf_benchmark"],
                "sharpe": bt["sharpe"],
                "sharpe_benchmark": bt["sharpe_benchmark"],
                "drawdown": bt["drawdown"],
                "drawdown_benchmark": bt["drawdown_benchmark"],
                "rotation": bt["rotation_moyenne"],
                "percentile": res.get("percentiles", {}).get(n),
            }
        payload["criteres"][key] = {
            "label": res["label"],
            "ic": res["ic"],
            "backtests": backtests,
        }

    path = os.path.join("data", "ranking_report.json")
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    console.print(f"\n  [green]Rapport de classement écrit dans {path}[/green]")


def main():
    parser = argparse.ArgumentParser(description="Évaluation des algorithmes de signal")
    parser.add_argument("--tickers", default=DEFAULT_TICKERS)
    parser.add_argument("--period", default="3y", help="3y, 5y, 10y, max…")
    parser.add_argument("--horizon", type=int, default=5,
                        help="séances de détention évaluées")
    parser.add_argument("--threshold", type=int, default=4,
                        help="score à partir duquel le bot déclenche")
    parser.add_argument("--refresh", action="store_true",
                        help="retélécharger au lieu d'utiliser le cache")
    parser.add_argument("--save", action="store_true",
                        help="écrire le rapport dans data/eval_report.json "
                             "pour que le dashboard l'affiche")
    parser.add_argument("--ranking", action="store_true",
                        help="évaluer le classement relatif (comparaison des "
                             "valeurs entre elles) au lieu du signal isolé")
    parser.add_argument("--legacy-universe", action="store_true",
                        help="rejouer sur les 9 valeurs d'avant "
                             "l'élargissement, au lieu des 49 actuelles")
    args = parser.parse_args()

    source = LEGACY_TICKERS if args.legacy_universe else args.tickers
    tickers = [t.strip().upper() for t in source.split(",") if t.strip()]

    if args.ranking:
        return run_ranking(args, tickers)

    console.print(Panel(
        f"[bold]Évaluation des signaux[/bold]\n"
        f"{len(tickers)} valeurs · {args.period} d'historique · "
        f"horizon {args.horizon} séances · seuil {args.threshold}/5\n"
        f"[dim]Chaque score est calculé avec les seules données disponibles "
        f"à sa date, puis confronté au rendement des {args.horizon} séances "
        f"suivantes.[/dim]",
        border_style="cyan"))

    all_v1, all_v2 = [], []
    per_ticker = []

    for ticker in tickers:
        df = load_prices(ticker, args.period, args.refresh)
        if df is None or len(df) < 120:
            console.print(f"[red]{ticker} — données insuffisantes, ignoré[/red]")
            continue

        fwd = forward_returns(df, args.horizon).to_dict()
        rows_v1 = engine.analyze_v1_series(df)
        rows_v2 = engine.analyze_series(df, horizon=args.horizon)

        r1 = evaluate_algo(rows_v1, fwd)
        r2 = evaluate_algo(rows_v2, fwd)
        if not (r1 and r2):
            continue

        all_v1.extend([(r["buy_score"], fwd.get(r["date"])) for r in rows_v1])
        all_v2.extend([(r["buy_score"], fwd.get(r["date"])) for r in rows_v2])

        per_ticker.append({
            "ticker": ticker,
            "n": r2["n"],
            "ic_v1": r1["ic"],
            "ic_v2": r2["ic"],
            "ref": r2["reference"],
        })
        console.print(f"[dim]{ticker} : {r2['n']} séances évaluées[/dim]")

    if not per_ticker:
        console.print("[red]Aucune donnée exploitable.[/red]")
        return 1

    # ---- Agrégat toutes valeurs confondues ----
    def regroup(pairs):
        pairs = [(s, f) for s, f in pairs if f is not None and np.isfinite(f)]
        rows = [{"date": i, "buy_score": s} for i, (s, _) in enumerate(pairs)]
        fwd = {i: f for i, (_, f) in enumerate(pairs)}
        return evaluate_algo(rows, fwd)

    agg_v1, agg_v2 = regroup(all_v1), regroup(all_v2)

    console.print()
    render_per_score("v1 — score actuel (stock_bot.py)", agg_v1, args.threshold)
    console.print()
    render_per_score("v2 — moteur adaptatif (stock_engine.py)", agg_v2, args.threshold)

    # ---- Synthèse ----
    console.print()
    summary = Table(title="Synthèse", title_style="bold", header_style="dim")
    summary.add_column("Mesure")
    summary.add_column("v1", justify="right")
    summary.add_column("v2", justify="right")

    summary.add_row("Information Coefficient",
                    f"{agg_v1['ic']:+.4f}" if agg_v1["ic"] is not None else "—",
                    f"{agg_v2['ic']:+.4f}" if agg_v2["ic"] is not None else "—")

    e1, e2 = signal_edge(agg_v1, args.threshold), signal_edge(agg_v2, args.threshold)
    for label, key, fmt in [
        (f"Séances au-dessus du seuil", "part_du_temps", "{:.1f} %"),
        (f"Rendement à {args.horizon} j quand déclenché", "rendement", "{:+.2f} %"),
        ("Écart vs séance quelconque", "ecart", "{:+.2f} pt"),
        ("Taux de hausse quand déclenché", "taux_hausse", "{:.0f} %"),
    ]:
        summary.add_row(
            label,
            fmt.format(e1[key]) if e1 and e1.get("n") and key in e1 else "jamais",
            fmt.format(e2[key]) if e2 and e2.get("n") and key in e2 else "jamais",
        )

    summary.add_row("[dim]Référence (ne rien faire)[/dim]", "",
                    f"[dim]{agg_v2['reference']:+.2f} %[/dim]")
    console.print(summary)

    console.print()
    console.print(f"  Lecture de l'IC — v1 : {interpret_ic(agg_v1['ic'])}")
    console.print(f"  Lecture de l'IC — v2 : {interpret_ic(agg_v2['ic'])}")

    # ---- Détail par valeur ----
    console.print()
    detail = Table(title="Information Coefficient par valeur",
                   title_style="bold", header_style="dim")
    detail.add_column("Valeur")
    detail.add_column("Séances", justify="right")
    detail.add_column("IC v1", justify="right")
    detail.add_column("IC v2", justify="right")
    detail.add_column("", justify="center")

    for p in sorted(per_ticker, key=lambda x: (x["ic_v2"] or -9), reverse=True):
        better = (p["ic_v2"] or 0) > (p["ic_v1"] or 0)
        detail.add_row(
            p["ticker"], str(p["n"]),
            f"{p['ic_v1']:+.4f}" if p["ic_v1"] is not None else "—",
            f"{p['ic_v2']:+.4f}" if p["ic_v2"] is not None else "—",
            "[green]v2[/green]" if better else "[yellow]v1[/yellow]",
        )
    console.print(detail)

    console.print(Panel(
        "[dim]Les séances consécutives se ressemblent : leurs rendements ne "
        "sont pas indépendants, donc ces coefficients sont moins précis que "
        "leur nombre de décimales ne le laisse croire. À lire comme des "
        "ordres de grandeur, sur une période donnée — pas comme une "
        "promesse.[/dim]",
        border_style="dim"))

    if args.save:
        save_report(args, agg_v1, agg_v2, e1, e2, per_ticker)
    return 0


def save_report(args, agg_v1, agg_v2, edge_v1, edge_v2, per_ticker):
    """
    Écrit le rapport pour le dashboard.

    Le dashboard doit pouvoir afficher en permanence la fiabilité
    mesurée du score qu'il présente. Un signal sans son taux de réussite
    connu invite à lui faire une confiance qu'il n'a pas méritée — c'est
    exactement ce que cet outil doit éviter.
    """
    import json
    from datetime import datetime

    os.makedirs("data", exist_ok=True)
    path = os.path.join("data", "eval_report.json")
    etfs = etfs or {}
    payload = {
        "genere_le": datetime.now().isoformat(),
        "parametres": {
            "periode": args.period,
            "horizon": args.horizon,
            "seuil": args.threshold,
            "valeurs": [p["ticker"] for p in per_ticker],
        },
        "v1": {"ic": agg_v1["ic"], "per_score": agg_v1["per_score"],
               "reference": agg_v1["reference"], "edge": edge_v1},
        "v2": {"ic": agg_v2["ic"], "per_score": agg_v2["per_score"],
               "reference": agg_v2["reference"], "edge": edge_v2},
        "par_valeur": per_ticker,
        "n_seances": agg_v2["n"],
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    console.print(f"\n  [green]Rapport écrit dans {path}[/green]")


if __name__ == "__main__":
    sys.exit(main())
