"""
Moteur d'analyse actions — v2.

POURQUOI UNE V2
    La v1 (méthode `analyze` de stock_bot.py) additionne cinq conditions
    avec des poids fixes : RSI bas +2, sous Bollinger +2, EMA haussière
    +1, MACD positif +1, SMA20>SMA50 +1. Ça marche, mais ça souffre de
    quatre défauts structurels :

    1. ELLE MÉLANGE DEUX FAMILLES QUI SE CONTREDISENT.
       Le RSI et les bandes de Bollinger sont des indicateurs de RETOUR
       À LA MOYENNE : ils disent « c'est descendu trop bas, ça va
       remonter ». Le MACD et les croisements d'EMA sont des indicateurs
       de SUIVI DE TENDANCE : ils disent « ça monte, ça va continuer ».
       Dans une tendance haussière franche, le RSI reste au-dessus de 70
       pendant des semaines et crie « vends » sans discontinuer, pendant
       que le MACD crie « achète ». Les deux s'annulent et le score
       stagne au milieu — précisément quand le signal serait le plus
       utile. C'est ce qu'on observe sur tes relevés : sept valeurs sur
       neuf bloquées à 3/5.

    2. SES SEUILS SONT ABSOLUS.
       « Sous la bande basse » n'a pas le même sens sur une valeur qui
       bouge de 1 % par jour et sur une autre qui en fait 6.

    3. ELLE IGNORE LE VOLUME.
       Une cassure sans volume, c'est du bruit ; la même avec trois fois
       le volume habituel, c'est un mouvement. La v1 ne fait pas la
       différence.

    4. SES POIDS N'ONT JAMAIS ÉTÉ MESURÉS.
       Le +2 du RSI contre le +1 du MACD est un choix d'intuition. Voir
       stock_eval.py, qui existe pour arrêter de deviner.

CE QUE FAIT LA V2
    Elle détecte d'abord le RÉGIME du marché (tendance ou range), puis
    pondère les deux familles en conséquence : en tendance on écoute le
    suivi, en range on écoute le retour à la moyenne. Les distances sont
    exprimées en ATR (donc comparables d'une valeur à l'autre), et le
    volume vient confirmer ou atténuer le résultat.

    Le score reste sur 0-5 pour rester lisible et comparable à
    l'historique déjà enregistré.
"""

import json
import os
from datetime import datetime

import numpy as np
import pandas as pd
import ta


# Horizon par défaut de la projection, en séances.
DEFAULT_HORIZON = 5


# ══════════════════════════════════════════════════════════════════
#  UNIVERS SUIVI
# ══════════════════════════════════════════════════════════════════
#
#  Défini ici parce que trois fichiers en ont besoin (le bot, le
#  serveur, l'évaluateur) et qu'une liste recopiée trois fois finit
#  toujours par diverger.
#
#  POURQUOI CET UNIVERS-LÀ
#      Les neuf valeurs d'origine étaient presque toutes des
#      semi-conducteurs : quand NVDA montait, AMD et AVGO montaient
#      aussi. Un classement n'avait alors presque rien à départager, et
#      la mesure l'a confirmé — sur ces neuf titres, aucun critère ne
#      battait le panier une fois le risque pris en compte.
#
#      Sur ces 49 valeurs réparties en huit secteurs, le momentum 12-1
#      atteint le 100ᵉ percentile du tirage au sort et bat le panier à
#      la fois en performance, en Sharpe et en drawdown. Ce n'est pas
#      l'algorithme qui a changé, c'est le terrain : ce sont les écarts
#      ENTRE secteurs qui font vivre un classement, bien plus que ceux
#      entre deux fabricants de puces.
#
#  Le secteur sert à regrouper et à filtrer l'affichage. Il est écrit à
#  la main plutôt que récupéré via yfinance : c'est une donnée qui ne
#  bouge pratiquement jamais, et un appel réseau de plus par valeur
#  ralentirait chaque scan pour rien.

