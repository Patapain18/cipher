"""
Le banc des prévisions — étape 1 : le test sur 6 ans (le critère 1).

CE QUE ÇA FAIT
    Quatre méthodes, figées avant de regarder (plan validé le 17/11/2026,
    détaillé le 20/11, fin de BRIEF.md), disent chaque soir, pour chacune
    des 49 valeurs, si elle montera le lendemain (en euros) :
      a. le score technique du bot (moteur v2) : « hausse » si sa force est
         au-dessus de 0 ;
      b. le momentum 12-1 : « hausse » s'il est positif ;
      c. une régression logistique sur 14 mesures ;
      d. un gradient boosting sur les mêmes 14 mesures.
    c et d apprennent sur toutes les années d'avant, puis sont notées sur
    l'année suivante, avec une séance de purge entre les deux. Six années
    sont notées, de nov. 2019 à oct. 2025.
    Chaque méthode est comparée à deux repères : le tirage au sort qui dit
    « baisse » aussi souvent qu'elle, compté par sa moyenne exacte (choix de
    Mathis, 20/11/2026), et « ça monte toujours ». Le critère 1 : « vu » face
    aux deux, à 99 %, sur les 6 ans ensemble. La porte A (le coffre) ne
    s'ouvre que si une candidate le passe. « Comme hier » (demain dans le
    même sens qu'aujourd'hui) est un repère de plus : il ne concourt pas.

CE QUE ÇA NE FAIT PAS
    Aucune prévision du jour, aucun conseil, aucun ordre. Le critère 2 (la
    stratégie de 200 €) et les soirs de résultats viennent ensuite, si
    Mathis le veut (choix du 20/11/2026). Le coffre (après le 31/10/2025)
    n'est pas lu : son ouverture se construira à la porte A, si elle s'ouvre.

LES DONNÉES
    Le cache data/market/*_10y.csv, lu sans rien écrire, comme l'étape 0 :
    ouverture, plus haut, plus bas, clôture et volume des 49 valeurs et de
    SPY (cours ajustés des dividendes et des fractionnements), et
    l'ouverture de l'euro/dollar. Tout est coupé au 31/10/2025 dès la lecture.

ÉCRIT
    Seulement par la vraie mesure : data/banc/etape1.json (le résultat,
    jamais écrasé) et data/banc/etape1-previsions.csv.gz (les sorties de
    chaque méthode, pour un recomptage à part).

LANCER
    python3 banc.py --mesurer            # la mesure, une seule fois (quelques minutes)
    python3 banc.py --mesurer --a-blanc  # toute la chaîne, des rendements tirés au hasard, rien d'écrit
    python3 banc.py --verifier           # les cas calculés à la main (étapes 0 et 1)
"""

import contextlib
import copy
import datetime
import hashlib
import io
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

import banc
import stock_engine as engine
from banc import TOUT, CacheInvalide

DOSSIER_BANC = os.path.join(banc.ROOT, "data", "banc")
NOM_MESURE = "etape1.json"
NOM_PREVISIONS = "etape1-previsions.csv.gz"
COLONNES = ("Open", "High", "Low", "Close", "Volume")
GRAINE = 20261120                # les tirages des blocs de l'étape 1 (une graine par période)
GRAINE_A_BLANC = 20261121        # les rendements inventés de --a-blanc
PURGE = 1                        # séances retirées entre l'apprentissage et l'année notée
SEUIL_PROBA = 0.5                # c et d disent « hausse » au-dessus de 50 %

# Les réglages figés le 16/11/2026 (plan du banc), avant de regarder.
REGLAGES_C = {"C": 1.0, "max_iter": 1000}
REGLAGES_D = {"max_iter": 200, "learning_rate": 0.05, "max_leaf_nodes": 15, "min_samples_leaf": 500,
              "l2_regularization": 1.0, "early_stopping": False, "random_state": 20261116}

# Les 14 mesures de c et d, toutes des rapports (jamais un prix ni un volume en
# niveau : Yahoo les réajuste après chaque fractionnement, qui suit souvent une
# grosse hausse), en dollars, connues à la clôture du soir.
MESURES = ("r1", "r5", "r21", "vol20", "rsi14", "macd_atr", "ecart20_atr", "sma20_50", "volume20",
           "mom12_1", "force", "spy1", "spy5", "eurusd1")
CANDIDATES = {"a": "le score technique", "b": "le momentum 12-1",
              "c": "la régression logistique", "d": "le gradient boosting"}
METHODES = ("a", "b", "c", "d", "hier")
NOMS = {**{m: f"{m}. {nom}" for m, nom in CANDIDATES.items()}, "hier": "« comme hier »"}
# La sortie continue de chaque méthode, et le seuil au-dessus duquel elle dit « hausse ».
SORTIE = {"a": "force", "b": "mom12_1", "hier": "r1"}
SEUILS = {"a": 0.0, "b": 0.0, "c": SEUIL_PROBA, "d": SEUIL_PROBA, "hier": 0.0}


# ══════════════════════════════════════════════════════════════════
#  LES DONNÉES ET LES 14 MESURES
# ══════════════════════════════════════════════════════════════════

@dataclass
class Donnees:
    dates: pd.DatetimeIndex   # toutes les séances du cache coupé
    valeurs: list             # les valeurs (sans SPY)
    X: np.ndarray             # séances × valeurs × 14 mesures (NaN : pas encore calculable)
    r: np.ndarray             # séances × valeurs : le rendement du LENDEMAIN en euros (la réponse)


def charger_cours(dossier=banc.MARCHE, tickers=None, fin=banc.FIN_AVANT_COFFRE):
    """
    Les cours des valeurs et de SPY (ouverture, plus haut, plus bas,
    clôture, volume, en dollars) et l'ouverture de l'euro/dollar, coupés à
    `fin` dès la lecture : rien après n'est gardé.
    """
    tickers = list(engine.UNIVERSE_TICKERS if tickers is None else tickers)
    cours = {t: banc._lire_colonnes(dossier, t, fin, COLONNES) for t in tickers + [banc.REFERENCE]}
    change = banc._lire(dossier, banc.CHANGE, fin, "Open").dropna()
    if fin is not None and (max(df.index.max() for df in cours.values()) > fin or change.index.max() > fin):
        raise RuntimeError("une date après la coupure a passé : le coffre serait regardé")
    return tickers, cours, change


def change_sur_un_jour(change):
    """L'euro/dollar d'une ouverture à la suivante, rangé à la date de la plus récente des deux."""
    return change / change.shift(1) - 1


def connu_le_soir(fx1, dates):
    """
    Pour chaque soir J : la dernière valeur de `fx1` datée de J ou d'avant
    (l'ouverture du jour J arrive vers 1 h du matin à Paris, bien avant le
    soir), vide si elle date de plus de 5 jours. Jamais une ligne d'après J.
    """
    return fx1.reindex(dates, method="ffill", tolerance=pd.Timedelta(days=banc.ECART_CHANGE_MAX))


def mesures_d_une_valeur(df, spy, fx1):
    """
    Les 14 mesures d'une valeur, chaque soir où elle a ses cinq colonnes.
    Elles ne lisent que le soir même et le passé : les indicateurs du
    moteur sont des fenêtres qui regardent en arrière, et l'euro/dollar est
    celui de la dernière ouverture datée du jour ou d'avant (vers 1 h du
    matin à Paris), jamais d'une ligne suivante. Une mesure infinie (une
    division par une amplitude nulle) devient vide.
    `spy` : les clôtures de SPY ; `fx1` : change_sur_un_jour(ouvertures).
    """
    d = df.dropna()
    ind = engine.compute_indicators(d)
    c = ind["close"]
    m = pd.DataFrame(index=d.index)
    m["r1"] = c / c.shift(1) - 1
    m["r5"] = c / c.shift(5) - 1
    m["r21"] = c / c.shift(21) - 1
    m["vol20"] = ind["daily_vol"]
    m["rsi14"] = ind["rsi"]
    m["macd_atr"] = (ind["ema12"] - ind["ema26"]) / ind["atr"]        # la ligne MACD, en ATR 14
    m["ecart20_atr"] = (c - ind["sma20"]) / ind["atr"]
    m["sma20_50"] = ind["sma20"] / ind["sma50"] - 1
    m["volume20"] = ind["volume"] / ind["vol_sma20"]
    m["mom12_1"] = ind["mom_12_1"]
    force = pd.Series({x["date"]: x["strength"] for x in engine.analyze_series(d)}, dtype=float)
    m["force"] = force.reindex(d.index)
    spy = spy.dropna()
    m["spy1"] = (spy / spy.shift(1) - 1).reindex(d.index)
    m["spy5"] = (spy / spy.shift(5) - 1).reindex(d.index)
    m["eurusd1"] = connu_le_soir(fx1, d.index)
    return m[list(MESURES)].replace([np.inf, -np.inf], np.nan)


def preparer(tickers, cours, change):
    """
    Les 14 mesures de chaque valeur et sa réponse, sur toutes les séances du
    cache : la réponse du soir J est le rendement en euros de la clôture de
    J à celle de J+1, chaque clôture divisée par l'ouverture de la ligne
    suivante de l'euro/dollar (la règle de l'étape 0, banc.en_euros).
    """
    clotures = pd.DataFrame({t: cours[t]["Close"] for t in tickers + [banc.REFERENCE]}).sort_index()
    dates = clotures.index
    r = banc.rendements_du_lendemain(banc.en_euros(clotures, change))[tickers]
    fx1 = change_sur_un_jour(change)
    spy = cours[banc.REFERENCE]["Close"]
    X = np.full((len(dates), len(tickers), len(MESURES)), np.nan)
    for j, t in enumerate(tickers):
        X[:, j, :] = mesures_d_une_valeur(cours[t], spy, fx1).reindex(dates).to_numpy(float)
    return Donnees(dates=dates, valeurs=list(tickers), X=X, r=r.to_numpy(float))


# ══════════════════════════════════════════════════════════════════
#  LES QUATRE MÉTHODES ET « COMME HIER »
# ══════════════════════════════════════════════════════════════════

@contextlib.contextmanager
def _sans_faux_avertissements():
    """
    numpy 2.0.2 (/usr/bin/python3) signale de faux « divide by zero / overflow
    / invalid value encountered in matmul » pendant une régression
    logistique, aux coefficients justes (vu le 16/11/2026) : on les tait ici
    seulement. Les sorties sont vérifiées finies juste après.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=r".*encountered in (matmul|dot)", category=RuntimeWarning)
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            yield


def seances_d_apprentissage(dates, debut_annee, purge=PURGE):
    """
    Les numéros des séances où c et d apprennent pour l'année notée qui
    commence à `debut_annee` : toutes celles d'avant, moins les `purge`
    dernières. Sans la purge, la réponse de la dernière séance apprise
    serait lue avec l'euro/dollar de la nuit qui suit la première séance
    notée.
    """
    premiere = int(dates.searchsorted(debut_annee))
    return np.arange(max(premiere - purge, 0))


def reponses(r):
    """1 si la valeur a monté le lendemain (strictement : un rendement nul est une baisse), 0 sinon ou sans lendemain."""
    r = np.asarray(r, float)
    return (np.where(np.isfinite(r), r, 0.0) > 0).astype(float)


def lignes_d_apprentissage(X, r, seances):
    """
    Les valeurs-jours où c et d apprennent, parmi les `seances` données :
    celles qui ont leurs 14 mesures et leur réponse. Rend (mesures, réponses
    0/1, le masque des valeurs-jours gardées).
    """
    Xa, ra = X[seances].reshape(-1, X.shape[2]), r[seances].ravel()
    ok = np.isfinite(Xa).all(axis=1) & np.isfinite(ra)
    return Xa[ok], reponses(ra[ok]).astype(int), ok


def _proba_hausse(modele, X):
    """La probabilité de « hausse » (la classe 1), cherchée par son nom, pas par sa place."""
    p = modele.predict_proba(X)[:, list(modele.classes_).index(1)]
    if not np.isfinite(p).all():
        raise FloatingPointError("un modèle a rendu une probabilité non finie")
    return p


def logistique(Xa, ya, Xt, reglages=REGLAGES_C):
    """
    c : centrée et réduite sur les seules lignes d'apprentissage, puis la
    régression logistique. La probabilité de « hausse » est calculée
    valeur-jour par valeur-jour (une somme de 14 termes, toujours dans le
    même ordre) : le produit de matrices de predict_proba additionne dans
    un ordre qui dépend du nombre de lignes, et la même valeur-jour changeait
    au 16e chiffre selon la longueur de l'année notée (vu le 20/11/2026).
    """
    from scipy.special import expit
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    echelle = StandardScaler().fit(Xa)
    with _sans_faux_avertissements():
        modele = LogisticRegression(**reglages).fit(echelle.transform(Xa), ya)
    if list(modele.classes_) != [0, 1]:
        raise ValueError("la régression logistique attend les deux classes 0 et 1")
    p = expit((echelle.transform(Xt) * modele.coef_[0]).sum(axis=1) + modele.intercept_[0])
    if not np.isfinite(p).all():
        raise FloatingPointError("un modèle a rendu une probabilité non finie")
    return p


def boosting(Xa, ya, Xt, reglages=REGLAGES_D):
    """d : le gradient boosting (HistGradientBoostingClassifier), réglages figés."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    modele = HistGradientBoostingClassifier(**reglages).fit(Xa, ya)
    return _proba_hausse(modele, Xt)