UNIVERSE = {
    # Technologie
    "AAPL": "Tech", "MSFT": "Tech", "NVDA": "Tech", "AMD": "Tech",
    "AVGO": "Tech", "INTC": "Tech", "MU": "Tech", "QCOM": "Tech",
    "TXN": "Tech", "ORCL": "Tech", "CRM": "Tech", "ADBE": "Tech",
    # Internet et communication
    "GOOGL": "Communication", "META": "Communication", "NFLX": "Communication",
    "DIS": "Communication", "CMCSA": "Communication",
    # Consommation
    "AMZN": "Consommation", "WMT": "Consommation", "COST": "Consommation",
    "PG": "Consommation", "KO": "Consommation", "PEP": "Consommation",
    "MCD": "Consommation", "NKE": "Consommation",
    # Santé
    "JNJ": "Santé", "UNH": "Santé", "PFE": "Santé", "MRK": "Santé",
    "ABBV": "Santé", "TMO": "Santé", "LLY": "Santé",
    # Finance
    "JPM": "Finance", "BAC": "Finance", "GS": "Finance", "MS": "Finance",
    "V": "Finance", "MA": "Finance", "AXP": "Finance",
    # Industrie et énergie
    "CAT": "Industrie", "BA": "Industrie", "HON": "Industrie",
    "GE": "Industrie", "XOM": "Énergie", "CVX": "Énergie", "LMT": "Industrie",
    # Services aux collectivités et immobilier
    "NEE": "Services", "DUK": "Services", "AMT": "Immobilier",
}

UNIVERSE_TICKERS = list(UNIVERSE)

# Les neuf valeurs suivies avant l'élargissement. Conservées pour
# pouvoir rejouer les mesures historiques à l'identique.
LEGACY_TICKERS = ["MU", "PLTR", "INTC", "AMD", "AVGO", "NVDA", "GOOGL", "ARKQ", "AIQ"]


def sector_of(ticker):
    return UNIVERSE.get(ticker.upper(), "—")


def normalize(df):
    """
    Accepte indifféremment les colonnes de yfinance (Close, High…) et
    celles de ccxt (close, high…).

    Les deux sources traversent ce fichier ; sans ce passage, une
    fonction sur deux planterait selon d'où viennent les données.
    """
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    out.columns = [str(c).lower() for c in out.columns]
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(out.columns)
    if missing:
        raise ValueError(f"colonnes manquantes : {', '.join(sorted(missing))}")
    return out


def _squash(x, scale=1.0):
    """
    Ramène une grandeur non bornée dans [-1, 1] via une tangente
    hyperbolique.

    Sans ça, un seul indicateur emballé (un MACD énorme après un saut de
    cours) écraserait tous les autres dans la moyenne. tanh sature en
    douceur : au-delà d'un certain écart, « très haussier » et
    « extrêmement haussier » comptent presque pareil — ce qui est le
    comportement qu'on veut, car la différence entre les deux n'est pas
    exploitable.
    """
    if x is None or not np.isfinite(x):
        return 0.0
    return float(np.tanh(x / scale))


# ══════════════════════════════════════════════════════════════════
#  INDICATEURS
# ══════════════════════════════════════════════════════════════════