def apprendre_et_prevoir(donnees, annees, reglages_c=REGLAGES_C, reglages_d=REGLAGES_D):
    """
    c et d, à pas glissants : pour chaque année notée, un seul modèle pour
    toutes les valeurs, appris sur toutes les valeurs-jours d'avant qui ont
    leurs 14 mesures et leur réponse (moins la purge), puis la probabilité
    de « hausse » de chaque valeur-jour de l'année. Vide hors des années
    notées. Rend (probabilités de c, de d, {année: (première et dernière
    séance apprise, nombre de lignes)}).
    """
    X, r, dates = donnees.X, donnees.r, donnees.dates
    n, v, k = X.shape
    pc, pd_ = np.full((n, v), np.nan), np.full((n, v), np.nan)
    appris = {}
    for a, b in annees:
        appr = seances_d_apprentissage(dates, a)
        test = np.arange(dates.searchsorted(a), dates.searchsorted(b, side="right"))
        Xa, ya, ok = lignes_d_apprentissage(X, r, appr)
        if len(np.unique(ya)) < 2:
            raise CacheInvalide(f"l'année notée {banc._nom_annee(a, b)} n'a pas assez de séances d'apprentissage")
        lignes_ok = ok.reshape(len(appr), v).any(axis=1)
        appris[banc._nom_annee(a, b)] = (dates[appr][lignes_ok][0], dates[appr][lignes_ok][-1], int(ok.sum()))
        Xt = X[test].reshape(-1, k)
        okt = np.isfinite(Xt).all(axis=1)
        if not okt.any():
            continue
        c_t, d_t = np.full(len(Xt), np.nan), np.full(len(Xt), np.nan)
        c_t[okt] = logistique(Xa, ya, Xt[okt], reglages_c)
        d_t[okt] = boosting(Xa, ya, Xt[okt], reglages_d)
        pc[test], pd_[test] = c_t.reshape(len(test), v), d_t.reshape(len(test), v)
    return pc, pd_, appris


def sorties(donnees, annees, reglages_c=REGLAGES_C, reglages_d=REGLAGES_D):
    """
    La sortie continue de chaque méthode, chaque soir (séances × valeurs ;
    vide = pas d'avis) : la force du score (a), le momentum 12-1 (b), la
    probabilité de « hausse » (c, d) et le rendement du jour en dollars
    (« comme hier »). Rend (sorties, ce que c et d ont appris).
    """
    i = {nom: MESURES.index(nom) for nom in SORTIE.values()}
    s = {m: donnees.X[:, :, i[SORTIE[m]]].copy() for m in SORTIE}
    s["c"], s["d"], appris = apprendre_et_prevoir(donnees, annees, reglages_c, reglages_d)
    return s, appris


def avis(m, sortie):
    """1 = « hausse » (la sortie au-dessus du seuil de la méthode), 0 = « baisse », vide = pas d'avis."""
    x = np.asarray(sortie, float)
    return np.where(np.isfinite(x), (x > SEUILS[m]).astype(float), np.nan)


# ══════════════════════════════════════════════════════════════════
#  LE CRITÈRE 1
# ══════════════════════════════════════════════════════════════════

def _face(d, t, niveau):
    """Un écart OBSERVÉ et ses tirages : le plus petit écart que le test déclarait « vu », « vu », et
    « vu_dessous » (l'écart est nettement NÉGATIF : la même règle, dans l'autre sens, avec son seuil)."""
    t = np.asarray(t, float)
    seuil = banc.seuil_critique(d, t, niveau)
    seuil_dessous = banc.seuil_critique(-d, -t, niveau)
    return {"ecart": float(d), "seuil": seuil, "vu": bool(d > seuil),
            "seuil_dessous": seuil_dessous, "vu_dessous": bool(-d > seuil_dessous)}


def face_au_sens_du_jour(d_t, t_t, d_h, t_h, niveau=banc.NIVEAU):
    """
    L'avance d'une candidate sur le hasard (d_t, et ses tirages t_t), comparée à celle que donne
    le seul sens du jour, dans ses DEUX versions : « comme hier » (son avance d_h, ses tirages t_h)
    et son contraire (−d_h, −t_h). Chacune est comptée face à son propre tirage au sort, sur les
    mêmes blocs. « vu » (nettement mieux que le sens du jour) seulement si la candidate est vue face
    aux DEUX : aucun sens n'est choisi après avoir regardé. Le choisir sur les chiffres (avec |d_h|)
    disait « nettement moins bien » 5 à 11 fois sur 100 sans aucun talent (relecture du 20/11/2026).
    Rend la moins bonne des deux comparaisons (écart d_t − |d_h|, borne basse) et le détail. Pas de
    « nettement moins bien » : ce test-là n'aurait pas son 1 sur 100.
    """
    t_t, t_h = np.asarray(t_t, float), np.asarray(t_h, float)
    hier, contraire = _face(d_t - d_h, t_t - t_h, niveau), _face(d_t + d_h, t_t + t_h, niveau)
    return {"ecart": min(hier["ecart"], contraire["ecart"]),
            "borne": min(hier["ecart"] - hier["seuil"], contraire["ecart"] - contraire["seuil"]),
            "vu": hier["vu"] and contraire["vu"], "comme_hier": hier, "contraire": contraire}


def critere_1(m, y, valide, poids, sens_du_jour=None, niveau=banc.NIVEAU):
    """
    Les bonnes réponses d'une méthode sur une période (séances × valeurs) :
    face à la moyenne exacte des tirages au sort qui disent « baisse » aussi
    souvent qu'elle, et face à « ça monte toujours ». Pour chaque repère :
    l'écart OBSERVÉ, le plus petit écart que le test déclarait « vu »
    (banc.seuil_critique, sur les tirages des blocs donnés, les mêmes pour
    toutes les méthodes) et « vu ». « passe » : vue face au tirage ET face à
    « ça monte toujours ». « barre » : le taux de bonnes réponses qu'il lui
    fallait pour passer.
    `sens_du_jour` (l'écart de « comme hier » face au tirage, et ses tirages
    sur les MÊMES blocs) : ajoute « sens_du_jour », la comparaison avec le
    seul sens du jour dans ses deux versions (face_au_sens_du_jour).
    Rend (résultat, tirages de son écart face au tirage).
    """
    v = np.asarray(valide, bool)
    m0, y0 = np.where(v, m, 0.0), np.where(v, y, 0.0)
    n = int(v.sum())
    mm, yy, my = m0.sum() / n, y0.sum() / n, (m0 * y0).sum() / n
    bonnes = 1 - mm - yy + 2 * my
    (d_t, d_m), (t_t, t_m) = banc.ecarts(m0, y0, v, poids)
    face = {"tirage": _face(d_t, t_t, niveau), "monte": _face(d_m, t_m, niveau)}
    if sens_du_jour is not None:
        d_h, t_h = sens_du_jour
        face["sens_du_jour"] = face_au_sens_du_jour(d_t, t_t, d_h, t_h, niveau)
    tirage = mm * yy + (1 - mm) * (1 - yy)
    return ({"n": n, "baisse": float(1 - mm), "bonnes": float(bonnes), "tirage": float(tirage), "monte": float(yy),
             "face": face, "passe": face["tirage"]["vu"] and face["monte"]["vu"],
             "barre": float(max(tirage + face["tirage"]["seuil"], yy + face["monte"]["seuil"]))}, t_t)


def periodes_notees(annees):
    """Toute la période notée, puis chaque année : [(nom, début, fin)]."""
    return [(TOUT, annees[0][0], annees[-1][1])] + [(banc._nom_annee(a, b), a, b) for a, b in annees]


def evaluer(donnees, s, annees, tirages=banc.TIRAGES, bloc=banc.BLOC, graine=GRAINE, niveau=banc.NIVEAU):
    """
    Le critère 1 de chaque méthode, sur les 6 ans puis année par année. Les
    valeurs-jours notées sont celles de l'étape 0 (les séances des années
    notées qui ont au moins un lendemain), moins celles où une méthode
    n'aurait pas d'avis : les mêmes pour toutes. Les tirages des blocs sont
    les mêmes pour toutes les méthodes d'une période.
    """
    dates = donnees.dates
    dans = (dates >= annees[0][0]) & (dates <= annees[-1][1])
    lignes = np.flatnonzero(dans & np.isfinite(donnees.r).any(axis=1))
    for a, b in annees:
        nb = int(((dates[lignes] >= a) & (dates[lignes] <= b)).sum())
        if nb < banc.SEANCES_MIN_ANNEE:
            raise CacheInvalide(f"l'année notée {banc._nom_annee(a, b)} n'a que {nb} séances dans le cache "
                                f"(au moins {banc.SEANCES_MIN_ANNEE} attendues) : rien n'est calculé")
    r = donnees.r[lignes]
    av = {m: avis(m, s[m][lignes]) for m in METHODES}
    a_un_lendemain = np.isfinite(r)
    valide = a_un_lendemain & np.logical_and.reduce([np.isfinite(av[m]) for m in METHODES])
    y = reponses(r)
    rap = {"debut": dates[lignes[0]], "fin": dates[lignes[-1]], "seances": len(lignes),
           "valeurs": len(donnees.valeurs), "valeurs_jours": int(valide.sum()),
           "retirees": int((a_un_lendemain & ~valide).sum()), "annees": [banc._nom_annee(a, b) for a, b in annees],
           "reglages": {"tirages": tirages, "bloc": bloc, "niveau": niveau}, "resultats": {}}
    for i, (nom, a, b) in enumerate(periodes_notees(annees)):
        p = (dates[lignes] >= a) & (dates[lignes] <= b)
        poids = banc.poids_blocs(int(p.sum()), bloc, tirages, graine + i)
        hier, t_hier = critere_1(np.nan_to_num(av["hier"][p]), y[p], valide[p], poids, niveau=niveau)
        rap["resultats"][nom] = {
            **{m: critere_1(np.nan_to_num(av[m][p]), y[p], valide[p], poids,
                            sens_du_jour=(hier["face"]["tirage"]["ecart"], t_hier), niveau=niveau)[0]
               for m in CANDIDATES},
            "hier": hier}
    rap["passent"], rap["porte_a"] = porte_a(rap["resultats"][TOUT])
    return rap, lignes, valide


def porte_a(resultats_tout):
    """
    La porte A : les CANDIDATES qui passent le critère 1 sur toute la
    période, et si la porte s'ouvre (au moins une). « Comme hier » est un
    repère : même « vu », il ne compte pas.
    """
    passent = [m for m in CANDIDATES if resultats_tout[m]["passe"]]
    return passent, bool(passent)


def egal_etape0(dossier, donnees, annees):
    """Les valeurs-jours notées par l'étape 1 sont-elles celles de l'étape 0 (banc._periode_notee), au bit près ?"""
    R0, *_ = banc._periode_notee(dossier, list(donnees.valeurs), annees)
    dans = (donnees.dates >= annees[0][0]) & (donnees.dates <= annees[-1][1])
    R1 = pd.DataFrame(donnees.r, index=donnees.dates, columns=donnees.valeurs).loc[dans].dropna(how="all")
    return R1.index.equals(R0.index) and np.array_equal(R1.to_numpy(), R0.to_numpy(), equal_nan=True)


# ══════════════════════════════════════════════════════════════════
#  LA MESURE
# ══════════════════════════════════════════════════════════════════

def mesurer(dossier=banc.MARCHE, tickers=None, annees=banc.ANNEES_NOTEES, a_blanc=False, **reglages):
    """
    Toute l'étape 1 sur le cache coupé avant le coffre. Avec `a_blanc`, les
    réponses (pour apprendre comme pour noter) sont remplacées par des
    rendements tirés au hasard : la chaîne tourne en entier, rien de vrai
    n'en sort. Rend (rapport, prévisions à garder).
    """
    tickers, cours, change = charger_cours(dossier, tickers)
    donnees = preparer(tickers, cours, change)
    if not egal_etape0(dossier, donnees, annees):
        raise RuntimeError("les valeurs-jours notées ne sont pas celles de l'étape 0")
    if a_blanc:
        rng = np.random.default_rng(GRAINE_A_BLANC)
        donnees.r = np.where(np.isfinite(donnees.r), rng.normal(0.0005, 0.02, donnees.r.shape), np.nan)
    s, appris = sorties(donnees, annees, **{k: v for k, v in reglages.items() if k.startswith("reglages_")})
    rap, lignes, valide = evaluer(donnees, s, annees, **{k: v for k, v in reglages.items()
                                                          if not k.startswith("reglages_")})
    rap["a_blanc"], rap["apprentissage"] = a_blanc, appris
    previsions = pd.DataFrame({
        "date": np.repeat(donnees.dates[lignes].strftime("%Y-%m-%d"), len(tickers)),
        "valeur": np.tile(tickers, len(lignes)),
        "rendement_eur": donnees.r[lignes].ravel(),
        **{m: s[m][lignes].ravel() for m in METHODES},
        "comptee": valide.ravel().astype(int)})
    return rap, previsions


# ══════════════════════════════════════════════════════════════════
#  L'AFFICHAGE
# ══════════════════════════════════════════════════════════════════

def _ecart(x, dec=2):
    """Un écart en points de pourcentage, signé : « +0,32 », « −0,05 »."""
    return f"{x * 100:+.{dec}f}".replace(".", ",").replace("-", "−")


def _ecart_pts(x, dec=2):
    """Un écart signé avec son unité : « +0,45 point », « −2,10 points » (singulier sous 2, comme banc._pts)."""
    v = round(x * 100, dec)
    return _ecart(x, dec) + (" point" if abs(v) < 2 else " points")


def _ecart_et_borne(f):
    """« +0,32 (−0,05) » : un écart observé, puis sa borne basse (l'écart moins son seuil)."""
    return f"{_ecart(f['ecart'])} ({_ecart(f['ecart'] - f['seuil'])})"


def _avec_borne(res, cle):
    return _ecart_et_borne(res["face"][cle])


def rendu(rap):
    """Le rapport de l'étape 1, en texte (des faits sur le passé : jamais de conseil ni de prévision)."""
    lignes = []
    dire = lignes.append
    tout = rap["resultats"][TOUT]
    reg = rap["reglages"]
    duree = f"{len(rap['annees'])} ans"
    if rap.get("a_blanc"):
        dire("À BLANC : les rendements sont TIRÉS AU HASARD. Rien de ce rapport ne parle des vraies méthodes.")
    dire(f"LE TEST SUR {duree.upper()} — étape 1 du banc des prévisions (le critère 1)")
    dire(f"Données : {rap['valeurs']} valeurs, en euros, dividendes compris (cache lu sans rien écrire), "
         f"coupé au {banc._date(banc.FIN_AVANT_COFFRE)} : le coffre n'est pas lu.")
    dire(f"Séances notées : du {banc._date(rap['debut'])} au {banc._date(rap['fin'])} ({banc._nb(rap['seances'], 0)} "
         f"soirs) ; {banc._nb(rap['valeurs_jours'], 0)} valeurs-jours, les mêmes pour toutes les méthodes "
         f"({banc._nb(rap['retirees'], 0)} retirée{'s' if rap['retirees'] > 1 else ''} faute d'avis).")
    dire("« Hausse » : le rendement en euros du lendemain est strictement positif.")
    dire("c et d apprennent sur toutes les années d'avant (une séance de purge), puis sont notées sur l'année suivante :")
    for nom, (premiere, derniere, n) in rap["apprentissage"].items():
        dire(f"   {nom} : appris du {banc._date(premiere)} au {banc._date(derniere)} ({banc._nb(n, 0)} valeurs-jours)")

    dire(f"\n1. LE CRITÈRE 1, SUR LES {duree.upper()} ({banc._pct(reg['niveau'], 0)}, bootstrap par blocs de "
         f"{reg['bloc']} séances, {banc._nb(reg['tirages'], 0)} tirages)")
    dire("   Écarts en points de bonnes réponses ; entre parenthèses, la borne basse : « vu » si elle est au-dessus de 0.")
    dire(f"   {'':30}{'« baisse »':>11}{'bonnes':>10}{'face au tirage':>19}{'face à « ça monte':>22}{'critère 1':>16}")
    dire(f"   {'':30}{'':>11}{'réponses':>10}{'au sort':>19}{'toujours »':>22}")
    for m in METHODES:
        x = tout[m]
        verdict = ("ne concourt pas" if m not in CANDIDATES else "PASSE" if x["passe"] else "ne passe pas")
        dire(f"   {NOMS[m] + (' (repère)' if m == 'hier' else ''):30}{banc._pct(x['baisse'], 1):>11}"
             f"{banc._pct(x['bonnes'], 2):>10}{_avec_borne(x, 'tirage'):>19}{_avec_borne(x, 'monte'):>22}"
             f"{verdict:>16}")
    dire(f"   « Ça monte toujours » : {banc._pct(tout['a']['monte'], 2)} de bonnes réponses.")
    dire("   Le tirage au sort : la moyenne exacte de tous les tirages qui disent « baisse » aussi souvent que la méthode.")
    dire("   La barre de chaque candidate, et ce que le test pouvait voir :")
    for m in CANDIDATES:
        x = tout[m]
        s_t, s_m = x["face"]["tirage"]["seuil"], x["face"]["monte"]["seuil"]
        source = "du tirage" if x["tirage"] + s_t >= x["monte"] + s_m else "de « ça monte toujours »"
        dire(f"     {NOMS[m]} : il lui fallait plus de {banc._pct(x['barre'], 2)} de bonnes réponses ; elle en a eu "
             f"{banc._pct(x['bonnes'], 2)}. Le test voyait les écarts de plus de {banc._pts(s_t, 2)} face au tirage "
             f"({banc._pct(x['tirage'], 2)}) et de plus de {banc._pts(s_m, 2)} face à « ça monte toujours » "
             f"({banc._pct(x['monte'], 2)}) : la barre vient {source}.")
    dire(f"   Ces seuils portent sur l'écart OBSERVÉ : une méthode dont l'écart vrai vaut le seuil n'est vue qu'une "
         f"fois sur deux ; pour l'être {banc._nb(banc.PUISSANCE * 10, 0)} fois sur 10, il lui faut environ "
         f"{banc._nb(banc.facteur_puissance(), 2)} fois le seuil.")

    dire(f"\n2. ANNÉE PAR ANNÉE (ne décide rien) : l'écart face au tirage au sort ; « vu » si vu face aux deux repères")
    dire(f"   {'':30}" + "".join(f"{nom:>14}" for nom in rap["annees"]))
    for m in METHODES:
        cellules = []
        for nom in rap["annees"]:
            x = rap["resultats"][nom][m]
            cellules.append(f"{_ecart(x['face']['tirage']['ecart']) + (' vu' if x['passe'] else '   '):>14}")
        dire(f"   {NOMS[m]:30}" + "".join(cellules))
    dire("   « ça monte » : " + ", ".join(f"{nom} {banc._pct(rap['resultats'][nom]['a']['monte'], 1)}"
                                         for nom in rap["annees"]) + ".")
    dire("   Sur un an, face au tirage seul, le test dit « vu » à tort environ 1,5 fois sur 100 ; face aux deux "
         "repères, presque jamais (au plus 1,1 fois sur 100, l'année plate 2022-2023 ; mesure du 20/11/2026).")

    dire("\n3. FACE À « COMME HIER » (ne décide rien)")
    h = tout["hier"]["face"]["tirage"]
    if h["vu"]:
        phrase = "demain va nettement plus souvent dans le même sens qu'aujourd'hui (un élan d'un jour)."
    elif h["vu_dessous"]:
        phrase = "demain va nettement plus souvent dans l'autre sens (un rebond d'un jour)."
    else:
        phrase = (f"rien de net à {banc._pct(reg['niveau'], 0)} : le test ne voyait que les écarts de plus de "
                  f"{banc._pts(max(h['seuil'], h['seuil_dessous']), 2)}, dans un sens ou dans l'autre.")
    dire(f"   « Comme hier » face au tirage au sort : {_ecart_pts(h['ecart'])} ; {phrase}")
    dire("   Chaque candidate face au seul sens du jour, dans ses deux versions, chacune comptée face à son propre "
         "tirage au sort (l'écart, puis sa borne basse) ; « nettement mieux » seulement si les deux bornes sont "
         "au-dessus de 0 :")
    seuils_sens = []
    for m in CANDIDATES:
        f = tout[m]["face"]["sens_du_jour"]
        seuils_sens += [f["comme_hier"]["seuil"], f["contraire"]["seuil"]]
        qualif = ("nettement mieux que le sens du jour, dans un sens comme dans l'autre" if f["vu"]
                  else "pas nettement mieux que le sens du jour")
        dire(f"     {NOMS[m]} : face à « comme hier » {_ecart_et_borne(f['comme_hier'])}, face à son contraire "
             f"{_ecart_et_borne(f['contraire'])} : {qualif}.")
    dire(f"   Ce test est prudent : il ne voyait que les écarts de plus de {banc._nb(min(seuils_sens) * 100, 2)} à "
         f"{banc._pts(max(seuils_sens), 2)}. « Pas nettement mieux » ne dit donc pas que l'avance d'une candidate ne "
         "vient que du sens du jour.")

    dire("\n4. LA PORTE A")
    if not rap["porte_a"]:
        dire(f"   Aucune candidate ne passe le critère 1 sur les {duree} : le coffre reste fermé.")
    else:
        dire("   Passe le critère 1 : " + ", ".join(NOMS[m] for m in rap["passent"]) + ".")
        elle, l_ = ("elle", "l'") if len(rap["passent"]) == 1 else ("elles", "les ")
        dire(f"   La porte A s'ouvre pour {elle}, rien de plus : ça ne dit pas que l'argent suivrait (le critère 2), "
             f"et le biais du survivant a pu {l_}aider (voir « À savoir »). Le coffre s'ouvrira une seule fois, "
             "après la construction et la répétition de son ouverture.")
    for m in CANDIDATES:
        x = tout[m]
        if x["face"]["tirage"]["vu"] and not x["face"]["monte"]["vu"]:
            fin = ("dire « hausse » partout fait nettement mieux" if x["face"]["monte"]["vu_dessous"]
                   else "dire « hausse » partout fait à peu près aussi bien")
            dire(f"   {NOMS[m]} est vue face au tirage, pas face à « ça monte toujours » : elle reconnaît mieux que le "
                 f"hasard les lendemains de hausse, mais {fin}. Elle ne passe pas. Le biais du survivant suffit à "
                 "produire ce cas : les 49 valeurs ont été choisies en 2026.")
        elif x["face"]["tirage"]["vu_dessous"]:
            dire(f"   {NOMS[m]} est nettement EN DESSOUS du tirage au sort : son contraire ferait mieux que le hasard, "
                 "mais ce contraire n'est pas une candidate (il n'a pas été choisi avant de regarder). Elle ne passe pas.")

    dire("\n5. À SAVOIR")
    dire("   Le biais du survivant : les 49 valeurs ont été choisies en 2026, toutes ont survécu. « Ça monte "
         "toujours » en est flatté ; une méthode peut l'être aussi, si ce qu'elle fait ou a appris ne vaut que pour "
         "des valeurs qui ont survécu (par exemple, dire « hausse » après une baisse). Le coffre, sur les mêmes 49 "
         "valeurs, ne protège pas de ce biais.")
    dire("   Des faits sur le passé, pas une prévision ni un conseil.")
    return "\n".join(lignes)


# ══════════════════════════════════════════════════════════════════
#  LE RÉSULTAT ÉCRIT (une seule fois)
# ══════════════════════════════════════════════════════════════════

FICHIERS_DU_CODE = ("banc.py", "banc_methodes.py", "stock_engine.py")