def compute_indicators(df):
    """Calcule une fois pour toutes les séries dont le moteur a besoin."""
    d = normalize(df)
    close, high, low, volume = d["close"], d["high"], d["low"], d["volume"]

    ind = pd.DataFrame(index=d.index)
    ind["close"] = close
    ind["sma20"] = ta.trend.sma_indicator(close, window=20)
    ind["sma50"] = ta.trend.sma_indicator(close, window=50)
    ind["ema12"] = ta.trend.ema_indicator(close, window=12)
    ind["ema26"] = ta.trend.ema_indicator(close, window=26)
    ind["rsi"] = ta.momentum.rsi(close, window=14)
    ind["macd_hist"] = ta.trend.macd_diff(close)
    ind["bb_low"] = ta.volatility.bollinger_lband(close, window=20)
    ind["bb_high"] = ta.volatility.bollinger_hband(close, window=20)

    # ATR — l'amplitude moyenne d'une séance. C'est l'unité de mesure de
    # tout le moteur : dire « le cours est 2 ATR sous sa moyenne » a le
    # même sens sur NVDA et sur AIQ, alors que « 4 % sous sa moyenne »
    # n'en a aucun tant qu'on ne sait pas ce que la valeur fait un jour
    # ordinaire.
    ind["atr"] = ta.volatility.average_true_range(high, low, close, window=14)

    # ADX — la FORCE d'une tendance, indépendamment de son sens. C'est
    # lui qui décide du régime, donc de quelle famille d'indicateurs on
    # écoute.
    ind["adx"] = ta.trend.adx(high, low, close, window=14)

    ind["vol_sma20"] = ta.trend.sma_indicator(volume, window=20)
    ind["volume"] = volume

    ind["ret20"] = close.pct_change(20)
    ind["daily_vol"] = close.pct_change().rolling(20).std()

    # --- Momentum ---
    # Le rendement des N derniers mois EN EXCLUANT le dernier.
    #
    # C'est l'anomalie de marché la mieux documentée depuis Jegadeesh &
    # Titman (1993) : à horizon de quelques mois, les titres qui ont le
    # plus monté continuent en moyenne de surperformer. C'est le seul
    # critère de ce projet dont la valeur ait résisté à la mesure.
    #
    # Le mois écarté n'est pas un détail : à très court terme le
    # mouvement s'inverse (les titres qui viennent de bondir refluent).
    # Garder le dernier mois mélangerait deux effets contraires et
    # affaiblirait le signal. D'où le décalage de 21 séances, commun aux
    # trois versions.
    #
    # Trois profondeurs sont calculées pour pouvoir les départager sur
    # données réelles (stock_eval.py --ranking) plutôt que de retenir
    # 12 mois par tradition. Un horizon court réagit plus vite mais
    # capte davantage de bruit ; un horizon long est plus stable mais
    # s'accroche à des tendances déjà finissantes.
    for months, lookback in (("12", 252), ("6", 126), ("3", 63)):
        ind[f"mom_{months}_1"] = close.shift(21) / close.shift(lookback) - 1.0

    return ind


# ══════════════════════════════════════════════════════════════════
#  RÉGIME
# ══════════════════════════════════════════════════════════════════

def detect_regime(row):
    """
    Tendance, range, ou entre les deux — et avec quelle netteté.

    Les seuils 20/25 sur l'ADX sont la convention du domaine. Plutôt que
    de basculer brutalement de l'un à l'autre, on renvoie aussi une
    position continue entre les deux : un ADX de 24 ne devrait pas être
    traité exactement comme un ADX de 12.
    """
    adx = row["adx"]
    if not np.isfinite(adx):
        return {"regime": "indéterminé", "trend_weight": 0.5, "adx": None}

    # 0 = range pur, 1 = tendance franche, avec un dégradé entre 15 et 30.
    strength = float(np.clip((adx - 15) / 15, 0, 1))

    if adx >= 25:
        label = "tendance"
    elif adx < 20:
        label = "range"
    else:
        label = "mixte"

    # Le poids du suivi de tendance va de 0,25 (range pur) à 0,75
    # (tendance franche). On ne descend jamais à 0 : même dans un range,
    # ignorer complètement la direction du marché serait imprudent.
    return {
        "regime": label,
        "trend_weight": round(0.25 + 0.5 * strength, 3),
        "adx": round(float(adx), 1),
    }


# ══════════════════════════════════════════════════════════════════
#  COMPOSANTES
# ══════════════════════════════════════════════════════════════════