def _md5(chemin):
    with open(chemin, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def md5_des_fichiers(dossier, noms=FICHIERS_DU_CODE):
    """L'empreinte md5 de chaque fichier du code du banc."""
    return {nom: _md5(os.path.join(dossier, nom)) for nom in noms}


def empreinte_du_code(dossier=banc.ROOT):
    """Le commit git, « modifie » si un fichier du banc diffère de ce commit (ou si git ne répond pas), et les md5."""
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=dossier, capture_output=True, text=True,
                                check=True).stdout.strip()
        etat = subprocess.run(["git", "status", "--porcelain", "--", *FICHIERS_DU_CODE], cwd=dossier,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit, etat = None, "git ne répond pas"
    return {"commit": commit, "modifie": bool(etat), "md5": md5_des_fichiers(dossier)}


def _en_json(x):
    """Le rapport en types que JSON connaît : dates en AAAA-MM-JJ, nombres non finis en null."""
    if isinstance(x, dict):
        return {str(k): _en_json(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_en_json(v) for v in x]
    if isinstance(x, (pd.Timestamp, datetime.date)):
        return x.strftime("%Y-%m-%d")
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x) if math.isfinite(x) else None
    return x


def empreinte_des_donnees(dossier, tickers):
    """L'empreinte md5 de chaque fichier du cache lu (les valeurs, SPY, l'euro/dollar) : un cache retéléchargé se verrait."""
    noms = [f"{t}_10y.csv" for t in list(tickers) + [banc.REFERENCE, banc.CHANGE]]
    return {nom: _md5(os.path.join(dossier, nom)) for nom in noms}


def versions_des_bibliotheques():
    """Python et les versions des bibliothèques qui font les calculs (les indicateurs viennent de ta)."""
    from importlib import metadata
    versions = {"python": platform.python_version()}
    for nom in ("numpy", "pandas", "scikit-learn", "scipy", "ta"):
        try:
            versions[nom] = metadata.version(nom)
        except metadata.PackageNotFoundError:
            versions[nom] = "inconnue"
    return versions


def ecrire_mesure(rap, previsions, texte, code, dossier_banc=DOSSIER_BANC, maintenant=None, donnees=None,
                  bibliotheques=None):
    """
    Écrit le résultat (etape1.json) et les prévisions, une seule fois. Les
    prévisions sont posées d'abord, le JSON en dernier, par un lien, qui
    échoue s'il existe déjà (FileExistsError) : une coupure en route laisse
    au pire des prévisions sans résultat (la mesure peut alors se refaire :
    rien n'a été affiché), jamais un résultat sans ses prévisions. Le JSON
    garde le md5 de SES prévisions : si deux mesures lancées en même temps
    se croisaient, la seconde, refusée, aurait pu remplacer le fichier des
    prévisions ; le recomptage le verrait. Rien n'est jamais écrit dans
    data/market.
    """
    if os.path.abspath(dossier_banc).startswith(os.path.abspath(banc.MARCHE)):
        raise RuntimeError("le banc n'écrit jamais dans data/market")
    os.makedirs(dossier_banc, exist_ok=True)
    chemin = os.path.join(dossier_banc, NOM_MESURE)
    if os.path.exists(chemin):
        raise FileExistsError(f"{chemin} existe déjà : une mesure ne s'écrase pas")
    maintenant = maintenant or datetime.datetime.now(datetime.timezone.utc).astimezone()
    contenu = {"date_mesure": maintenant.isoformat(timespec="seconds"), "code": code,
               "donnees": donnees or {}, "bibliotheques": bibliotheques or {},
               "choix": {"tirage": "la moyenne exacte de tous les tirages (choix de Mathis, 20/11/2026)",
                         "ordre": "le critère 1 d'abord (choix de Mathis, 20/11/2026)"},
               "resultat": _en_json(rap), "rapport": texte}
    fd, temp = tempfile.mkstemp(dir=dossier_banc, prefix=".etape1-", suffix=".tmp")
    fd2, temp_prev = tempfile.mkstemp(dir=dossier_banc, prefix=".previsions-", suffix=".tmp")
    os.close(fd2)
    try:
        previsions.to_csv(temp_prev, index=False, compression="gzip")
        contenu["previsions"] = {"fichier": NOM_PREVISIONS, "md5": _md5(temp_prev), "lignes": len(previsions)}
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(contenu, f, ensure_ascii=False, indent=1, allow_nan=False)
        os.replace(temp_prev, os.path.join(dossier_banc, NOM_PREVISIONS))      # les prévisions d'abord…
        os.link(temp, chemin)                       # refusé si le résultat existe déjà
    finally:
        os.remove(temp)
        if os.path.exists(temp_prev):
            os.remove(temp_prev)
    return chemin


def lancer_la_mesure(a_blanc=False, dossier=banc.MARCHE, dossier_banc=DOSSIER_BANC, tickers=None,
                     annees=banc.ANNEES_NOTEES, racine_du_code=banc.ROOT):
    """
    `python3 banc.py --mesurer` : la mesure, une seule fois, sur du code
    commité. Le rapport ne s'affiche qu'une fois écrit, relu dans le fichier :
    si l'écriture échoue, rien n'a été vu, et la mesure peut se relancer. Si
    elle a déjà été faite, son rapport est réaffiché tel quel.
    `--a-blanc` : des rendements tirés au hasard, rien n'est écrit.
    """
    chemin = os.path.join(dossier_banc, NOM_MESURE)
    if not a_blanc and os.path.exists(chemin):
        with open(chemin, encoding="utf-8") as f:
            deja = json.load(f)
        print(f"La mesure de l'étape 1 a déjà été faite le {deja['date_mesure']} (commit {deja['code']['commit']}) : "
              "elle ne s'écrase pas. Voici son rapport.\n")
        print(deja["rapport"])
        return deja
    code = empreinte_du_code(racine_du_code)
    if not a_blanc and code["modifie"]:
        raise SystemExit("Rien n'est calculé : banc.py, banc_methodes.py ou stock_engine.py n'est pas commité tel "
                         "quel. La mesure doit tourner sur du code enregistré (git commit d'abord).")
    rap, previsions = mesurer(dossier, tickers, annees, a_blanc=a_blanc)
    texte = rendu(rap)
    if a_blanc:
        print(texte)
        return rap
    lues = list(engine.UNIVERSE_TICKERS if tickers is None else tickers)
    ecrire_mesure(rap, previsions, texte, code, dossier_banc, donnees=empreinte_des_donnees(dossier, lues),
                  bibliotheques=versions_des_bibliotheques())
    with open(chemin, encoding="utf-8") as f:
        print(json.load(f)["rapport"])
    print(f"\nÉcrit, une fois pour toutes : {chemin} et {os.path.join(dossier_banc, NOM_PREVISIONS)}.")
    return rap


# ══════════════════════════════════════════════════════════════════
#  LES VÉRIFICATIONS (des cours inventés, jamais le vrai cache)
# ══════════════════════════════════════════════════════════════════

FAUX = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")
ANNEES_FAUSSES = ((pd.Timestamp("2023-11-01"), pd.Timestamp("2024-10-31")),
                  (pd.Timestamp("2024-11-01"), pd.Timestamp("2025-10-31")))


def _faux_cache(dossier, graine=1, signal=0.5, debut="2020-06-01", fin="2025-12-31", tickers=FAUX,
                trou=("FFF", "2024-06-12")):
    """
    Un faux cache au format de data/market (ouverture, plus haut, plus bas,
    clôture, volume), dans un dossier jetable (jamais data/market).
    `signal` : la probabilité que le rendement de demain ait le même sens
    que celui d'aujourd'hui (0,5 : aucun lien, des marches au hasard).
    `trou` : une valeur à qui il manque une séance (comme un jour de cotation suspendue).
    """
    if os.path.abspath(dossier) == os.path.abspath(banc.MARCHE):
        raise RuntimeError("les vérifications n'écrivent jamais dans data/market")
    rng = np.random.default_rng(graine)
    jours = pd.bdate_range(debut, fin)
    n = len(jours)
    idx = pd.Index(jours, name="Date")
    for t in list(tickers) + [banc.REFERENCE]:
        taille, u = np.abs(rng.normal(0, 0.02, n)), rng.random(n)
        signes = np.ones(n)
        for i in range(1, n):
            signes[i] = signes[i - 1] if u[i] < signal else -signes[i - 1]
        close = 50 * np.cumprod(1 + signes * taille + 0.0003)
        ouv = np.concatenate([[close[0]], close[:-1]]) * (1 + rng.normal(0, 0.003, n))
        haut = np.maximum(ouv, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
        bas = np.minimum(ouv, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
        volume = np.round(rng.lognormal(14, 0.4, n))
        df = pd.DataFrame({"Close": close, "High": haut, "Low": bas, "Open": ouv, "Volume": volume}, index=idx)
        if trou is not None and t == trou[0]:
            df = df.drop(pd.Timestamp(trou[1]))
        df.to_csv(os.path.join(dossier, f"{t}_10y.csv"))
    fx = 1.10 * np.exp(np.cumsum(rng.normal(0, 0.003, n)))
    pd.DataFrame({"Close": fx * 1.0005, "High": fx * 1.002, "Low": fx * 0.998, "Open": fx, "Volume": 0},
                 index=idx).to_csv(os.path.join(dossier, f"{banc.CHANGE}_10y.csv"))
    return jours


def _copier_cache(source, cible, changer=None):
    """Recopie un faux cache dans `cible` (jamais data/market), chaque fichier passé par changer(symbole, df) → df."""
    if os.path.abspath(cible) == os.path.abspath(banc.MARCHE):
        raise RuntimeError("les vérifications n'écrivent jamais dans data/market")
    os.makedirs(cible, exist_ok=True)
    for nom in sorted(os.listdir(source)):
        df = pd.read_csv(os.path.join(source, nom), index_col=0, parse_dates=True)
        if changer is not None:
            df = changer(nom[:-len("_10y.csv")], df.copy())
        df.to_csv(os.path.join(cible, nom))


def _chaine(dossier, fin=banc.FIN_AVANT_COFFRE, annees=ANNEES_FAUSSES, echelle=None):
    """
    Le chargeur, les mesures et les sorties des cinq méthodes, sur un faux
    cache. `echelle` {valeur: (k, l)} : ses cours lus multipliés par k et ses
    volumes par l, en mémoire (le lecteur de pandas arrondit parfois le 17e
    chiffre d'un fichier : un aller-retour par le disque ne serait pas exact).
    """
    tickers, cours, change = charger_cours(dossier, FAUX, fin)
    if echelle is not None:
        cours = {t: df.assign(**{c: df[c] * echelle[t][0] for c in ("Open", "High", "Low", "Close")},
                              Volume=df["Volume"] * echelle[t][1])
                 for t, df in cours.items()}
    donnees = preparer(tickers, cours, change)
    s, appris = sorties(donnees, annees)
    return donnees, s, appris


def _memes_jusqu_a(c1, c2, jour, methodes=METHODES):
    """Les sorties de chaque méthode identiques AU BIT PRÈS sur toutes les séances jusqu'à `jour` compris."""
    (d1, s1, _), (d2, s2, _) = c1, c2
    i1, i2 = d1.dates <= jour, d2.dates <= jour
    if not d1.dates[i1].equals(d2.dates[i2]):
        return False
    return all(np.array_equal(s1[m][i1], s2[m][i2], equal_nan=True) for m in methodes)


def verifier():
    cas = []

    def ok(nom, condition):
        cas.append(bool(condition))
        print(("✓ " if condition else "✗ ") + nom)

    def proche(a, b, tol=1e-12):
        return a is not None and b is not None and abs(a - b) <= tol

    print("— Les réglages —")
    ok("les 14 mesures du plan, dans l'ordre, toutes des rapports (rendements 1, 5, 21 ; volatilité 20 ; RSI 14 ; "
       "MACD/ATR ; écart à la moyenne 20 en ATR ; moyennes 20/50 ; volume/moyenne 20 ; momentum 12-1 ; force ; "
       "SPY 1 et 5 ; euro/dollar 1)",
       MESURES == ("r1", "r5", "r21", "vol20", "rsi14", "macd_atr", "ecart20_atr", "sma20_50", "volume20",
                   "mom12_1", "force", "spy1", "spy5", "eurusd1"))
    ok("c : C = 1, max_iter = 1000 ; d : 200 itérations, pas 0,05, 15 feuilles, 500 lignes par feuille, "
       "L2 = 1, sans arrêt précoce, graine 20261116 (réglages figés le 16/11/2026)",
       REGLAGES_C == {"C": 1.0, "max_iter": 1000}
       and REGLAGES_D == {"max_iter": 200, "learning_rate": 0.05, "max_leaf_nodes": 15, "min_samples_leaf": 500,
                          "l2_regularization": 1.0, "early_stopping": False, "random_state": 20261116})
    ok("une séance de purge ; « hausse » au-dessus de 0 (a, b, « comme hier ») et de 50 % (c, d) ; 4 candidates, "
       "« comme hier » ne concourt pas ; la graine des blocs de l'étape 1 : 20261120",
       PURGE == 1 and SEUILS == {"a": 0.0, "b": 0.0, "c": 0.5, "d": 0.5, "hier": 0.0}
       and tuple(CANDIDATES) == ("a", "b", "c", "d") and "hier" not in CANDIDATES and GRAINE == 20261120
       and SORTIE == {"a": "force", "b": "mom12_1", "hier": "r1"})
    texte_du_module = open(os.path.abspath(__file__), encoding="utf-8").read().lower()
    ok("le module ne nomme pas le courtier (dépôt public)",
       ("trade" + " republic") not in texte_du_module and ("147," + "46") not in texte_du_module)

    print("\n— Les avis —")
    ok("a, b, « comme hier » : « hausse » au-dessus de 0 strictement, 0 = « baisse », vide = pas d'avis",
       np.array_equal(avis("a", [0.0, 0.1, -0.1, np.nan]), [0.0, 1.0, 0.0, np.nan], equal_nan=True)
       and np.array_equal(avis("hier", [0.0, 1e-9]), [0.0, 1.0]))
    ok("c, d : « hausse » au-dessus de 50 % strictement (50 % pile = « baisse »)",
       np.array_equal(avis("c", [0.5, 0.5000001, 0.4, np.nan]), [0.0, 1.0, 0.0, np.nan], equal_nan=True)
       and np.array_equal(avis("d", [0.51]), [1.0]))

    ok("une réponse nulle est une « baisse », pour noter comme pour apprendre ; sans lendemain, elle ne compte pas",
       np.array_equal(reponses([0.0, 0.01, -0.01, np.nan]), [0.0, 1.0, 0.0, 0.0])
       and np.array_equal(lignes_d_apprentissage(np.zeros((3, 1, 2)), np.array([[0.0], [0.02], [np.nan]]),
                                                 np.arange(3))[1], [0, 1]))

    print("\n— La réponse et les mesures, à la main —")
    jours = pd.bdate_range("2024-01-01", periods=64)
    clot = np.full(64, 100.0)
    clot[61], clot[62], clot[63] = 125.0, 90.0, 90.0
    x = pd.DataFrame({"Open": clot, "High": clot * 1.01, "Low": clot * 0.99, "Close": clot, "Volume": 1000.0},
                     index=jours)
    spy = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1000.0}, index=jours)
    fx = pd.Series(2.0, index=pd.bdate_range("2024-01-01", periods=65))
    fx.iloc[62], fx.iloc[63], fx.iloc[64] = 2.5, 1.5, 1.5
    d_main = preparer(["X"], {"X": x, banc.REFERENCE: spy}, fx)
    i_r1 = MESURES.index("r1")
    ok("la réponse du soir J va de J à J+1, en euros : 100 $ ÷ 2,0 = 50 € puis 125 $ ÷ 2,5 = 50 € → 0 %, une "
       "« baisse » alors que le dollar a pris +25 %",
       d_main.r[60, 0] == 0.0 and avis("a", d_main.r[60:61, 0])[0] == 0.0)
    ok("… puis 50 € → 90 $ ÷ 1,5 = 60 € : +20 % en euros quand le dollar perd 28 %, puis 0 %",
       proche(d_main.r[61, 0], 0.2) and d_main.r[62, 0] == 0.0 and math.isnan(d_main.r[63, 0]))
    ok("la mesure « rendement sur 1 séance » du soir J va de J−1 à J, en dollars : +25 %, −28 %, 0",
       proche(d_main.X[61, 0, i_r1], 0.25) and proche(d_main.X[62, 0, i_r1], -0.28) and d_main.X[63, 0, i_r1] == 0.0)
    ok("SPY immobile : ses rendements sur 1 et 5 séances valent 0",
       d_main.X[63, 0, MESURES.index("spy1")] == 0.0 and d_main.X[63, 0, MESURES.index("spy5")] == 0.0)
    ok("un volume constant : volume ÷ sa moyenne 20 séances = 1", d_main.X[40, 0, MESURES.index("volume20")] == 1.0)
    fxj = pd.Series([1.00, 1.10, 1.32], index=pd.DatetimeIndex(["2024-01-08", "2024-01-09", "2024-01-11"]))
    soir = connu_le_soir(change_sur_un_jour(fxj),
                         pd.DatetimeIndex(["2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12", "2024-01-17"]))
    ok("l'euro/dollar connu le soir : mardi +10 % ; mercredi, sans ligne, encore +10 % (celle de mardi, jamais celle "
       "de jeudi) ; jeudi et vendredi +20 % ; vide à 6 jours de la dernière ligne",
       proche(soir.iloc[0], 0.10) and proche(soir.iloc[1], 0.10) and proche(soir.iloc[2], 0.20)
       and proche(soir.iloc[3], 0.20) and math.isnan(soir.iloc[4]))
    jours_p = pd.bdate_range("2024-10-28", "2024-11-08")
    ok("la purge : pour une année qui commence le vendredi 01/11/2024, c et d apprennent jusqu'au mercredi 30/10 ; "
       "le jeudi 31/10 est retiré (sans purge, il serait gardé)",
       list(jours_p[seances_d_apprentissage(jours_p, pd.Timestamp("2024-11-01"))].strftime("%d/%m"))
       == ["28/10", "29/10", "30/10"]
       and len(seances_d_apprentissage(jours_p, pd.Timestamp("2024-11-01"), purge=0)) == 4)
    geo = pd.DataFrame({"Close": 100 * 1.01 ** np.arange(300.0)}, index=pd.bdate_range("2019-01-01", periods=300))
    geo = geo.assign(Open=geo["Close"], High=geo["Close"] * 1.005, Low=geo["Close"] * 0.995, Volume=500.0)
    m_geo = mesures_d_une_valeur(geo, geo["Close"], pd.Series(dtype=float, index=pd.DatetimeIndex([])))
    ok("+1 % par séance : rendements sur 1, 5 et 21 séances de 1 %, 1,01⁵ − 1 et 1,01²¹ − 1 ; momentum 12-1 = "
       "1,01²³¹ − 1",
       proche(m_geo["r1"].iloc[-1], 0.01, 1e-12) and proche(m_geo["r5"].iloc[-1], 1.01 ** 5 - 1, 1e-12)
       and proche(m_geo["r21"].iloc[-1], 1.01 ** 21 - 1, 1e-12)
       and proche(m_geo["mom12_1"].iloc[-1], 1.01 ** 231 - 1, 1e-9))
    # Les 14 mesures recalculées à part, depuis les cours bruts d'une valeur et de SPY qui bougent
    # chacun à leur façon (ouvertures, plus hauts, plus bas et clôtures tous différents) : un
    # indicateur pris pour un autre (RSI / ADX, ligne MACD / histogramme, moyenne 20 / 50…), SPY
    # pris sur ses ouvertures ou remplacé par la valeur elle-même ne passent pas.
    import ta
    rng_m = np.random.default_rng(14)
    jours_m = pd.bdate_range("2021-01-04", periods=330)

    def cours_inventes(depart):
        c = depart * np.cumprod(1 + rng_m.normal(0.0004, 0.015, len(jours_m)))
        o = c * (1 + rng_m.normal(0, 0.004, len(jours_m)))
        return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * (1 + np.abs(rng_m.normal(0, 0.006, len(c)))),
                             "Low": np.minimum(o, c) * (1 - np.abs(rng_m.normal(0, 0.006, len(c)))), "Close": c,
                             "Volume": np.round(rng_m.lognormal(13, 0.5, len(c)))}, index=jours_m)

    x_m, spy_m = cours_inventes(80.0), cours_inventes(300.0)
    fx_m = pd.Series(1.1 * np.exp(np.cumsum(rng_m.normal(0, 0.003, 331))), index=pd.bdate_range("2021-01-04", periods=331))
    d_m = preparer(["X"], {"X": x_m, banc.REFERENCE: spy_m}, fx_m)
    c_m, v_m, s_m = x_m["Close"], x_m["Volume"], spy_m["Close"]
    atr_m = ta.volatility.average_true_range(x_m["High"], x_m["Low"], c_m, window=14)
    ema = {k: c_m.ewm(span=k, min_periods=k, adjust=False).mean() for k in (12, 26)}
    attendu_m = {
        "r1": c_m / c_m.shift(1) - 1, "r5": c_m / c_m.shift(5) - 1, "r21": c_m / c_m.shift(21) - 1,
        "vol20": c_m.pct_change().rolling(20).std(), "rsi14": ta.momentum.rsi(c_m, window=14),
        "macd_atr": (ema[12] - ema[26]) / atr_m, "ecart20_atr": (c_m - c_m.rolling(20).mean()) / atr_m,
        "sma20_50": c_m.rolling(20).mean() / c_m.rolling(50).mean() - 1, "volume20": v_m / v_m.rolling(20).mean(),
        "mom12_1": c_m.shift(21) / c_m.shift(252) - 1,
        "force": pd.Series({x["date"]: x["strength"] for x in engine.analyze_series(x_m)}, dtype=float),
        "spy1": s_m / s_m.shift(1) - 1, "spy5": s_m / s_m.shift(5) - 1,
        "eurusd1": (fx_m / fx_m.shift(1) - 1).reindex(jours_m)}

    def mesure_differente(nom):
        faite = d_m.X[:, 0, MESURES.index(nom)]
        voulue = attendu_m[nom].reindex(d_m.dates).to_numpy(float)
        ici = np.isfinite(voulue)
        return not (np.array_equal(np.isfinite(faite), ici) and ici.sum() >= 60
                    and np.allclose(faite[ici], voulue[ici], rtol=1e-9, atol=1e-12))

    differentes = [nom for nom in MESURES if mesure_differente(nom)]
    ok("les 14 mesures recalculées à part depuis les cours bruts (une valeur et SPY qui bougent chacun à leur "
       "façon) : rendements 1, 5, 21 ; écart-type des 20 derniers rendements ; RSI 14 ; ligne MACD (EMA 12 − EMA 26) "
       "÷ ATR 14 ; (cours − moyenne 20) ÷ ATR ; moyenne 20 ÷ moyenne 50 − 1 ; volume ÷ sa moyenne 20 ; momentum "
       "12-1 ; force ; clôtures de SPY sur 1 et 5 séances ; euro/dollar" + (f" — DIFFÉRENTES : {', '.join(differentes)}"
                                                                           if differentes else ""),
       not differentes)

    print("\n— Le critère 1, à la main —")
    un = np.ones((1, 2), dtype=np.float32)      # un seul « tirage » : l'échantillon lui-même (seuil 0)
    r1_, t1_ = critere_1(np.array([[1.0, 0.0], [1.0, 1.0]]), np.array([[1.0, 0.0], [0.0, 1.0]]), np.ones((2, 2), bool),
                         un)
    ok("3 bonnes réponses sur 4, « baisse » 1 fois sur 4, 2 hausses sur 4 : le tirage en attend 2 (3/4 × 1/2 + "
       "1/4 × 1/2), « ça monte » en a 2 → +25 et +25 points, la barre est à 50 %",
       proche(r1_["bonnes"], 0.75) and proche(r1_["tirage"], 0.5) and proche(r1_["monte"], 0.5)
       and proche(r1_["face"]["tirage"]["ecart"], 0.25) and proche(r1_["face"]["monte"]["ecart"], 0.25)
       and proche(r1_["baisse"], 0.25) and proche(r1_["barre"], 0.5) and r1_["passe"] and r1_["n"] == 4
       and proche(float(t1_[0]), 0.25) and "sens_du_jour" not in r1_["face"])
    r2_, _ = critere_1(np.array([[0.0, 1.0], [0.0, 1.0]]), np.array([[1.0, 1.0], [0.0, 1.0]]), np.ones((2, 2), bool),
                       un)
    ok("vue face au tirage (+25) mais pas face à « ça monte » (3 sur 4 pour lui aussi, 0 point) : ne passe pas",
       r2_["face"]["tirage"]["vu"] and not r2_["face"]["monte"]["vu"] and not r2_["passe"])
    seul = np.ones((1, 1), dtype=np.float32)
    args3 = (np.array([[1.0, 0.0, 1.0]]), np.array([[1.0, 0.0, 1.0]]), np.array([[True, True, False]]), seul)
    r3_, _ = critere_1(*args3, sens_du_jour=(-0.3, np.array([-0.3])))
    r3b, _ = critere_1(*args3, sens_du_jour=(0.6, np.array([0.6])))
    ok("une valeur-jour non comptée ne compte pas (2 sur 2, +50 points face au tirage) ; face au sens du jour dans "
       "ses deux versions (« comme hier » à −30 points, son contraire à +30) : +80 et +20 points, la moins bonne "
       "(+20) est gardée, vue face aux deux ; « comme hier » à +60 : −10 points, pas vue, et jamais « moins bien »",
       r3_["n"] == 2 and proche(r3_["bonnes"], 1.0) and proche(r3_["face"]["tirage"]["ecart"], 0.5)
       and proche(r3_["face"]["sens_du_jour"]["comme_hier"]["ecart"], 0.8)
       and proche(r3_["face"]["sens_du_jour"]["contraire"]["ecart"], 0.2)
       and proche(r3_["face"]["sens_du_jour"]["ecart"], 0.2) and proche(r3_["face"]["sens_du_jour"]["borne"], 0.2)
       and r3_["face"]["sens_du_jour"]["vu"]
       and proche(r3b["face"]["sens_du_jour"]["ecart"], -0.1) and not r3b["face"]["sens_du_jour"]["vu"]
       and "vu_dessous" not in r3b["face"]["sens_du_jour"])
    rebond_seul = face_au_sens_du_jour(0.3, np.array([0.3, 0.25, 0.35]), -0.3, np.array([-0.3, -0.25, -0.35]), 0.75)
    ok("une candidate qui ne fait que le contraire de « comme hier » (son avance = celle du rebond) n'est pas "
       "« nettement mieux que le sens du jour », même si elle bat « comme hier » de loin",
       rebond_seul["comme_hier"]["vu"] and not rebond_seul["contraire"]["vu"] and not rebond_seul["vu"]
       and rebond_seul["ecart"] == 0.0)
    deux_seuils = face_au_sens_du_jour(0.5, np.array([0.5, 0.3, 0.7]), 0.1, np.array([0.1, 0.0, 0.2]), 0.75)
    ok("la borne face au sens du jour : la plus basse des deux bornes, chacune l'écart MOINS son seuil (face à « comme "
       "hier » : 0,40 − 0,05 = 0,35 ; face au contraire : 0,60 − 0,15 = 0,45), pas le plus petit écart (0,40)",
       proche(deux_seuils["ecart"], 0.4) and proche(deux_seuils["borne"], 0.35)
       and proche(deux_seuils["comme_hier"]["seuil"], 0.05) and proche(deux_seuils["contraire"]["seuil"], 0.15)
       and deux_seuils["vu"])
    W = np.array([[1, 1, 1, 1], [2, 0, 1, 1], [0, 2, 2, 0], [1, 1, 0, 2]], dtype=np.float32)
    m4 = np.array([[1.0, 0.0], [0.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    y4 = np.array([[1.0, 1.0], [0.0, 1.0], [1.0, 0.0], [1.0, 1.0]])
    r4_, _ = critere_1(m4, y4, np.ones_like(m4, bool), W, niveau=0.75)
    (o_t, o_m), (t_t, t_m) = banc.ecarts(m4, y4, np.ones_like(m4, bool), W)
    s_t, s_m = banc.seuil_critique(o_t, t_t, 0.75), banc.seuil_critique(o_m, t_m, 0.75)
    ok("la barre = le plus haut de (tirage + son seuil) et (« ça monte » + son seuil), sur les tirages donnés",
       proche(r4_["barre"], max(r4_["tirage"] + s_t, r4_["monte"] + s_m))
       and proche(r4_["face"]["tirage"]["seuil"], s_t) and proche(r4_["face"]["monte"]["seuil"], s_m))
    h4 = np.array([[1.0, 1.0], [0.0, 1.0], [0.0, 0.0], [1.0, 0.0]])
    r4h, t4h = critere_1(h4, y4, np.ones_like(m4, bool), W, niveau=0.75)
    d_h4 = r4h["face"]["tirage"]["ecart"]
    r4s, _ = critere_1(m4, y4, np.ones_like(m4, bool), W, sens_du_jour=(d_h4, t4h), niveau=0.75)
    deux = []
    for signe in (1, -1):                           # « comme hier », puis son contraire
        g = o_t - signe * d_h4
        deux.append((g, float(np.quantile(g - (np.asarray(t_t) - signe * np.asarray(t4h)), 0.75))))
    f4 = r4s["face"]["sens_du_jour"]
    ok("face au sens du jour, tirage par tirage (les mêmes blocs), recalculé à part : l'écart face à « comme hier » "
       "et face à son contraire, chacun avec son seuil ; la moins bonne des deux et sa borne ; « vu » face aux deux",
       proche(f4["comme_hier"]["ecart"], deux[0][0]) and proche(f4["comme_hier"]["seuil"], deux[0][1])
       and proche(f4["contraire"]["ecart"], deux[1][0]) and proche(f4["contraire"]["seuil"], deux[1][1])
       and proche(f4["ecart"], min(g for g, _ in deux)) and proche(f4["borne"], min(g - s for g, s in deux))
       and f4["vu"] == all(g > s for g, s in deux))
    r5_, _ = critere_1(np.array([[0.0, 1.0], [1.0, 0.0]]), np.array([[1.0, 0.0], [0.0, 1.0]]), np.ones((2, 2), bool),
                       un)
    ok("toujours à côté : −50 points face au tirage, nettement EN DESSOUS (vu_dessous), jamais « vu »",
       proche(r5_["face"]["tirage"]["ecart"], -0.5) and r5_["face"]["tirage"]["vu_dessous"]
       and not r5_["face"]["tirage"]["vu"])

    print("\n— Sur des cours inventés : la chaîne entière —")
    with tempfile.TemporaryDirectory() as racine:
        base = os.path.join(racine, "base")
        os.makedirs(base)
        _faux_cache(base, graine=5, signal=0.5)
        c0 = _chaine(base)
        d0, s0, appris0 = c0
        ok("les valeurs-jours notées sont celles de l'étape 0 (banc._periode_notee), au bit près",
           egal_etape0(base, d0, ANNEES_FAUSSES))
        rap0, lignes0, valide0 = evaluer(d0, s0, ANNEES_FAUSSES)
        R0, *_ = banc._periode_notee(base, list(FAUX), ANNEES_FAUSSES)
        ok(f"{banc._nb(rap0['valeurs_jours'], 0)} valeurs-jours comptées, aucune retirée ; les 2 années notées ont "
           "chacune leur apprentissage (de la première séance complète à la veille de la purge)",
           rap0["valeurs_jours"] == int(np.isfinite(R0.to_numpy()).sum()) and rap0["retirees"] == 0
           and list(appris0) == ["2023-2024", "2024-2025"]
           and appris0["2023-2024"][1] == pd.Timestamp("2023-10-30") and appris0["2024-2025"][1] == pd.Timestamp("2024-10-30"))
        # c et d recalculés à part, chaque année notée : mêmes lignes (TOUTES les séances d'avant, moins la
        # purge), même centrage, mêmes probabilités.
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler

        def recalcule(a, b):
            appr = np.flatnonzero(d0.dates < a)[:-1]
            test = np.flatnonzero((d0.dates >= a) & (d0.dates <= b))
            Xa, ra = d0.X[appr].reshape(-1, len(MESURES)), d0.r[appr].ravel()
            garde = np.isfinite(Xa).all(1) & np.isfinite(ra)
            Xa, ya = Xa[garde], (ra[garde] > 0).astype(int)
            Xt = d0.X[test].reshape(-1, len(MESURES))
            okt = np.isfinite(Xt).all(1)
            echelle = StandardScaler().fit(Xa)
            with _sans_faux_avertissements():
                pc = LogisticRegression(C=1.0, max_iter=1000).fit(echelle.transform(Xa), ya).predict_proba(
                    echelle.transform(Xt[okt]))[:, 1]
            pdd = HistGradientBoostingClassifier(**REGLAGES_D).fit(Xa, ya).predict_proba(Xt[okt])[:, 1]
            c_t, d_t = s0["c"][test].ravel(), s0["d"][test].ravel()
            juste = (np.allclose(c_t[okt], pc, rtol=0, atol=1e-15) and np.array_equal(d_t[okt], pdd)
                     and np.isnan(c_t[~okt]).all() and np.isnan(d_t[~okt]).all())
            premiere = d0.dates[appr][garde.reshape(len(appr), -1).any(axis=1)][0]
            return juste, len(ya), premiere, int((~okt).sum()), test

        a1, b1 = ANNEES_FAUSSES[0]
        juste1, n1, premiere1, vides1, test = recalcule(a1, b1)
        juste2, n2, premiere2, vides2, _ = recalcule(*ANNEES_FAUSSES[1])
        ok(f"c et d recalculés à part, chaque année, sur toutes les séances d'avant moins la purge ({banc._nb(n1, 0)} "
           f"puis {banc._nb(n2, 0)} lignes, depuis la première séance complète), centrées et réduites sur elles "
           "seules ; les probabilités de predict_proba (c, à 10⁻¹⁵ près : son produit de matrices additionne dans "
           "un autre ordre ; d au bit près), vides là où une mesure manque",
           juste1 and juste2 and appris0["2023-2024"][2] == n1 and appris0["2024-2025"][2] == n2 and n2 > n1
           and appris0["2023-2024"][0] == premiere1 and appris0["2024-2025"][0] == premiere2 == premiere1
           and vides1 == 1)
        i_f = MESURES.index("force")
        ccc = charger_cours(base, FAUX)[1]["CCC"]
        force_ccc = pd.Series({x["date"]: x["strength"] for x in engine.analyze_series(ccc)}).reindex(d0.dates)
        mom_ccc = engine.compute_indicators(ccc)["mom_12_1"].reindex(d0.dates)
        j_c = list(FAUX).index("CCC")
        ok("a dit la force du score technique (analyze_series), b le momentum 12-1 (compute_indicators), « comme "
           "hier » le rendement du jour : recalculés à part pour une valeur",
           np.array_equal(s0["a"][:, j_c], force_ccc.to_numpy(float), equal_nan=True)
           and np.array_equal(s0["b"][:, j_c], mom_ccc.to_numpy(float), equal_nan=True)
           and np.array_equal(s0["hier"], d0.X[:, :, i_r1], equal_nan=True)
           and np.array_equal(s0["a"], d0.X[:, :, i_f], equal_nan=True))
        ok("la dernière séance notée est le 29/10/2025 : le change du 03/11 est déjà dans le coffre ; la valeur à "
           "qui il manque une séance n'a pas de réponse ce soir-là ni la veille",
           rap0["fin"] == pd.Timestamp("2025-10-29")
           and np.isnan(d0.r[d0.dates.get_loc(pd.Timestamp("2024-06-11")), list(FAUX).index("FFF")])
           and np.isnan(d0.r[d0.dates.get_loc(pd.Timestamp("2024-06-12")), list(FAUX).index("FFF")]))
        s_trou = {m: v.copy() for m, v in s0.items()}
        s_trou["a"][d0.dates.get_loc(pd.Timestamp("2024-01-10")), 0] = np.nan
        s_trou["d"][d0.dates.get_loc(pd.Timestamp("2025-02-12")), 1] = np.nan
        rap_trou, _, _ = evaluer(d0, s_trou, ANNEES_FAUSSES)
        ok("une valeur-jour où une méthode n'a pas d'avis est retirée pour TOUTES, et comptée comme telle (2 sur "
           f"{banc._nb(rap0['valeurs_jours'], 0)})",
           rap_trou["retirees"] == 2 and rap_trou["valeurs_jours"] == rap0["valeurs_jours"] - 2
           and all(rap_trou["resultats"][TOUT][m]["n"] == rap0["valeurs_jours"] - 2 for m in METHODES))
        d_zero = copy.copy(d0)
        d_zero.r = d0.r.copy()
        i_z = d0.dates.get_loc(pd.Timestamp("2024-03-05"))
        j_z = int(np.flatnonzero(d0.r[i_z] > 0)[0])
        d_zero.r[i_z, j_z] = 0.0
        rap_zero, _, _ = evaluer(d_zero, s0, ANNEES_FAUSSES)
        n_z = rap0["valeurs_jours"]
        ok("un rendement en euros exactement nul est noté comme une « baisse » : « ça monte » perd une hausse sur "
           f"{banc._nb(n_z, 0)}",
           proche(rap_zero["resultats"][TOUT]["a"]["monte"], rap0["resultats"][TOUT]["a"]["monte"] - 1 / n_z, 1e-12))
        ok("c et d n'ont pas d'avis hors des années notées ; a, b et « comme hier » en ont partout où leurs mesures existent",
           np.isnan(s0["c"][d0.dates < a1]).all() and np.isnan(s0["d"][d0.dates < a1]).all()
           and np.array_equal(np.isfinite(s0["a"][test]), np.isfinite(d0.X[test][..., i_f]))
           and np.isfinite(s0["b"][test]).sum() == s0["b"][test].size - 1)
        i_y = 2
        p_an = (d0.dates[lignes0] >= ANNEES_FAUSSES[1][0]) & (d0.dates[lignes0] <= ANNEES_FAUSSES[1][1])
        r_an = d0.r[lignes0][p_an]
        y_an = (np.nan_to_num(r_an) > 0).astype(float)
        w_an = banc.poids_blocs(int(p_an.sum()), banc.BLOC, banc.TIRAGES, GRAINE + i_y)
        hier_an, t_hier_an = critere_1(np.nan_to_num(avis("hier", s0["hier"][lignes0][p_an])), y_an, valide0[p_an], w_an)
        attendu, _ = critere_1(np.nan_to_num(avis("c", s0["c"][lignes0][p_an])), y_an, valide0[p_an], w_an,
                               sens_du_jour=(hier_an["face"]["tirage"]["ecart"], t_hier_an))
        ok("assemblage : les résultats de c et de « comme hier » en 2024-2025, recalculés avec les tirages des blocs de "
           "CETTE année (les mêmes pour les deux)",
           banc._egaux(rap0["resultats"]["2024-2025"]["c"], attendu)
           and banc._egaux(rap0["resultats"]["2024-2025"]["hier"], hier_an))
        ok("le placebo : sur des cours au hasard, aucune méthode n'est « vue » face aux deux repères ; la porte A reste "
           "fermée", not rap0["porte_a"] and not any(rap0["resultats"][TOUT][m]["passe"] for m in METHODES))

        # Aucun regard vers le futur : au milieu d'une année, puis à la première séance d'une année notée.
        for jour, ou in ((pd.Timestamp("2024-03-15"), "au milieu d'une année"),
                         (pd.Timestamp("2024-11-01"), "à la première séance d'une année notée")):
            dossier = os.path.join(racine, f"coupe-{jour:%Y%m%d}")
            _copier_cache(base, dossier)
            ok(f"couper toutes les données après le {jour:%d/%m/%Y} ({ou}) ne change aucune sortie d'avant, au bit près",
               _memes_jusqu_a(c0, _chaine(dossier, fin=jour), jour))
            rng = np.random.default_rng(int(jour.strftime("%Y%m%d")))

            def changer_apres(sym, df, jour=jour, rng=rng):
                if sym == banc.CHANGE:
                    return df
                f = np.where(df.index > jour, rng.uniform(0.5, 1.5, len(df)), 1.0)
                for col in ("Open", "High", "Low", "Close"):
                    df[col] = df[col] * f
                df["Volume"] = np.where(df.index > jour, np.round(df["Volume"] * rng.uniform(0.2, 5, len(df))),
                                        df["Volume"])
                return df

            dossier = os.path.join(racine, f"apres-{jour:%Y%m%d}")
            _copier_cache(base, dossier, changer_apres)
            ok(f"CHANGER les cours et les volumes après le {jour:%d/%m/%Y} ({ou}) ne change aucune sortie d'avant",
               _memes_jusqu_a(c0, _chaine(dossier), jour))

            def changer_le_change(sym, df, jour=jour):
                if sym == banc.CHANGE:
                    df["Open"] = np.where(df.index > jour, df["Open"] * 1.3, df["Open"])
                return df

            dossier = os.path.join(racine, f"change-{jour:%Y%m%d}")
            _copier_cache(base, dossier, changer_le_change)
            ok(f"changer l'euro/dollar après le {jour:%d/%m/%Y} ({ou}) ne change aucune sortie d'avant "
               "(sans la purge, c et d changeraient à la première séance d'une année notée)",
               _memes_jusqu_a(c0, _chaine(dossier), jour))

        def changer_avant(sym, df):
            if sym == "CCC":
                df.loc[pd.Timestamp("2024-02-01"), ["Open", "High", "Low", "Close"]] *= 1.2
            return df

        dossier = os.path.join(racine, "avant")
        _copier_cache(base, dossier, changer_avant)
        c_avant = _chaine(dossier)
        ok("le contrôle inverse : changer un cours d'AVANT (le 01/02/2024) change des sorties d'avant le 15/03/2024 "
           "(les tests ne sont pas aveugles)",
           not _memes_jusqu_a(c0, c_avant, pd.Timestamp("2024-03-15"), ("a",))
           and not _memes_jusqu_a(c0, c_avant, pd.Timestamp("2024-03-15"), ("c",)))

        puissances = dict(zip(list(FAUX) + [banc.REFERENCE], (8, 0.25, 2, 1, 16, 0.5, 4)))
        ok("chaque valeur multipliée par SA puissance de 2 (×8, ÷4, ×2, ×1, ×16, ÷2, SPY ×4 ; volumes à l'inverse ; "
           "exact en binaire), comme l'ajustement de Yahoo après un fractionnement : les mêmes sorties au bit près, "
           "partout (une mesure en niveau le trahirait)",
           _memes_jusqu_a(c0, _chaine(base, echelle={t: (k, 1 / k) for t, k in puissances.items()}),
                          pd.Timestamp("2099-01-01")))
        d10, s10, _ = _chaine(base, echelle={t: (10, 1 / 10) for t in puissances})
        ok("des prix × 10 et des volumes ÷ 10 donnent les mêmes « hausse » et « baisse » pour toutes les méthodes "
           "(et des sorties égales à 10⁻⁹ près)",
           all(np.array_equal(avis(m, s0[m]), avis(m, s10[m]), equal_nan=True) for m in METHODES)
           and all(np.allclose(s0[m], s10[m], rtol=1e-9, atol=1e-12, equal_nan=True) for m in METHODES))

        temoin = os.path.join(racine, "temoin")
        os.makedirs(temoin)
        _faux_cache(temoin, graine=5, signal=0.75)
        dt, st, _ = _chaine(temoin)
        rapt, *_ = evaluer(dt, st, ANNEES_FAUSSES)
        ok("le témoin positif : un signal d'un jour glissé exprès (3 lendemains sur 4 dans le sens du jour) est « vu » "
           "par c et d face aux deux repères, et ouvre la porte A",
           "c" in rapt["passent"] and "d" in rapt["passent"] and rapt["porte_a"])
        seul_hier = copy.deepcopy(rap0)
        for m in METHODES:
            seul_hier["resultats"][TOUT][m]["passe"] = (m == "hier")
        ok("« comme hier » ne concourt pas : même « vu », il n'ouvre pas la porte A",
           porte_a(seul_hier["resultats"][TOUT]) == ([], False) and porte_a(rapt["resultats"][TOUT])[1])
        courtes = ((pd.Timestamp("2025-08-01"), pd.Timestamp("2025-09-30")),)
        refus = None
        try:
            evaluer(d0, s0, courtes)
        except CacheInvalide as erreur:
            refus = str(erreur)
        ok("une année notée de deux mois est refusée, avec un message clair", refus is not None and "séances" in refus)

        print("\n— La mesure : à blanc, écrite une seule fois —")
        rap_m, prev_m = mesurer(base, list(FAUX), ANNEES_FAUSSES)
        ok("mesurer = la chaîne entière (mêmes résultats que pas à pas), et des prévisions gardées pour chaque "
           "valeur-jour notée",
           banc._egaux(rap_m["resultats"], rap0["resultats"]) and len(prev_m) == rap0["seances"] * len(FAUX)
           and int(prev_m["comptee"].sum()) == rap0["valeurs_jours"]
           and np.array_equal(prev_m["c"].to_numpy(), s0["c"][lignes0].ravel(), equal_nan=True))
        rap_b, _ = mesurer(base, list(FAUX), ANNEES_FAUSSES, a_blanc=True)
        ok("à blanc : les réponses sont tirées au hasard, les résultats changent, le rapport le dit en tête",
           rap_b["a_blanc"] and not banc._egaux(rap_b["resultats"], rap0["resultats"])
           and rendu(rap_b).startswith("À BLANC"))
        banc_tmp = os.path.join(racine, "banc")
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            lancer_la_mesure(a_blanc=True, dossier=base, dossier_banc=banc_tmp, tickers=list(FAUX),
                             annees=ANNEES_FAUSSES)
        ok("à blanc, rien n'est écrit", not os.path.exists(banc_tmp) and "À BLANC" in sortie.getvalue())
        texte0 = rendu({**rap0, "apprentissage": appris0, "a_blanc": False})
        chemin = ecrire_mesure({**rap0, "apprentissage": appris0}, prev_m, texte0,
                               {"commit": "0" * 40, "modifie": False, "md5": {}}, banc_tmp)
        with open(chemin, encoding="utf-8") as f:
            lu = json.load(f)
        relue = pd.read_csv(os.path.join(banc_tmp, NOM_PREVISIONS), float_precision="round_trip")
        ok("le résultat écrit : la date, le commit, les choix de Mathis, le rapport tel qu'affiché, la porte A, le md5 "
           "et le nombre de lignes de SES prévisions ; les prévisions relues à l'identique",
           lu["code"]["commit"] == "0" * 40 and lu["rapport"] == texte0 and lu["resultat"]["porte_a"] is False
           and "moyenne exacte" in lu["choix"]["tirage"] and len(relue) == len(prev_m)
           and lu["previsions"]["md5"] == _md5(os.path.join(banc_tmp, NOM_PREVISIONS))
           and lu["previsions"]["lignes"] == len(prev_m) and lu["previsions"]["fichier"] == NOM_PREVISIONS
           and np.allclose(relue["d"].to_numpy(), prev_m["d"].to_numpy(), rtol=0, atol=0, equal_nan=True))
        deux = None
        try:
            ecrire_mesure(rap0, prev_m, "autre", {"commit": None, "modifie": False, "md5": {}}, banc_tmp)
        except FileExistsError:
            deux = True
        with open(chemin, encoding="utf-8") as f:
            ok("une seconde mesure ne s'écrase pas : refusée, le premier résultat reste tel quel",
               deux is True and json.load(f)["rapport"] == texte0)
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            lancer_la_mesure(dossier=os.path.join(racine, "inexistant"), dossier_banc=banc_tmp)
        ok("relancer --mesurer réaffiche le premier rapport, sans rien recalculer",
           "ne s'écrase pas" in sortie.getvalue() and texte0 in sortie.getvalue())
        interdit = None
        try:
            ecrire_mesure(rap0, prev_m, "x", {}, os.path.join(banc.MARCHE, "sous-dossier"))
        except RuntimeError:
            interdit = True
        ok("le banc n'écrit jamais dans data/market", interdit is True)
        ok("les nombres non finis deviennent null, les dates AAAA-MM-JJ",
           _en_json({"a": float("nan"), "b": pd.Timestamp("2024-01-02"), "c": np.bool_(True), "d": np.int64(3)})
           == {"a": None, "b": "2024-01-02", "c": True, "d": 3})
        from unittest import mock
        banc_coupe = os.path.join(racine, "banc-coupe")
        coupure = None
        with mock.patch("os.replace", side_effect=OSError("coupure")):
            try:
                ecrire_mesure(rap0, prev_m, "x", {"commit": None, "modifie": False, "md5": {}}, banc_coupe)
            except OSError as erreur:
                coupure = erreur
        ok("une coupure en posant les prévisions ne laisse aucun résultat sans elles (les prévisions d'abord, le "
           "résultat en dernier), ni aucun fichier temporaire",
           coupure is not None and os.listdir(banc_coupe) == [])

        print("\n— Le code commité, puis écrire avant d'afficher —")
        depot = os.path.join(racine, "depot")
        os.makedirs(depot)
        for nom in ("banc.py", "banc_methodes.py", "stock_engine.py"):
            shutil.copy(os.path.join(banc.ROOT, nom), depot)
        # Un dépôt jetable ordinaire : les crochets du Mac (le contrôle gitleaks posé à chaque git init) tournent.
        git = ["git", "-c", "user.name=banc", "-c", "user.email=banc@exemple.invalid"]
        for commande in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "essai"]):
            subprocess.run(git + commande, cwd=depot, check=True, capture_output=True)
        propre = empreinte_du_code(depot)
        retouches = {}
        for nom in ("banc.py", "banc_methodes.py", "stock_engine.py"):
            chemin_code = os.path.join(depot, nom)
            avant = open(chemin_code, "rb").read()
            with open(chemin_code, "ab") as f:
                f.write(b"\n# une retouche\n")
            retouches[nom] = empreinte_du_code(depot)["modifie"]
            with open(chemin_code, "wb") as f:
                f.write(avant)
        ok("l'empreinte du code : le commit et « pas modifié » sur un dépôt propre ; « modifié » dès que banc.py, "
           "banc_methodes.py OU stock_engine.py change ; les md5 des trois fichiers",
           not propre["modifie"] and len(propre["commit"] or "") == 40 and all(retouches.values())
           and propre["md5"] == md5_des_fichiers(depot) and set(propre["md5"]) == set(retouches)
           and not empreinte_du_code(depot)["modifie"])
        with open(os.path.join(depot, "banc_methodes.py"), "a", encoding="utf-8") as f:
            f.write("\n# une retouche pas commitée\n")
        banc_refus, sortie, refus = os.path.join(racine, "banc-refus"), io.StringIO(), None
        with contextlib.redirect_stdout(sortie):
            try:
                lancer_la_mesure(dossier=base, dossier_banc=banc_refus, tickers=list(FAUX), annees=ANNEES_FAUSSES,
                                 racine_du_code=depot)
            except SystemExit as erreur:
                refus = str(erreur)
        ok("sur du code pas commité, la mesure refuse : rien n'est calculé, affiché ni écrit",
           refus is not None and "commit" in refus and sortie.getvalue() == "" and not os.path.exists(banc_refus))
        subprocess.run(git + ["checkout", "-q", "--", "banc_methodes.py"], cwd=depot, check=True, capture_output=True)
        pas_un_dossier = os.path.join(racine, "pas-un-dossier")
        open(pas_un_dossier, "w").close()
        sortie, echec = io.StringIO(), None
        with contextlib.redirect_stdout(sortie):
            try:
                lancer_la_mesure(dossier=base, dossier_banc=os.path.join(pas_un_dossier, "banc"), tickers=list(FAUX),
                                 annees=ANNEES_FAUSSES, racine_du_code=depot)
            except OSError as erreur:
                echec = erreur
        ok("si l'écriture échoue, rien n'est affiché : le résultat n'a pas été vu, la mesure peut se refaire",
           echec is not None and sortie.getvalue() == "")
        banc_ok, sortie = os.path.join(racine, "banc-ok"), io.StringIO()
        with contextlib.redirect_stdout(sortie):
            lancer_la_mesure(dossier=base, dossier_banc=banc_ok, tickers=list(FAUX), annees=ANNEES_FAUSSES,
                             racine_du_code=depot)
        with open(os.path.join(banc_ok, NOM_MESURE), encoding="utf-8") as f:
            ecrit = json.load(f)
        ok("sur du code commité : la mesure s'écrit, PUIS affiche le rapport relu dans le fichier ; le fichier garde le "
           f"commit, l'empreinte des {len(FAUX) + 2} fichiers de cours lus et les versions des bibliothèques",
           sortie.getvalue().startswith(ecrit["rapport"] + "\n") and "Écrit, une fois pour toutes" in sortie.getvalue()
           and ecrit["code"]["commit"] == propre["commit"] and not ecrit["code"]["modifie"]
           and ecrit["donnees"] == empreinte_des_donnees(base, FAUX) and len(ecrit["donnees"]) == len(FAUX) + 2
           and {"python", "numpy", "pandas", "scikit-learn", "scipy", "ta"} <= set(ecrit["bibliotheques"])
           and os.path.exists(os.path.join(banc_ok, NOM_PREVISIONS)))

        print("\n— La commande —")
        essai_cli = ("import runpy, sys, banc, banc_methodes\n"
                     "def refuse(**_):\n    raise banc.CacheInvalide('un cache inventé pour l’essai')\n"
                     "banc_methodes.lancer_la_mesure = refuse\n"
                     "sys.argv = ['banc.py', '--mesurer', '--a-blanc']\n"
                     "runpy.run_path(banc.__file__, run_name='__main__')\n")
        r_cli = subprocess.run([sys.executable, "-c", essai_cli], cwd=banc.ROOT, capture_output=True,
                               encoding="utf-8", timeout=120,
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"})
        ok("en commande, un cache refusé à l'étape 1 donne « Rien n'est calculé : … » et le code 1, pas une trace Python",
           r_cli.returncode == 1 and "Rien n'est calculé : un cache inventé pour l’essai" in r_cli.stderr
           and "Traceback" not in r_cli.stderr)

        print("\n— Le cache abîmé —")
        abime = os.path.join(racine, "abime")
        for genre, changer, attendu in (
                ("une date illisible", lambda p: open(p, "a").write("pas une date,1,1,1,1,1\n"), "illisibles"),
                ("des dates avec un fuseau", None, "fuseau"),
                ("un volume nul", None, "nul"),
                ("une colonne Volume absente", None, "Volume")):
            _copier_cache(base, abime)
            p = os.path.join(abime, "BBB_10y.csv")
            df = pd.read_csv(p, index_col=0)
            if genre == "une date illisible":
                changer(p)
            elif genre == "des dates avec un fuseau":
                df.index = pd.DatetimeIndex(df.index).tz_localize("UTC")     # un décalage fixe : pandas les lit
                df.to_csv(p)
            elif genre == "un volume nul":
                df.iloc[100, df.columns.get_loc("Volume")] = 0
                df.to_csv(p)
            else:
                df.drop(columns=["Volume"]).to_csv(p)
            message = None
            try:
                charger_cours(abime, FAUX)
            except CacheInvalide as erreur:
                message = str(erreur)
            ok(f"{genre} est refusé avec un message clair", message is not None and "BBB_10y.csv" in message
               and attendu in message)

    print("\n— Le rapport —")
    faux = copy.deepcopy(rap0)
    faux.update(apprentissage=appris0, a_blanc=False)
    for nom in [TOUT] + faux["annees"]:                # au départ, aucun « vu » nulle part
        for m in METHODES:
            for cle in ("tirage", "monte"):
                faux["resultats"][nom][m]["face"][cle].update(vu=False, vu_dessous=False)
            faux["resultats"][nom][m]["passe"] = False
            if m in CANDIDATES:
                faux["resultats"][nom][m]["face"]["sens_du_jour"]["vu"] = False
    x = faux["resultats"][TOUT]["a"]
    x.update(baisse=0.431, bonnes=0.512, tirage=0.5088, monte=0.5201, barre=0.5291)
    x["face"]["tirage"].update(ecart=0.0032, seuil=0.0037)
    x["face"]["monte"].update(ecart=-0.0081, seuil=0.0090)
    xb = faux["resultats"][TOUT]["b"]
    xb.update(bonnes=0.531, tirage=0.5300, monte=0.5201, barre=0.5340)
    xb["face"]["tirage"].update(seuil=0.0040)
    xb["face"]["monte"].update(seuil=0.0020)
    an1, an2 = faux["annees"]
    faux["resultats"][an1]["a"]["monte"], faux["resultats"][an2]["a"]["monte"] = 0.511, 0.533
    faux["resultats"][an1]["a"]["face"]["tirage"].update(ecart=0.0123, vu=True)       # vu face au tirage SEUL
    faux["resultats"][an2]["b"]["face"]["tirage"].update(ecart=0.0234, vu=True)
    faux["resultats"][an2]["b"]["face"]["monte"].update(vu=True)
    faux["resultats"][an2]["b"]["passe"] = True                                       # vu face aux deux
    texte = rendu(faux)
    ligne_a = next((l for l in texte.splitlines() if l.strip().startswith("a. le score technique ")), "")
    ok("la ligne de a : « baisse » 43,1 %, bonnes réponses 51,20 %, +0,32 (−0,05) face au tirage, −0,81 (−1,71) "
       "face à « ça monte », « ne passe pas »",
       "43,1 %" in ligne_a and "51,20 %" in ligne_a and "+0,32 (−0,05)" in ligne_a and "−0,81 (−1,71)" in ligne_a
       and ligne_a.rstrip().endswith("ne passe pas"))
    ok("la barre de a, et ce que le test voyait face à chaque repère : la barre vient de « ça monte toujours » "
       "(52,01 % + 0,90 = 52,91 %, plus haut que 50,88 % + 0,37)",
       "a. le score technique : il lui fallait plus de 52,91 % de bonnes réponses ; elle en a eu 51,20 %. Le test "
       "voyait les écarts de plus de 0,37 point face au tirage (50,88 %) et de plus de 0,90 point face à « ça monte "
       "toujours » (52,01 %) : la barre vient de « ça monte toujours »." in texte)
    ok("… et celle de b vient du tirage (53,00 % + 0,40 = 53,40 %, plus haut que 52,01 % + 0,20)",
       "b. le momentum 12-1 : il lui fallait plus de 53,40 % de bonnes réponses ; elle en a eu 53,10 %. Le test "
       "voyait les écarts de plus de 0,40 point face au tirage (53,00 %) et de plus de 0,20 point face à « ça monte "
       "toujours » (52,01 %) : la barre vient du tirage." in texte)
    ok("les seuils portent sur l'écart OBSERVÉ : un écart vrai de cette taille n'est vu qu'une fois sur deux, il en "
       "faut environ 1,36 fois plus pour l'être 8 fois sur 10",
       "Ces seuils portent sur l'écart OBSERVÉ : une méthode dont l'écart vrai vaut le seuil n'est vue qu'une fois "
       "sur deux ; pour l'être 8 fois sur 10, il lui faut environ 1,36 fois le seuil." in texte)
    ok("« ça monte toujours » : son chiffre sur les 2 ans (52,01 %), puis année par année (51,1 % et 53,3 %)",
       "   « Ça monte toujours » : 52,01 % de bonnes réponses." in texte
       and f"« ça monte » : {an1} 51,1 %, {an2} 53,3 %." in texte)
    annees_txt = texte.split("2. ANNÉE PAR ANNÉE")[1].split("3. FACE")[0]
    ligne_a2 = next((l for l in annees_txt.splitlines() if l.strip().startswith("a. le score technique")), "")
    ligne_b2 = next((l for l in annees_txt.splitlines() if l.strip().startswith("b. le momentum")), "")
    ok("année par année, « vu » seulement face aux DEUX repères : pas pour a (vue face au tirage seul, +1,23), "
       "oui pour b (+2,34)", "+1,23" in ligne_a2 and "+1,23 vu" not in ligne_a2 and "+2,34 vu" in ligne_b2)
    ok("les écarts avec leur unité : +2,30 points, −0,45 point, −2,00 points",
       _ecart_pts(0.023) == "+2,30 points" and _ecart_pts(-0.0045) == "−0,45 point" and _ecart_pts(-0.02) == "−2,00 points")
    ok("aucune candidate ne passe : « le coffre reste fermé »",
       "Aucune candidate ne passe le critère 1 sur les 2 ans : le coffre reste fermé." in texte)
    ok("aucune phrase des cas particuliers quand rien n'est vu",
       "est vue face au tirage" not in texte and "EN DESSOUS du tirage" not in texte)
    x["face"]["tirage"]["vu"] = True
    texte_vue = rendu(faux)
    x["face"]["monte"]["vu_dessous"] = True
    texte_dessous = rendu(faux)
    x["face"]["tirage"]["vu"] = x["face"]["monte"]["vu_dessous"] = False
    ok("vue face au tirage, pas face à « ça monte » : la phrase écrite d'avance (« à peu près aussi bien »), ou "
       "« nettement mieux » si elle est nettement en dessous de « ça monte toujours »",
       "a. le score technique est vue face au tirage, pas face à « ça monte toujours » : elle reconnaît mieux que le "
       "hasard les lendemains de hausse, mais dire « hausse » partout fait à peu près aussi bien. Elle ne passe pas. "
       "Le biais du survivant suffit à produire ce cas : les 49 valeurs ont été choisies en 2026." in texte_vue
       and "mais dire « hausse » partout fait nettement mieux. Elle ne passe pas." in texte_dessous)
    xc = faux["resultats"][TOUT]["c"]
    xc["face"]["tirage"]["vu_dessous"] = True
    texte_c = rendu(faux)
    xc["face"]["tirage"]["vu_dessous"] = False
    ok("nettement EN DESSOUS du tirage : son contraire n'est pas une candidate (écrit d'avance)",
       "c. la régression logistique est nettement EN DESSOUS du tirage au sort : son contraire ferait mieux que le "
       "hasard, mais ce contraire n'est pas une candidate (il n'a pas été choisi avant de regarder). Elle ne passe "
       "pas." in texte_c)
    deux_passent = copy.deepcopy(faux)
    deux_passent["passent"], deux_passent["porte_a"] = ["c", "d"], True
    un_passe = copy.deepcopy(faux)
    un_passe["passent"], un_passe["porte_a"] = ["b"], True
    ok("la porte A : les candidates qui passent, « pour elle » ou « pour elles », et le biais du survivant qui a pu "
       "les aider",
       "Passe le critère 1 : c. la régression logistique, d. le gradient boosting." in rendu(deux_passent)
       and "s'ouvre pour elles, rien de plus : ça ne dit pas que l'argent suivrait (le critère 2), et le biais du "
           "survivant a pu les aider" in rendu(deux_passent)
       and "s'ouvre pour elle, rien de plus : ça ne dit pas que l'argent suivrait (le critère 2), et le biais du "
           "survivant a pu l'aider" in rendu(un_passe))
    rebond = copy.deepcopy(faux)
    rebond["resultats"][TOUT]["hier"]["face"]["tirage"].update(ecart=-0.012, vu=False, vu_dessous=True)
    elan = copy.deepcopy(faux)
    elan["resultats"][TOUT]["hier"]["face"]["tirage"].update(ecart=0.012, vu=True, vu_dessous=False)
    neutre = copy.deepcopy(faux)
    neutre["resultats"][TOUT]["hier"]["face"]["tirage"].update(ecart=0.001, seuil=0.0040, seuil_dessous=0.0045)
    ok("« comme hier » : « rebond d'un jour » s'il est nettement en dessous du tirage, « élan d'un jour » au-dessus ; "
       "sinon « rien de net », avec ce que le test voyait (le plus grand des deux seuils), sans dire que le sens du "
       "jour ne dit rien",
       "« Comme hier » face au tirage au sort : −1,20 point ; demain va nettement plus souvent dans l'autre sens (un "
       "rebond d'un jour)." in rendu(rebond)
       and "« Comme hier » face au tirage au sort : +1,20 point ; demain va nettement plus souvent dans le même sens "
           "qu'aujourd'hui (un élan d'un jour)." in rendu(elan)
       and "« Comme hier » face au tirage au sort : +0,10 point ; rien de net à 99 % : le test ne voyait que les "
           "écarts de plus de 0,45 point, dans un sens ou dans l'autre." in rendu(neutre))
    sens = copy.deepcopy(faux)
    for m, (e1, s1, e2, s2, ecart, vu) in {"a": (0.0010, 0.0035, -0.0020, 0.0040, -0.0020, False),
                                           "b": (0.0011, 0.0041, 0.0020, 0.0045, 0.0011, False),
                                           "c": (0.0080, 0.0050, 0.0042, 0.0031, 0.0042, True),
                                           "d": (-0.0063, 0.0039, 0.0012, 0.0050, -0.0063, False)}.items():
        f = sens["resultats"][TOUT][m]["face"]["sens_du_jour"]
        f["comme_hier"].update(ecart=e1, seuil=s1)
        f["contraire"].update(ecart=e2, seuil=s2)
        f.update(ecart=ecart, vu=vu)
    texte_sens = rendu(sens)
    ok("face au sens du jour : ses deux versions, chacune avec sa borne ; « nettement mieux » (c) seulement si elle "
       "est vue face aux deux, sinon « pas nettement mieux » (d, et b malgré ses écarts positifs), jamais « moins "
       "bien » ; ce que ce test prudent pouvait voir (de 0,31 à 0,50 point), et ce que « pas nettement mieux » ne "
       "dit pas",
       "c. la régression logistique : face à « comme hier » +0,80 (+0,30), face à son contraire +0,42 (+0,11) : "
       "nettement mieux que le sens du jour, dans un sens comme dans l'autre." in texte_sens
       and "d. le gradient boosting : face à « comme hier » −0,63 (−1,02), face à son contraire +0,12 (−0,38) : pas "
           "nettement mieux que le sens du jour." in texte_sens
       and "b. le momentum 12-1 : face à « comme hier » +0,11 (−0,30), face à son contraire +0,20 (−0,25) : pas "
           "nettement mieux que le sens du jour." in texte_sens
       and "Ce test est prudent : il ne voyait que les écarts de plus de 0,31 à 0,50 point. « Pas nettement mieux » "
           "ne dit donc pas que l'avance d'une candidate ne vient que du sens du jour." in texte_sens
       and "moins bien que le sens du jour" not in texte_sens)
    ok("le rapport rappelle le biais du survivant, dans les deux sens (« ça monte toujours » et une méthode), et "
       "finit par « pas une prévision ni un conseil »",
       "« Ça monte toujours » en est flatté ; une méthode peut l'être aussi" in texte
       and texte.endswith("Des faits sur le passé, pas une prévision ni un conseil."))

    print(f"\n{sum(cas)}/{len(cas)} vérifications réussies (étape 1)")
    return all(cas)