def _trend_components(row):
    """
    Famille « suivi de tendance ». Positif = haussier.

    Chaque écart est divisé par l'ATR avant d'être aplati : c'est ce qui
    rend les valeurs comparables entre elles et entre les actions.
    """
    atr = row["atr"] if np.isfinite(row["atr"]) and row["atr"] > 0 else None
    if atr is None:
        return {}

    # Les clés sont les libellés affichés : ils apparaissent tels quels
    # dans le dashboard et les notifications. Un nom lisible évite d'avoir
    # à traduire des identifiants techniques dans trois fichiers différents.
    comps = {
        # Écartement des deux EMA : l'impulsion courte contre la moyenne.
        "impulsion EMA": _squash((row["ema12"] - row["ema26"]) / atr, 0.5),
        # Histogramme MACD : l'accélération de cette impulsion.
        "accélération MACD": _squash(row["macd_hist"] / atr, 0.35),
        # Structure de fond : la moyenne 20 séances au-dessus de la 50.
        "structure": _squash((row["sma20"] - row["sma50"]) / atr, 1.5),
    }

    # Momentum 20 séances, rapporté à ce que la volatilité rendait
    # plausible sur la même durée. +8 % en un mois est remarquable sur
    # une valeur calme, banal sur une valeur agitée.
    if np.isfinite(row["ret20"]) and np.isfinite(row["daily_vol"]) and row["daily_vol"] > 0:
        expected = row["daily_vol"] * np.sqrt(20)
        comps["momentum"] = _squash(row["ret20"] / expected, 1.0)

    return comps


def _reversion_components(row):
    """
    Famille « retour à la moyenne ». Positif = opportunité d'achat,
    c'est-à-dire cours anormalement bas.
    """
    atr = row["atr"] if np.isfinite(row["atr"]) and row["atr"] > 0 else None
    comps = {}

    # RSI ramené dans [-1, 1] : 30 → +0,4 (survendu, donc achat),
    # 70 → -0,4 (suracheté, donc prudence).
    if np.isfinite(row["rsi"]):
        comps["rsi"] = float(np.clip((50 - row["rsi"]) / 50, -1, 1))

    # Position dans le canal de Bollinger, recentrée : 0 sur la bande
    # basse → +1, sur la bande haute → -1.
    span = row["bb_high"] - row["bb_low"]
    if np.isfinite(span) and span > 0:
        pos = (row["close"] - row["bb_low"]) / span
        comps["Bollinger"] = float(np.clip((0.5 - pos) * 2, -1, 1))

    # Écart à la moyenne 20, en ATR. Redondant avec Bollinger sur le
    # principe, mais pas sur l'échelle : Bollinger sature dès qu'on sort
    # du canal, celui-ci continue de mesurer à quel point on en est loin.
    if atr and np.isfinite(row["sma20"]):
        comps["écart moyenne"] = _squash(-(row["close"] - row["sma20"]) / atr, 2.0)

    return comps


def _volume_factor(row):
    """
    Multiplicateur de confirmation, entre 0,85 et 1,15.

    Le volume ne dit pas dans quel sens ça va — seulement si le marché
    y croit. Il ne peut donc pas produire de signal à lui seul : il ne
    fait que renforcer ou atténuer celui des prix. D'où un facteur
    multiplicatif volontairement modeste, plutôt qu'une composante de
    plus dans la moyenne.
    """
    v, ref = row["volume"], row["vol_sma20"]
    if not (np.isfinite(v) and np.isfinite(ref)) or ref <= 0:
        return 1.0, None
    ratio = v / ref
    # log2 : le double du volume habituel vaut +1, la moitié vaut -1.
    factor = 1.0 + 0.15 * float(np.clip(np.log2(ratio), -1, 1))
    return factor, round(float(ratio), 2)


# ══════════════════════════════════════════════════════════════════
#  PROJECTION
# ══════════════════════════════════════════════════════════════════

def project_range(row, horizon=DEFAULT_HORIZON):
    """
    Fourchette de cours attendue à `horizon` séances.

    UNE FOURCHETTE, PAS UN PRIX. Annoncer « NVDA vaudra 237,40 $ jeudi »
    serait une fausse précision : personne ne sait faire ça, et un
    chiffre unique donne une confiance que rien ne justifie. Ce que l'on
    peut estimer honnêtement, c'est l'AMPLITUDE plausible du mouvement —
    et pour ça la volatilité récente est un bien meilleur guide que
    n'importe quelle prédiction de direction.

    L'amplitude croît en racine carrée du temps : c'est le comportement
    d'une marche aléatoire, et sur quelques séances les cours en sont
    très proches. Cinq séances ne sont donc pas cinq fois plus
    incertaines qu'une seule, mais environ 2,2 fois.

    La bande couvre à peu près deux cas sur trois (un écart-type). Le
    tiers restant en sort — c'est attendu, pas un défaut.
    """
    price = row["close"]
    vol = row["daily_vol"]
    if not (np.isfinite(price) and np.isfinite(vol)) or vol <= 0:
        return None

    sigma = float(vol) * np.sqrt(horizon)
    return {
        "horizon_jours": horizon,
        "bas": round(float(price) * (1 - sigma), 2),
        "haut": round(float(price) * (1 + sigma), 2),
        "amplitude_pct": round(float(sigma) * 100, 2),
    }


# ══════════════════════════════════════════════════════════════════
#  ANALYSE
# ══════════════════════════════════════════════════════════════════

def raw_row(row, horizon=DEFAULT_HORIZON):
    """
    Note brute d'une séance, dans [-1, 1], et son explication.

    Le score sur 5 n'est PAS calculé ici : il dépend de la distribution
    des notes passées de la valeur (voir `_to_scores`).
    """
    regime = detect_regime(row)
    w_trend = regime["trend_weight"]
    w_rev = 1.0 - w_trend

    trend = _trend_components(row)
    reversion = _reversion_components(row)

    trend_avg = float(np.mean(list(trend.values()))) if trend else 0.0
    rev_avg = float(np.mean(list(reversion.values()))) if reversion else 0.0

    vol_factor, vol_ratio = _volume_factor(row)

    raw = float(np.clip((w_trend * trend_avg + w_rev * rev_avg) * vol_factor, -1, 1))

    # Les trois composantes les plus influentes, pour que le score reste
    # explicable. Un score qu'on ne peut pas justifier ne sert à rien :
    # on ne saurait ni lui faire confiance, ni le corriger.
    contributions = [(k, v * w_trend, "suivi") for k, v in trend.items()]
    contributions += [(k, v * w_rev, "retour") for k, v in reversion.items()]
    drivers = sorted(contributions, key=lambda c: abs(c[1]), reverse=True)[:3]

    return {
        "price": float(row["close"]),
        "raw_score": round(raw, 4),
        "regime": regime["regime"],
        "adx": regime["adx"],
        "trend_weight": w_trend,
        "trend_avg": round(trend_avg, 4),
        "reversion_avg": round(rev_avg, 4),
        "volume_ratio": vol_ratio,
        "volume_factor": round(vol_factor, 3),
        "rsi": round(float(row["rsi"]), 1) if np.isfinite(row["rsi"]) else None,
        "atr_pct": round(float(row["atr"]) / float(row["close"]) * 100, 2)
                   if np.isfinite(row["atr"]) and row["close"] else None,
        **{k: (round(float(row[k]), 4) if np.isfinite(row.get(k, np.nan)) else None)
           for k in ("mom_12_1", "mom_6_1", "mom_3_1")},
        "drivers": [{"nom": n, "poids": round(v, 3), "famille": f} for n, v, f in drivers],
        "projection": project_range(row, horizon),
    }


# Fenêtre de référence pour situer une note. Environ un an de bourse :
# assez long pour que la distribution soit stable, assez court pour
# suivre un changement de régime de la valeur.
CALIBRATION_WINDOW = 250
CALIBRATION_MIN = 40


def _to_scores(raws):
    """
    Convertit les notes brutes en scores 0-5, par comparaison à leur
    propre passé.

    POURQUOI CETTE ÉTAPE EXISTE
        Première version mesurée : le score ne dépassait jamais 3/5, donc
        le seuil de déclenchement à 4 n'était jamais franchi — l'algorithme
        était muet. En cause, la moyenne de composantes : pour qu'une note
        brute approche 1, il faudrait que TOUS les indicateurs soient
        simultanément à leur extrême. Ça n'arrive jamais, et la note reste
        tassée autour de zéro.

        Traduire la note en écart-type par rapport à ses propres valeurs
        récentes résout les deux problèmes d'un coup :
          — toute l'échelle 0-5 est utilisée, puisque la référence n'est
            plus un maximum théorique inatteignable mais ce que cette
            valeur produit habituellement ;
          — les scores deviennent comparables d'une action à l'autre, une
            note « haute pour NVDA » n'étant pas la même qu'une note haute
            pour AIQ.

        La fenêtre ne regarde que vers le passé : le score d'une séance
        n'est calibré que sur les séances qui la précèdent, jamais sur
        celles qui la suivent. Sans ça on tricherait avec le futur et
        toute l'évaluation serait faussée.
    """
    s = pd.Series(raws, dtype=float)
    # `shift(1)` : la séance du jour est exclue de sa propre référence.
    ref = s.shift(1)
    mean = ref.rolling(CALIBRATION_WINDOW, min_periods=CALIBRATION_MIN).mean()
    std = ref.rolling(CALIBRATION_WINDOW, min_periods=CALIBRATION_MIN).std()

    z = (s - mean) / std.replace(0, np.nan)
    # ±2 écarts-types couvrent l'essentiel de la distribution : au-delà,
    # « exceptionnel » et « très exceptionnel » ne se distinguent plus
    # utilement.
    strength = (z / 2.0).clip(-1, 1).fillna(0.0)

    buy = (strength.clip(lower=0) * 5).round().astype(int)
    sell = ((-strength).clip(lower=0) * 5).round().astype(int)
    return buy.tolist(), sell.tolist(), strength.round(4).tolist()


def _recommend(buy, sell):
    if buy >= 4:
        return "BUY"
    if sell >= 3:
        return "SELL"
    if buy >= 3:
        return "WATCH"
    return "HOLD"


def analyze_series(df, horizon=DEFAULT_HORIZON):
    """
    Rejoue le moteur sur toutes les séances exploitables.

    Utilisé par stock_eval.py : pour mesurer un algorithme, il faut
    pouvoir l'appliquer au passé séance par séance. Chaque ligne
    n'utilise que les colonnes de sa propre date — les indicateurs sont
    tous calculés sur des fenêtres glissantes qui ne regardent que vers
    l'arrière, donc aucune information du futur ne remonte.
    """
    ind = compute_indicators(df)
    usable = ind.dropna(subset=["sma50", "adx", "atr", "daily_vol"])
    if usable.empty:
        return []

    records = [{"date": idx, **raw_row(row, horizon)} for idx, row in usable.iterrows()]
    buys, sells, strengths = _to_scores([r["raw_score"] for r in records])

    for rec, b, s, st in zip(records, buys, sells, strengths):
        rec["buy_score"] = b
        rec["sell_score"] = s
        rec["strength"] = st
        rec["recommendation"] = _recommend(b, s)
    return records


def analyze(df, horizon=DEFAULT_HORIZON):
    """
    Analyse la dernière séance disponible d'un historique.

    Passe par `analyze_series` parce que le score du jour n'a de sens
    que rapporté aux notes des séances précédentes — il faut donc
    calculer toute la série, pas seulement la dernière ligne. Fournir au
    moins un an d'historique donne la meilleure calibration.
    """
    series = analyze_series(df, horizon)
    if not series:
        raise ValueError("historique trop court pour analyser cette valeur")
    return series[-1]


# ══════════════════════════════════════════════════════════════════
#  V1 — pour comparaison
# ══════════════════════════════════════════════════════════════════

def analyze_v1_row(row):
    """
    Reproduit exactement le score de la v1 (stock_bot.analyze), pour
    pouvoir comparer les deux sur les mêmes données.

    Sans point de comparaison, « la v2 gagne 3 % » ne veut rien dire.
    """
    buy = 0
    rsi, close = row["rsi"], row["close"]
    bb_low, bb_high = row["bb_low"], row["bb_high"]

    if rsi < 30:
        buy += 2
    elif rsi < 45:
        buy += 1

    if close < bb_low:
        buy += 2
    elif close < (bb_low + bb_high) / 2:
        buy += 1

    if row["ema12"] > row["ema26"]:
        buy += 1
    if row["macd_hist"] > 0:
        buy += 1
    if row["sma20"] > row["sma50"]:
        buy += 1

    sell = 0
    if rsi > 70:
        sell += 2
    if close > bb_high:
        sell += 2
    if row["ema12"] < row["ema26"]:
        sell += 1
    if row["macd_hist"] < 0:
        sell += 1

    # La v1 plafonne à 7 en théorie ; on borne à 5 comme le fait
    # l'affichage d'origine.
    return {"buy_score": min(buy, 5), "sell_score": min(sell, 5), "price": float(close)}


def analyze_v1_series(df):
    """Rejoue la v1 sur tout l'historique."""
    ind = compute_indicators(df)
    usable = ind.dropna(subset=["sma50", "bb_low", "macd_hist", "rsi"])
    return [{"date": idx, **analyze_v1_row(row)} for idx, row in usable.iterrows()]


# ══════════════════════════════════════════════════════════════════
#  POSITIONS : P&L ET DIVISIONS D'ACTIONS
# ══════════════════════════════════════════════════════════════════
#
#  Les cours viennent de yfinance avec auto_adjust=True : ils sont donc
#  ajustés RÉTROACTIVEMENT des dividendes et des divisions d'actions.
#
#  Le prix que tu saisis dans « Mes positions », lui, est celui que tu as
#  réellement payé chez ton courtier — un prix nominal, jamais retraité.
#
#  Tant qu'aucune division n'a lieu, les deux référentiels coïncident et
#  le P&L est juste. Mais dès qu'une valeur se divise (NVDA l'a fait 10
#  pour 1 en 2024), le cours ajusté est divisé par dix d'un jour à
#  l'autre alors que ton prix d'achat, lui, ne bouge pas. Le tableau
#  afficherait alors une perte de 90 % sur une position qui n'a rien
#  perdu — et le nombre d'actions détenues, lui, aurait décuplé.
#
#  On corrige en ramenant le prix d'achat dans le référentiel courant.

_SPLITS_CACHE = {}


def facteur_split(ticker, depuis_iso):
    """
    Produit des divisions d'actions subies depuis la date d'achat.

    Renvoie 1.0 s'il n'y en a eu aucune, ou si l'information n'est pas
    disponible : en cas de doute on ne corrige rien, ce qui laisse le
    P&L tel quel plutôt que de le fausser dans l'autre sens.

    Le résultat est mis en cache : une position est réaffichée à chaque
    scan, et interroger le réseau à chaque fois serait absurde pour une
    donnée qui change au plus une fois par an.
    """
    if not depuis_iso:
        return 1.0
    cle = (str(ticker).upper(), str(depuis_iso)[:10])
    if cle in _SPLITS_CACHE:
        return _SPLITS_CACHE[cle]

    facteur = 1.0
    try:
        import yfinance as yf
        splits = yf.Ticker(cle[0]).splits
        if splits is not None and len(splits):
            index = splits.index
            if getattr(index, "tz", None) is not None:
                index = index.tz_localize(None)
            recents = splits[index > pd.Timestamp(cle[1])]
            if len(recents):
                facteur = float(recents.prod())
    except Exception:
        facteur = 1.0            # réseau indisponible, ticker inconnu…

    _SPLITS_CACHE[cle] = facteur
    return facteur


def pnl_position(position, prix_actuel, ticker=None):
    """
    P&L d'une position, corrigé des divisions d'actions.

    Source unique de vérité : la formule était auparavant recopiée à
    quatre endroits (le serveur, deux affichages du bot, le panneau de
    vente). Une correction partielle en aurait laissé trois fausses.
    """
    try:
        entree = float(position.get("entry_price", 0))
        quantite = float(position.get("quantity", 0))
    except (TypeError, ValueError):
        return None
    if entree <= 0:
        return None

    f = facteur_split(ticker or position.get("ticker", ""),
                      position.get("entry_date"))
    entree_eff = entree / f
    quantite_eff = quantite * f

    return {
        "quantity": quantite_eff,
        "entry_price": entree_eff,
        "pnl_pct": (prix_actuel - entree_eff) / entree_eff * 100,
        "pnl_eur": (prix_actuel - entree_eff) * quantite_eff,
        "split": f,
    }


# ══════════════════════════════════════════════════════════════════
#  CALENDRIER DES RÉSULTATS
# ══════════════════════════════════════════════════════════════════
#
#  POURQUOI CETTE DONNÉE ET PAS L'ACTUALITÉ EN GÉNÉRAL
#      Une dépêche est intégrée au cours en quelques secondes. Quand tu
#      la lis, quand yfinance te la sert, quand le bot la scanne cinq
#      minutes plus tard, elle est déjà dans le prix : tu n'échanges pas
#      contre le marché, tu échanges contre des machines qui l'ont lue
#      avant toi. Et on ne pourrait même pas le vérifier — les archives
#      de presse sont horodatées à la dernière modification de l'article,
#      donc un backtest sur les news « verrait » l'information après le
#      mouvement qu'elle est censée expliquer. Le résultat serait
#      magnifique et entièrement faux.
#
#      Une DATE DE PUBLICATION DE RÉSULTATS, elle, est connue des
#      semaines à l'avance et n'a rien de confidentiel. Elle ne prédit
#      pas le sens du mouvement — mais elle annonce qu'il y en aura
#      probablement un, et c'est déjà une information utile : acheter la
#      veille d'une publication revient à jouer à pile ou face sur un
#      écart d'ouverture.
#
#  MISE EN CACHE
#      Interroger yfinance pour 49 valeurs prend plusieurs dizaines de
#      secondes. Le bot, qui tourne en continu, rafraîchit le fichier
#      une fois par jour ; le serveur web ne fait que le LIRE, et ne
#      bloque donc jamais sur le réseau. Seules les dates sont stockées :
#      le nombre de jours restants se recalcule à l'affichage, sinon
#      « dans 3 jours » resterait affiché toute la semaine.

EARNINGS_CACHE = "data/earnings_cache.json"
EARNINGS_TTL_HEURES = 24


def _lire_cache_earnings():
    try:
        with open(EARNINGS_CACHE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def prochaine_publication(ticker):
    """Date de la prochaine publication de résultats (AAAA-MM-JJ), ou None."""
    return _lire_cache_earnings().get("dates", {}).get(str(ticker).upper())


def jours_avant_publication(ticker, aujourdhui=None):
    """Nombre de jours avant la prochaine publication, ou None."""
    date = prochaine_publication(ticker)
    if not date:
        return None
    try:
        jour = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (jour - (aujourdhui or datetime.now().date())).days


def cache_earnings_perime():
    """Le calendrier a-t-il besoin d'être rafraîchi ?"""
    data = _lire_cache_earnings()
    genere = data.get("genere_le")
    if not genere:
        return True
    try:
        age = datetime.now() - datetime.fromisoformat(genere)
    except (ValueError, TypeError):
        return True
    return age.total_seconds() > EARNINGS_TTL_HEURES * 3600


def rafraichir_earnings(tickers, force=False):
    """
    Interroge yfinance et réécrit le calendrier.

    Appelé par le bot, jamais par le serveur web : c'est long, et une
    page qui attend le réseau pour 49 valeurs n'est pas une page.

    Une valeur dont la date est introuvable est simplement absente du
    cache — on préfère ne rien afficher plutôt qu'une date inventée.
    """
    if not force and not cache_earnings_perime():
        return None

    import yfinance as yf
    aujourdhui = pd.Timestamp.now().normalize()
    dates = {}

    for ticker in tickers:
        t = str(ticker).upper()
        try:
            ed = yf.Ticker(t).earnings_dates
            if ed is None or not len(ed):
                continue
            index = ed.index
            if getattr(index, "tz", None) is not None:
                index = index.tz_localize(None)
            futures = sorted(d for d in index if d >= aujourdhui)
            if futures:
                dates[t] = futures[0].strftime("%Y-%m-%d")
        except Exception:
            continue          # ticker inconnu, réseau, format inattendu

    os.makedirs("data", exist_ok=True)
    tmp = EARNINGS_CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"genere_le": datetime.now().isoformat(), "dates": dates}, f, indent=2)
    os.replace(tmp, EARNINGS_CACHE)
    return len(dates)
