"""
Le banc des prévisions — étape 0 : le mur des frais.

CE QUE ÇA FAIT
    Mathis a demandé (16/11/2026) un système, calé sur son courtier, qui
    prévoit la hausse ou la baisse du lendemain des 49 valeurs. Avant de
    mesurer si une méthode y arrive (l'étape 1), ce module chiffre, sur le
    cache des cours, ce qu'elle devrait réussir pour que ça paie :
      1. le coût d'un aller-retour chez le courtier (acheter le soir, revendre le
         lendemain) : 1 € par ordre, plus l'écart entre le prix d'achat et
         le prix de vente ;
      2. le rendement qu'il faudrait gagner à chaque séance pour ne pas
         perdre, face à ce qu'une valeur rapporte en moyenne, et le nombre
         de séances au bout duquel les frais vident le capital ;
      3. le taux de bonnes réponses qu'il faudrait (la valeur achetée le
         soir monte le lendemain) : en moyenne, puis en simulant la
         stratégie de 200 € face à SPY et au panier des 49 gardés ;
      4. le plus petit écart de bonnes réponses que le test de l'étape 1
         pourra distinguer du hasard ;
      5. les bonnes réponses de « ça monte toujours », année par année.

CE QUE ÇA NE FAIT PAS
    Aucune prévision, aucun conseil, aucun ordre. Aucune des quatre
    méthodes candidates n'est calculée ici : elles attendent l'étape 1
    (plan validé le 17/11/2026, à la fin de BRIEF.md).

LES DONNÉES
    Le cache data/market/*_10y.csv, lu sans rien écrire : les 49 valeurs
    (stock_engine.UNIVERSE), SPY et l'euro/dollar. Les cours de Yahoo sont
    ajustés des dividendes et des fractionnements (auto_adjust=True dans
    stock_eval.load_prices) : les rendements comptent les dividendes. Tout
    est coupé au 31/10/2025 dès la lecture : le coffre (nov. 2025 → nov.
    2026) n'est pas regardé. Les années notées vont de novembre à octobre,
    de nov. 2019 à oct. 2025 (les six années de l'étape 1).

LANCER
    python3 banc.py --frais            # le mur des frais (moins de 10 s)
    python3 banc.py --verifier         # les cas calculés à la main, sans le cache
    python3 banc.py --fausses-alarmes  # le test, sans talent, dit-il « vu » 1 fois sur 100 ? (moins d'1 min)
"""

import argparse
import contextlib
import copy
import hashlib
import io
import math
import os
import sys
import tempfile
from statistics import NormalDist

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import stock_engine as engine   # noqa: E402  (les 49 valeurs)

MARCHE = os.path.join(ROOT, "data", "market")
REFERENCE = "SPY"                # le S&P 500, tel qu'un ETF le suit
CHANGE = "EURUSD=X"              # des dollars pour un euro
FIN_AVANT_COFFRE = pd.Timestamp("2025-10-31")
# Les six années notées de l'étape 1, de novembre à octobre.
ANNEES_NOTEES = tuple((pd.Timestamp(f"{a}-11-01"), pd.Timestamp(f"{a + 1}-10-31"))
                      for a in range(2019, 2025))
SEANCES_MIN_ANNEE = 200          # une année notée qui en a moins est refusée (une vraie en a environ 250)

# Les frais du courtier (relevés par Mathis le 16/11/2026).
FRAIS_ORDRE = 1.00               # € par ordre, à l'achat comme à la vente
ECART = 0.001                    # l'écart achat/vente pour l'aller-retour ENTIER (environ 0,1 % chez le courtier)
ECART_CONTROLE = 0.003           # le contrôle : il ne décide rien
CAPITAUX = (50.0, 200.0)
CAPITAL = 200.0                  # la stratégie de 200 € du plan

ECART_CHANGE_MAX = 5             # jours : du jeudi au mardi de Pâques, Yahoo n'a pas de change
VOL_JOURS = 20                   # l'agitation : la volatilité des 20 dernières séances
PART_AGITEES = 0.10              # les 10 % les plus agitées : 5 valeurs sur 49 chaque soir

# Le test de l'étape 1 (critère 1 du plan).
NIVEAU = 0.99                    # 99 % et pas 95 % : quatre candidates concourent
TIRAGES = 5000                   # « au moins 5 000 tirages »
BLOC = 10                        # deux semaines de Bourse (fausses alarmes mesurées le 17/11/2026, voir CLAUDE.md)
METHODES = 50                    # méthodes sans talent tirées pour chaque cas (on garde la médiane)
PARTS_BAISSE = (0.20, 0.50)
FORMES = {"valeur": "valeur par valeur", "marche": "tout le marché"}
PUISSANCE = 0.80                 # « vue 8 fois sur 10 »
CHEMINS = 1000                   # chemins de la stratégie simulée
PARTS_CHEMINS = (0.5, 0.9)       # finir devant « 1 fois sur 2 » et « 9 fois sur 10 »
FAUSSES_ALARMES_METHODES = 1000  # méthodes sans talent par cas et par période, pour --fausses-alarmes
PAQUETS_BLOCS = 5                # … réparties sur 5 tirages des blocs par période (un seul tirage varie trop)
GRAINE = 20261117
TOUT = "tout"                    # la clé de toute la période notée, à côté des années


class CacheInvalide(ValueError):
    """Le cache n'a pas ce qu'il faut (fichier abîmé, année trop courte) : rien n'est calculé."""


# ══════════════════════════════════════════════════════════════════
#  LES FRAIS (purs : vérifiés à la main par --verifier)
# ══════════════════════════════════════════════════════════════════

def facteur_ecart(ecart):
    """
    Ce qui reste de l'argent placé après un aller-retour à cours inchangé,
    à cause de l'écart : on achète au prix vendeur (milieu × (1 + ecart/2))
    et on revend au prix acheteur (milieu × (1 − ecart/2)). Un DEMI-écart
    de chaque côté : compter l'écart entier des deux côtés le doublerait.
    """
    h = ecart / 2
    return (1 - h) / (1 + h)


def apres_aller_retour(capital, rendement, ecart=ECART, frais=FRAIS_ORDRE):
    """
    Le capital après un aller-retour : 1 € pour l'ordre d'achat (le reste
    est placé), le rendement de la valeur entre les deux milieux de
    fourchette, puis 1 € pour l'ordre de vente.
    """
    return (capital - frais) * facteur_ecart(ecart) * (1 + rendement) - frais


def cout_aller_retour(capital, ecart=ECART, frais=FRAIS_ORDRE):
    """Ce qu'un aller-retour coûte quand le cours ne bouge pas, en euros."""
    return capital - apres_aller_retour(capital, 0.0, ecart, frais)


def rendement_necessaire(capital, ecart=ECART, frais=FRAIS_ORDRE, objectif=0.0):
    """
    Le rendement brut qu'il faudrait sur la séance pour finir l'aller-retour
    à capital × (1 + objectif) : objectif = 0 pour ne pas perdre.
    """
    return (capital * (1 + objectif) + frais) / ((capital - frais) * facteur_ecart(ecart)) - 1


def seances_jusqu_a(capital, seuil, ecart=ECART, frais=FRAIS_ORDRE, plafond=100_000):
    """
    À rendement nul, un aller-retour par séance : le nombre de séances
    jouées avant que le capital tombe à `seuil` ou moins, et ce qui reste.
    Avec seuil = 2 € : le capital ne paie plus les deux frais, tout est parti.
    """
    n = 0
    while capital > seuil and n < plafond:
        capital = max(apres_aller_retour(capital, 0.0, ecart, frais), 0.0)
        n += 1
    return n, capital


def taux_necessaire(hausse, baisse, rendement):
    """
    La part des séances où la valeur achetée doit monter pour que le
    rendement moyen atteigne `rendement` : quand elle monte, elle gagne
    `hausse` en moyenne ; quand elle baisse, elle perd `baisse` (positif).
        p × hausse − (1 − p) × baisse = rendement
        p = (rendement + baisse) / (hausse + baisse)
    Au-dessus de 1 : impossible en moyenne, même en n'achetant que des
    valeurs qui montent (prises au hasard parmi elles).
    """
    return (rendement + baisse) / (hausse + baisse)


def hausse_et_baisse(rendements):
    """La hausse moyenne des valeurs-jours qui montent, et la baisse moyenne (positive) des autres."""
    x = np.asarray(rendements, float).ravel()
    x = x[np.isfinite(x)]
    return float(x[x > 0].mean()), float(-x[x <= 0].mean())


def part_de_hausses(rendements):
    """
    La part des valeurs-jours en hausse STRICTE : les bonnes réponses de
    « ça monte toujours » (un rendement nul n'est pas une hausse).
    """
    x = np.asarray(rendements, float).ravel()
    x = x[np.isfinite(x)]
    return (float((x > 0).mean()) if len(x) else float("nan")), int(len(x))


# ══════════════════════════════════════════════════════════════════
#  LES DONNÉES (le cache, lu sans rien écrire, coupé avant le coffre)
# ══════════════════════════════════════════════════════════════════

def _lire(dossier, symbole, fin, colonne):
    """
    Une colonne d'un fichier du cache, coupée à `fin` juste après la
    lecture. Refusée (CacheInvalide, avec un message clair) si le fichier n'a
    pas cette colonne, s'il a des dates en double, ou si un cours de cette
    colonne est nul, négatif ou infini avant la coupure. Un cours vide, lui,
    ne compte simplement pas ; les autres colonnes ne sont pas regardées.
    """
    nom = f"{symbole}_10y.csv"
    df = pd.read_csv(os.path.join(dossier, nom), index_col=0, parse_dates=True).sort_index().loc[:fin]
    if colonne not in df.columns:
        raise CacheInvalide(f"{nom} n'a pas de colonne « {colonne} » : le cache est à vérifier")
    if df.index.duplicated().any():
        raise CacheInvalide(f"{nom} a des dates en double ({df.index[df.index.duplicated()][0].date()}) : "
                            "le cache est à vérifier")
    serie = df[colonne].astype(float)
    x = serie.to_numpy()
    if (np.isinf(x) | (x <= 0)).any():
        raise CacheInvalide(f"{nom} a un cours nul, négatif ou infini : le cache est à vérifier")
    return serie


def charger(dossier=MARCHE, tickers=None, fin=FIN_AVANT_COFFRE):
    """
    Les clôtures en dollars des valeurs et de SPY, et l'ouverture de
    l'euro/dollar, toutes coupées à `fin` : rien après n'est gardé.
    """
    tickers = list(engine.UNIVERSE_TICKERS if tickers is None else tickers)
    cours = pd.DataFrame({t: _lire(dossier, t, fin, "Close") for t in tickers + [REFERENCE]}).sort_index()
    change = _lire(dossier, CHANGE, fin, "Open").dropna()
    if cours.index.max() > fin or change.index.max() > fin:
        raise RuntimeError("une date après la coupure a passé : le coffre serait regardé")
    return cours, change


def en_euros(cours, change, ecart_max=ECART_CHANGE_MAX):
    """
    Les cours en euros. La clôture de New York du jour J (22 h à Paris) est
    divisée par l'OUVERTURE de la ligne suivante de l'euro/dollar chez Yahoo
    (vers 1 h du matin à Paris, environ 3 h plus tard). La « clôture »
    journalière de l'euro/dollar est en fait une photo du début de journée :
    la prendre reviendrait à un change d'environ 21 h plus tôt (vérifié le
    16/11/2026). Jamais de bfill. Le cours reste vide quand la ligne
    suivante est à plus de `ecart_max` jours, ou avant le début du change.
    """
    jours = cours.index
    pos = change.index.searchsorted(jours, side="right")       # la 1re ligne de change APRÈS J
    taux = np.full(len(jours), np.nan)
    dans = pos < len(change)
    if dans.any():
        suivante = change.index[pos[dans]]
        delai = np.asarray((suivante - jours[dans]).days)
        connu = (delai <= ecart_max) & np.asarray(jours[dans] >= change.index[0])
        t = change.to_numpy(float)[pos[dans]]
        t[~connu] = np.nan
        taux[dans] = t
    return cours.div(taux, axis=0)


def rendements_du_lendemain(prix):
    """Le rendement de J à la séance suivante du tableau, rangé à la date J (le soir où l'on décide)."""
    return prix.shift(-1) / prix - 1


def agitation(cours, jours=VOL_JOURS):
    """
    La volatilité des `jours` dernières séances, connue le soir même : en
    dollars, pour ne pas dépendre du change de la nuit suivante.
    """
    variations = cours / cours.shift(1) - 1
    return variations.rolling(jours, min_periods=jours).std()


def plus_agitees(vol, part=PART_AGITEES):
    """Chaque soir, les ⌈10 % × n⌉ valeurs les plus agitées parmi celles qui ont une volatilité (5 sur 49)."""
    n = vol.notna().sum(axis=1)
    combien = np.ceil(part * n)
    rang = vol.rank(axis=1, ascending=False, method="first")
    return rang.le(combien, axis=0) & vol.notna()


def _periode_notee(dossier, tickers, annees):
    """
    Les rendements du lendemain des valeurs (séances notées × valeurs), ceux
    de SPY, la volatilité connue chaque soir et les plus agitées du soir.
    Refuse une année notée qui a moins de SEANCES_MIN_ANNEE séances.
    """
    cours, change = charger(dossier, tickers)
    valeurs = [t for t in cours.columns if t != REFERENCE]
    r = rendements_du_lendemain(en_euros(cours, change))
    vol = agitation(cours[valeurs])
    R = r.loc[annees[0][0]:annees[-1][1], valeurs].dropna(how="all")   # les séances qui ont leur lendemain
    for a, b in annees:
        n = len(R.loc[a:b])
        if n < SEANCES_MIN_ANNEE:
            raise CacheInvalide(f"l'année notée {_nom_annee(a, b)} n'a que {n} séances dans le cache "
                             f"(au moins {SEANCES_MIN_ANNEE} attendues) : rien n'est calculé")
    spy = r.loc[R.index, REFERENCE]
    if spy.isna().any():
        raise CacheInvalide("SPY n'a pas de lendemain certains soirs où les valeurs en ont un")
    agitees = plus_agitees(vol).reindex(R.index).fillna(False).astype(bool)
    return R, spy, vol.reindex(R.index), agitees


def _nom_annee(debut, fin):
    """« 2019-2020 » pour une année notée de novembre à octobre."""
    return f"{debut.year}-{fin.year}" if debut.year != fin.year else f"{debut:%d/%m}-{fin:%d/%m/%Y}"


# ══════════════════════════════════════════════════════════════════
#  CE QUE LE TEST DE L'ÉTAPE 1 POURRA VOIR
# ══════════════════════════════════════════════════════════════════
#
#  Le critère 1 du plan : la méthode a raison plus souvent que (a) un
#  tirage au sort qui dit « baisse » aussi souvent qu'elle, et (b) « ça
#  monte toujours ». Le plan dit : UN tirage, à graine fixe. Proposé (à
#  décider par Mathis avant l'étape 1) : le compter par son espérance exacte
#  sur la même période (la moyenne de tous les tirages possibles), même
#  sens, sans son bruit à lui.
#
#  Avec m = 1 pour « hausse », y = 1 si la valeur a monté, sur toutes les
#  valeurs-jours de la période :
#      bonnes réponses de la méthode   H = moy(m·y + (1 − m)(1 − y))
#      moyenne des tirages au sort     T = moy(m)·moy(y) + (1 − moy(m))(1 − moy(y))
#      « ça monte toujours »           U = moy(y)
#  donc H − T = 2 (moy(m·y) − moy(m)·moy(y)) et H − U = 1 − moy(m) − 2 moy(y) + 2 moy(m·y).

def poids_blocs(n, bloc=BLOC, tirages=TIRAGES, graine=GRAINE, paquet=500):
    """
    Le bootstrap par blocs de séances, circulaire : chaque tirage recolle
    des blocs de `bloc` séances consécutives pris au hasard (un bloc qui
    dépasse la fin repart au début), jusqu'à n séances. Rend, pour chaque
    tirage, le nombre de fois que chaque séance est reprise (tirages × n).
    Des séances entières : les 49 valeurs d'un même soir, qui bougent
    ensemble, restent ensemble.
    """
    rng = np.random.default_rng(graine)
    nb = math.ceil(n / bloc)
    poids = np.empty((tirages, n), dtype=np.float32)
    for a in range(0, tirages, paquet):
        k = min(paquet, tirages - a)
        debuts = rng.integers(0, n, size=(k, nb))
        idx = ((debuts[:, :, None] + np.arange(bloc)) % n).reshape(k, nb * bloc)[:, :n]
        lignes = np.repeat(np.arange(k), n)
        poids[a:a + k] = np.bincount(lignes * n + idx.ravel(), minlength=k * n).reshape(k, n)
    return poids


def ecarts(m, y, valide, poids=None, base=None):
    """
    Les écarts de bonnes réponses d'une méthode (lignes = séances, colonnes
    = valeurs) : face à la moyenne des tirages au sort (H − T), face à « ça
    monte toujours » (H − U) et, si `base` est donné, face à CE tirage-là
    (H − H_base). Avec `poids` (le bootstrap), rend aussi ces écarts pour
    chaque tirage : tout est recalculé sur les séances reprises.
    """
    v = np.asarray(valide, bool)
    m = np.where(v, m, 0.0)
    y = np.where(v, y, 0.0)
    colonnes = [v.sum(1), m.sum(1), y.sum(1), (m * y).sum(1)]
    if base is not None:
        b = np.where(v, base, 0.0)
        juste = m * y + (1 - m) * (1 - y)
        juste_base = b * y + (1 - b) * (1 - y)
        colonnes.append(((juste - juste_base) * v).sum(1))
    par_seance = np.column_stack(colonnes).astype(float)

    def calcul(s):
        n = s[..., 0]
        mm, yy, my = s[..., 1] / n, s[..., 2] / n, s[..., 3] / n
        res = (2 * (my - mm * yy), 1 - mm - 2 * yy + 2 * my)
        return res + (s[..., 4] / n,) if base is not None else res

    obs = calcul(par_seance.sum(0))
    if poids is None:
        return obs
    # numpy 2.0.2 (/usr/bin/python3, Accelerate) signale de faux « divide by
    # zero / overflow / invalid value encountered in matmul » sur ce produit,
    # au résultat exact : on les tait ici seulement, et on vérifie le résultat.
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        tires = poids @ par_seance
    if not np.isfinite(tires).all():
        raise FloatingPointError("le bootstrap a donné une valeur non finie")
    return obs, calcul(tires)


def seuil_critique(observe, tires, niveau=NIVEAU):
    """
    Le plus petit écart OBSERVÉ que le test déclarerait « vu » : la distance
    entre l'écart observé et sa borne basse à 99 % (quantile 1 % du
    bootstrap). Une méthode dont l'écart VRAI vaut ce seuil n'est vue
    qu'une fois sur deux (voir PUISSANCE).
    """
    return float(np.quantile(observe - np.asarray(tires, float), niveau))


def facteur_puissance(puissance=PUISSANCE, niveau=NIVEAU):
    """Combien de fois le seuil un écart vrai doit valoir pour être vu `puissance` fois sur 1 (loi normale)."""
    z = NormalDist().inv_cdf
    return 1 + z(puissance) / z(niveau)


def methode_sans_talent(forme, part_baisse, seances, valeurs, rng):
    """
    Une méthode qui ne sait rien et dit « baisse » une part `part_baisse`
    du temps (1 = « hausse »). « valeur » : chaque valeur-jour au hasard ;
    « marche » : le même avis pour toutes les valeurs le même soir. Les
    vraies méthodes sont entre les deux.
    """
    if forme == "valeur":
        return (rng.random((seances, valeurs)) >= part_baisse).astype(float)
    if forme == "marche":
        return np.repeat((rng.random((seances, 1)) >= part_baisse).astype(float), valeurs, axis=1)
    raise ValueError(forme)


def _une_methode(r, part_baisse, forme, rng):
    """Une méthode sans talent et le tirage au sort du plan : valeur par valeur, « baisse » aussi souvent qu'elle."""
    valide = np.isfinite(r)
    m = methode_sans_talent(forme, part_baisse, *r.shape, rng)
    base = (rng.random(r.shape) >= 1 - m[valide].mean()).astype(float)
    return m, base


def ce_que_le_test_verra(r, poids, part_baisse, forme, methodes=METHODES, graine=GRAINE):
    """
    Les seuils du test pour `methodes` méthodes sans talent (la médiane de
    chacun) : « plan » face à UN tirage au sort à graine fixe (le plan à la
    lettre), « moyenne » face à la moyenne exacte des tirages (proposé),
    « monte » face à « ça monte toujours ».
    `r` : les rendements du lendemain (séances × valeurs, vide = ne compte pas).
    """
    r = np.asarray(r, float)
    valide = np.isfinite(r)
    y = (np.where(valide, r, 0.0) > 0).astype(float)
    seuils = {"plan": [], "moyenne": [], "monte": []}
    for k in range(methodes):
        m, base = _une_methode(r, part_baisse, forme, np.random.default_rng([graine, k]))
        (d_moy, d_monte, d_plan), (b_moy, b_monte, b_plan) = ecarts(m, y, valide, poids, base)
        seuils["moyenne"].append(seuil_critique(d_moy, b_moy))
        seuils["monte"].append(seuil_critique(d_monte, b_monte))
        seuils["plan"].append(seuil_critique(d_plan, b_plan))
    return {cle: float(np.median(v)) for cle, v in seuils.items()}


# ══════════════════════════════════════════════════════════════════
#  LA STRATÉGIE DE 200 €, POUR UNE MÉTHODE QUI A RAISON p FOIS SUR 100
# ══════════════════════════════════════════════════════════════════

def simuler_200(jours, p, chemins=CHEMINS, graine=GRAINE, capital=CAPITAL, ecart=ECART, frais=FRAIS_ORDRE):
    """
    La stratégie de 200 €, un aller-retour par séance, pour une méthode qui
    a raison avec la probabilité p chaque soir :
      - quand elle a raison, elle achète une valeur prise au hasard parmi
        celles qui monteront le lendemain ; si aucune ne monte (un soir de
        krach), elle reste en liquide, sans frais ;
      - quand elle se trompe, elle achète une valeur prise au hasard parmi
        celles qui baisseront ; si toutes montent, celle qu'elle achète
        monte quand même.
    `jours` : pour chaque séance, les rendements du lendemain des valeurs
    où elle choisit. Elle s'arrête quand le capital ne paie plus les deux
    frais (2 € ou moins) et garde ce qui reste. Les hasards sont les mêmes
    quel que soit p ; avoir raison plus souvent finit presque toujours plus
    haut, mais pas toujours près de la ruine : un chemin arrêté garde ses
    quelques euros, un chemin qui a survécu peut tout perdre ensuite.
    Rend les capitaux finaux et la part des soirs (des chemins encore en
    jeu) où elle a eu raison.
    """
    rng = np.random.default_rng(graine)
    k = facteur_ecart(ecart)
    c = np.full(chemins, float(capital))
    justes = soirs = 0
    for r in jours:
        u1, u2 = rng.random(chemins), rng.random(chemins)   # tirés chaque soir, quel que soit p
        if not len(r):
            continue
        hausse, baisse = r[r > 0], r[r <= 0]
        juste = u1 < p
        rr = np.zeros(chemins)
        achete = np.zeros(chemins, bool)
        if len(hausse):
            monte = hausse[np.minimum((u2 * len(hausse)).astype(int), len(hausse) - 1)]
            rr[juste] = monte[juste]
            achete[juste] = True
        if len(baisse):
            rr[~juste] = baisse[np.minimum((u2 * len(baisse)).astype(int), len(baisse) - 1)][~juste]
        else:
            rr[~juste] = monte[~juste]
        achete[~juste] = True
        vivant = c > 2 * frais
        c = np.where(vivant & achete, np.maximum((c - frais) * k * (1 + rr) - frais, 0.0), c)
        raison = np.where(achete, rr > 0, True)              # en liquide : seulement quand rien ne monte
        justes += int((raison & vivant).sum())
        soirs += int(vivant.sum())
    return c, (justes / soirs if soirs else float("nan"))


def garde(rendements, capital=CAPITAL, ecart=ECART, frais=FRAIS_ORDRE):
    """Le capital placé une seule fois (1 € et un demi-écart à l'achat), puis gardé sans rien vendre."""
    return (capital - frais) / (1 + ecart / 2) * float(np.prod(1 + np.asarray(rendements, float)))


def garde_panier(rendements, capital=CAPITAL, ecart=ECART, frais=FRAIS_ORDRE):
    """
    Le panier des valeurs (lignes = séances, colonnes = valeurs), à parts
    égales au départ, acheté une fois (1 € et un demi-écart, comme dans le
    plan) puis gardé sans rien vendre ni rééquilibrer.
    """
    x = np.asarray(rendements, float)
    croissance = np.prod(np.where(np.isfinite(x), 1 + x, 1.0), axis=0)
    return (capital - frais) / (1 + ecart / 2) * float(np.mean(croissance))


def taux_pour_battre(jours, cible, part=0.5, pas=13, **reglages):
    """
    Le taux p, cherché entre 50 % et 100 % par dichotomie (à moins de 0,01
    point près), à partir duquel au moins `part` des chemins finissent à
    `cible` ou plus : 0,5 = une fois sur deux, 0,9 = neuf fois sur dix.
    Rend {"p", "juste" : la part des soirs où elle a vraiment eu raison,
    "ruines" : la part des chemins arrêtés à 2 € ou moins} au taux trouvé ;
    p = 0,5 si la moitié suffit déjà ; None si même p = 1 ne suffit pas.
    """
    frais = reglages.get("frais", FRAIS_ORDRE)

    def essai(p):
        c, juste = simuler_200(jours, p, **reglages)
        return float(np.mean(c >= cible)) >= part, juste, float(np.mean(c <= 2 * frais))

    if not essai(1.0)[0]:
        return None
    bas, haut = 0.5, 1.0
    if essai(bas)[0]:
        haut = bas
    else:
        for _ in range(pas):
            milieu = (bas + haut) / 2
            if essai(milieu)[0]:
                haut = milieu
            else:
                bas = milieu
    _, juste, ruines = essai(haut)
    return {"p": haut, "juste": juste, "ruines": ruines}


# ══════════════════════════════════════════════════════════════════
#  LE MUR DES FRAIS
# ══════════════════════════════════════════════════════════════════

def mur_des_frais(dossier=MARCHE, tickers=None, annees=ANNEES_NOTEES, tirages=TIRAGES,
                  bloc=BLOC, methodes=METHODES, chemins=CHEMINS, graine=GRAINE):
    R, spy, vol, agitees = _periode_notee(dossier, tickers, annees)
    debut, fin = annees[0][0], annees[-1][1]
    pool_agite = R.where(agitees)
    noms = [_nom_annee(a, b) for a, b in annees]
    rap = {"debut": R.index[0], "fin": R.index[-1], "seances": len(R), "valeurs": R.shape[1],
           "annees": noms, "duree": len(annees),
           "reglages": {"tirages": tirages, "bloc": bloc, "methodes": methodes, "chemins": chemins}}

    # 1. Un aller-retour.
    rap["aller_retour"] = {(c, e): cout_aller_retour(c, e) for c in CAPITAUX for e in (ECART, ECART_CONTROLE)}

    # 2. Ce qu'il faudrait gagner.
    rap["necessaire"] = {(c, e): rendement_necessaire(c, e) for c in CAPITAUX for e in (ECART, ECART_CONTROLE)}
    rap["moyenne_valeur"] = float(np.nanmean(R.to_numpy(float)))
    rap["plus_forte_hausse"] = float(np.mean(np.nanmax(R.to_numpy(float), axis=1)))   # la meilleure des 49, chaque soir
    rap["epuise"] = seances_jusqu_a(CAPITAL, 2 * FRAIS_ORDRE)
    rap["moitie"] = seances_jusqu_a(CAPITAL, CAPITAL / 2)

    # 3. Le taux de bonnes réponses qu'il faudrait : en moyenne (la formule),
    #    puis la stratégie simulée face au meilleur de SPY et du panier des 49
    #    gardés (le critère 2 du plan), sur toute la période et année par
    #    année (200 € remis chaque mois de novembre).
    def jours_de(pool):
        return [ligne[np.isfinite(ligne)] for ligne in pool.to_numpy(float)]

    def cible(a, b):
        return max(garde(spy.loc[a:b]), garde_panier(R.loc[a:b]))

    rap["spy_garde"], rap["panier_garde"] = garde(spy), garde_panier(R)
    rap["cibles"] = {TOUT: cible(debut, fin), **{nom_a: cible(a, b) for nom_a, (a, b) in zip(noms, annees)}}
    rap["taux"] = {}
    for nom, pool in (("toutes", R), ("agitees", pool_agite)):
        hausse, baisse = hausse_et_baisse(pool)
        jours = jours_de(pool)
        rap["taux"][nom] = {
            "hausse": hausse, "baisse": baisse, "part_hausses": part_de_hausses(pool)[0],
            "ne_pas_perdre": taux_necessaire(hausse, baisse, rendement_necessaire(CAPITAL)),
            "ne_pas_perdre_controle": taux_necessaire(hausse, baisse, rendement_necessaire(CAPITAL, ECART_CONTROLE)),
            "ne_pas_perdre_50": taux_necessaire(hausse, baisse, rendement_necessaire(50.0)),
            "simule": {part: taux_pour_battre(jours, rap["cibles"][TOUT], part, chemins=chemins, graine=graine)
                       for part in PARTS_CHEMINS},
            "par_annee": {nom_a: taux_pour_battre(jours_de(pool.loc[a:b]), rap["cibles"][nom_a], 0.5,
                                                  chemins=chemins, graine=graine)
                          for nom_a, (a, b) in zip(noms, annees)},
        }
    # Le taux qui suffit juste pour ne pas être ruiné une fois sur deux (finir
    # au-dessus de 2 €) : s'il égale celui de la cible, c'est la ruine qui le fixe.
    rap["taux"]["toutes"]["survie"] = taux_pour_battre(jours_de(R), 2 * FRAIS_ORDRE + 1e-9, 0.5,
                                                       chemins=chemins, graine=graine)
    # Le seuil de la relecture du plan, pour comparer : les 10 % de valeurs-jours
    # les plus agitées de TOUTE la période (un seuil fixé après coup).
    v, x = vol.to_numpy(float), R.to_numpy(float)
    ok = np.isfinite(v) & np.isfinite(x)
    seuil = np.quantile(v[ok], 1 - PART_AGITEES)
    rap["taux_seuil_global"] = taux_necessaire(*hausse_et_baisse(x[ok & (v >= seuil)]), rendement_necessaire(CAPITAL))

    # 4. Ce que le test de l'étape 1 pourra voir : sur toute la période, puis
    #    année par année. « avance » : ce que « ça monte toujours » gagne
    #    d'avance sur une méthode sans talent, f × (2 × part des hausses − 1).
    periodes = [(TOUT, debut, fin)] + [(nom_a, a, b) for nom_a, (a, b) in zip(noms, annees)]
    rap["visible"] = {}
    for i, (nom, a, b) in enumerate(periodes):
        bloc_r = R.loc[a:b].to_numpy(float)
        poids = poids_blocs(len(bloc_r), bloc, tirages, graine + i)
        q = part_de_hausses(bloc_r)[0]
        for j, forme in enumerate(FORMES):
            for l, f in enumerate(PARTS_BAISSE):
                seuils = ce_que_le_test_verra(bloc_r, poids, f, forme, methodes,
                                              graine * 1000 + 100 * i + 10 * j + l)
                rap["visible"][(nom, forme, f)] = dict(seuils, avance=f * (2 * q - 1))

    # 5. « Ça monte toujours », année par année.
    rap["monte"] = {nom_a: part_de_hausses(R.loc[a:b]) for nom_a, (a, b) in zip(noms, annees)}
    rap["monte"][TOUT] = part_de_hausses(R)
    return rap


def fausses_alarmes(dossier=MARCHE, tickers=None, annees=ANNEES_NOTEES, methodes=FAUSSES_ALARMES_METHODES,
                    tirages=TIRAGES, bloc=BLOC, graine=GRAINE + 1, paquets=PAQUETS_BLOCS):
    """
    Combien de fois le test dit « vu » pour des méthodes SANS talent, sur les
    vrais lendemains d'avant le coffre : à 99 %, il devrait le dire 1 fois sur
    100. Face au tirage au sort du plan et face à la moyenne des tirages
    (face à « ça monte toujours », une méthode sans talent part en retard :
    rien à mesurer ainsi). Rend {(période, forme, f): {"plan", "moyenne" : la part
    des méthodes vues, "marges" : pour chaque méthode, écart − seuil face à la
    moyenne des tirages puis face au tirage du plan (vue si la marge est > 0)}}.
    La méthode k utilise le tirage des blocs k % `paquets` de sa période : une
    seule matrice de blocs par période ferait trop varier le résultat.
    """
    R, *_ = _periode_notee(dossier, tickers, annees)
    periodes = [(TOUT, annees[0][0], annees[-1][1])] + [(_nom_annee(a, b), a, b) for a, b in annees]
    res = {}
    for i, (nom, a, b) in enumerate(periodes):
        r = R.loc[a:b].to_numpy(float)
        valide = np.isfinite(r)
        y = (np.where(valide, r, 0.0) > 0).astype(float)
        marges = {(j, l): np.empty((methodes, 2)) for j in range(len(FORMES)) for l in range(len(PARTS_BAISSE))}
        for g in range(paquets):
            poids = poids_blocs(len(r), bloc, tirages, graine + 100 * i + g)
            for j, forme in enumerate(FORMES):
                for l, f in enumerate(PARTS_BAISSE):
                    for k in range(g, methodes, paquets):
                        m, base = _une_methode(r, f, forme, np.random.default_rng([graine, i, j, l, k]))
                        (d_moy, _, d_plan), (b_moy, _, b_plan) = ecarts(m, y, valide, poids, base)
                        marges[(j, l)][k] = (d_moy - seuil_critique(d_moy, b_moy),
                                             d_plan - seuil_critique(d_plan, b_plan))
        for (j, l), mg in marges.items():
            res[(nom, list(FORMES)[j], PARTS_BAISSE[l])] = {"moyenne": float(np.mean(mg[:, 0] > 0)),
                                                            "plan": float(np.mean(mg[:, 1] > 0)), "marges": mg}
    return res


# ══════════════════════════════════════════════════════════════════
#  L'AFFICHAGE
# ══════════════════════════════════════════════════════════════════

def _nb(x, dec=2):
    return f"{x:,.{dec}f}".replace(",", " ").replace(".", ",")


def _pct(x, dec=2, signe=False):
    s = f"{x * 100:+.{dec}f}" if signe else f"{x * 100:.{dec}f}"
    return s.replace(".", ",").replace("-", "−") + " %"


def _pts(x, dec=1):
    """Des points de pourcentage : singulier sous 2, comme le veut l'usage (« 1,5 point »)."""
    v = round(x * 100, dec)
    return f"{v:.{dec}f}".replace(".", ",").replace("-", "−") + (" point" if abs(v) < 2 else " points")


def _date(d):
    return d.strftime("%d/%m/%Y")


def _simule(x):
    """Un taux simulé : la part des soirs justes au seuil trouvé, ou « impossible »."""
    return "impossible" if x is None else _pct(x["juste"], 0)


def _fourchette(valeurs):
    bas, haut = _pts(min(valeurs)), _pts(max(valeurs))
    return f"{bas} chaque année" if bas == haut else f"de {bas} à {haut}"


def afficher(rap):
    e, ec = ECART, ECART_CONTROLE
    reg = rap["reglages"]
    duree = f"{rap['duree']} ans"
    annees = rap["annees"]
    nb_agitees = math.ceil(PART_AGITEES * rap["valeurs"])
    print("LE MUR DES FRAIS — étape 0 du banc des prévisions")
    print(f"Données : {rap['valeurs']} valeurs et SPY, en euros, dividendes compris (cache lu sans rien écrire).")
    print(f"Coupé au {_date(FIN_AVANT_COFFRE)} : le coffre (nov. 2025 → nov. 2026) n'est pas lu.")
    print(f"Séances notées : du {_date(rap['debut'])} au {_date(rap['fin'])} ({_nb(rap['seances'], 0)} soirs).")

    print("\n1. UN ALLER-RETOUR CHEZ LE COURTIER (acheter le soir, revendre le lendemain, cours inchangé)")
    print("   1 € par ordre, plus un demi-écart à l'achat et un demi-écart à la vente.")
    print(f"   {'':8}{'écart ' + _pct(e, 1):>24}{'contrôle ' + _pct(ec, 1):>26}")
    for c in CAPITAUX:
        a, b = rap["aller_retour"][(c, e)], rap["aller_retour"][(c, ec)]
        print(f"   {_nb(c, 0) + ' €':8}{_nb(a) + ' € soit ' + _pct(a / c):>24}{_nb(b) + ' € soit ' + _pct(b / c):>26}")

    print(f"\n2. CE QU'IL FAUDRAIT GAGNER, avec {_nb(CAPITAL, 0)} € et un aller-retour par séance")
    n200, n50 = rap["necessaire"][(CAPITAL, e)], rap["necessaire"][(50.0, e)]
    print(f"   Pour ne pas perdre : {_pct(n200, 2, True)} par séance, brut "
          f"(contrôle : {_pct(rap['necessaire'][(CAPITAL, ec)], 2, True)} ; avec 50 € : {_pct(n50, 2, True)}).")
    moy = rap["moyenne_valeur"]
    print(f"   Une des {rap['valeurs']} valeurs a rapporté en moyenne {_pct(moy, 3, True)} par séance"
          + (f" : {_nb(n200 / moy, 0)} fois moins." if moy > 0 else "."))
    n, reste = rap["epuise"]
    print(f"   À rendement nul, les frais vident les {_nb(CAPITAL, 0)} € en {n} séances "
          f"(il reste {_nb(reste)} €) ; la moitié est partie en {rap['moitie'][0]} séances.")

    print(f"\n3. LE TAUX DE BONNES RÉPONSES QU'IL FAUDRAIT, avec {_nb(CAPITAL, 0)} € "
          "(la valeur achetée le soir monte le lendemain)")
    print(f"   {'':33}{'si elle monte':>14}{'si elle baisse':>16}{'pour ne pas':>13}"
          f"{'finir devant (' + duree + ', simulé)':>32}")
    print(f"   {'':33}{'(moyenne)':>14}{'(moyenne)':>16}{'perdre':>13}{'1 fois sur 2':>16}{'9 fois sur 10':>16}")
    noms = {"toutes": f"parmi les {rap['valeurs']} valeurs",
            "agitees": f"parmi les {nb_agitees} plus agitées du soir"}
    for nom, t in rap["taux"].items():
        print(f"   {noms[nom]:33}{_pct(t['hausse'], 2, True):>14}{_pct(-t['baisse'], 2, True):>16}"
              f"{_pct(t['ne_pas_perdre'], 0):>13}{_simule(t['simule'][0.5]):>16}{_simule(t['simule'][0.9]):>16}")
    t, ta = rap["taux"]["toutes"], rap["taux"]["agitees"]
    print("   « Pour ne pas perdre » : en moyenne, par aller-retour (au contrôle 0,3 % : "
          f"{_pct(t['ne_pas_perdre_controle'], 0)} et {_pct(ta['ne_pas_perdre_controle'], 0)}).")
    print(f"   « Pour finir devant » : au-dessus de {_nb(CAPITAL, 0)} € gardés dans SPY ({_nb(rap['spy_garde'], 0)} € "
          f"au bout des {duree}) ET dans le panier des {rap['valeurs']} ({_nb(rap['panier_garde'], 0)} €), "
          f"sur {_nb(reg['chemins'], 0)} chemins tirés.")
    print("   Un soir où rien ne monte, avoir raison, c'est rester en liquide ; « impossible » : "
          "même en ayant toujours raison.")
    moitie, survie = t["simule"][0.5], t.get("survie")
    if moitie is not None:
        phrase = (f"   Au taux « 1 fois sur 2 », {_pct(moitie['ruines'], 0)} des chemins sont ruinés "
                  f"(parmi les {rap['valeurs']} valeurs)")
        if survie is not None and abs(survie["p"] - moitie["p"]) < 0.005:
            phrase += " : sur la durée, c'est la ruine qui fixe ce taux, le même suffit juste pour ne pas être ruiné."
        elif survie is not None:
            phrase += f" ; pour ne pas être ruiné 1 fois sur 2, il suffirait de {_simule(survie)}."
        else:
            phrase += "."
        print(phrase)
    for nom, t2 in rap["taux"].items():
        print(f"   Année par année ({_nb(CAPITAL, 0)} € remis chaque novembre, 1 fois sur 2), {noms[nom]} : "
              + ", ".join(f"{k} {_simule(x)}" for k, x in t2["par_annee"].items()) + ".")
    print(f"   Avec le seuil de la relecture du plan (les 10 % de valeurs-jours les plus agitées de toute la "
          f"période, fixé après coup) : {_pct(rap['taux_seuil_global'], 0)} pour ne pas perdre.")
    print(f"   Pour comparer : « ça monte toujours » a raison {_pct(t['part_hausses'], 1)} du temps "
          f"(parmi les plus agitées : {_pct(ta['part_hausses'], 1)}).")
    if t["ne_pas_perdre_50"] > 1:
        meilleure = rap["plus_forte_hausse"]
        print(f"   Avec 50 € : il faudrait {_pct(n50, 2, True)} par séance ; même en ayant toujours raison sur le sens, "
              f"une valeur qui monte rapporte {_pct(t['hausse'], 2, True)} en moyenne"
              + (f". Seule la plus forte hausse des {rap['valeurs']} de chaque soir fait mieux en moyenne "
                 f"({_pct(meilleure, 2, True)}) : il faudrait la deviner." if meilleure > n50 else
                 f", et même la plus forte hausse de chaque soir ({_pct(meilleure, 2, True)}) ne suffit pas."))

    print(f"\n4. CE QUE LE TEST DE L'ÉTAPE 1 POURRA VOIR ({_pct(NIVEAU, 0)}, bootstrap par blocs de "
          f"{reg['bloc']} séances, {_nb(reg['tirages'], 0)} tirages)")
    print("   Le plus petit écart de bonnes réponses OBSERVÉ (sur toutes les valeurs-jours) qu'il déclarera « vu »,")
    print(f"   pour une méthode sans talent (médiane de {reg['methodes']} méthodes tirées ; "
          f"« 1 an » : la médiane des {len(annees)} années).")
    print(f"   {'':37}{'face au tirage au sort':>24}{'face à « ça monte toujours »':>30}")
    print(f"   {'':37}{'1 an':>12}{duree:>12}{'1 an':>15}{duree:>15}")

    def un_an(forme, f, cle):
        return float(np.median([rap["visible"][(a, forme, f)][cle] for a in annees]))

    for forme, nom in FORMES.items():
        for f in PARTS_BAISSE:
            tout = rap["visible"][(TOUT, forme, f)]
            print(f"   {'« baisse » ' + _pct(f, 0) + ', ' + nom:37}{_pts(un_an(forme, f, 'plan')):>12}"
                  f"{_pts(tout['plan']):>12}{_pts(un_an(forme, f, 'monte')):>15}{_pts(tout['monte']):>15}")
    print("   Le tirage au sort est celui du plan : UN tirage à graine fixe, qui dit « baisse » aussi souvent "
          "que la méthode, valeur par valeur.")
    print("   Proposé, à décider avant l'étape 1 : le compter par la moyenne exacte de tous les tirages "
          "(sans son propre bruit). Le test verrait alors :")
    for forme, nom in FORMES.items():
        for f in PARTS_BAISSE:
            tout = rap["visible"][(TOUT, forme, f)]
            print(f"     {nom}, « baisse » {_pct(f, 0)} : {_pts(un_an(forme, f, 'moyenne'))} sur 1 an et "
                  f"{_pts(tout['moyenne'])} sur {duree} (au lieu de {_pts(un_an(forme, f, 'plan'))} "
                  f"et {_pts(tout['plan'])}).")
    print("   Sur 1 an, selon l'année (tirage au sort du plan ; « ça monte toujours ») :")
    for forme, nom in FORMES.items():
        for f in PARTS_BAISSE:
            un = [rap["visible"][(a, forme, f)] for a in annees]
            print(f"     {nom}, « baisse » {_pct(f, 0)} : {_fourchette([x['plan'] for x in un])} ; "
                  f"{_fourchette([x['monte'] for x in un])}.")
    print(f"   Ces seuils portent sur l'écart OBSERVÉ : une méthode dont l'écart vrai vaut le seuil n'est vue "
          f"qu'une fois sur deux ; pour l'être {_nb(PUISSANCE * 10, 0)} fois sur 10, il lui faut environ "
          f"{_nb(facteur_puissance(), 2)} fois le seuil.")
    print("   Sur 1 an, le test est un peu indulgent : sans talent, il dit « vu » un peu plus d'une fois sur 100 "
          "(mesure : python3 banc.py --fausses-alarmes).")
    for f in PARTS_BAISSE:
        avance = rap["visible"][(TOUT, "valeur", f)]["avance"]
        print(f"   Sans talent, dire « baisse » {_pct(f, 0)} du temps part avec {_pts(abs(avance))} "
              f"{'de retard sur' if avance >= 0 else 'd’avance sur'} « ça monte toujours » ({duree}).")

    print("\n5. « ÇA MONTE TOUJOURS », ANNÉE PAR ANNÉE (de novembre à octobre)")
    for nom, (q, n) in rap["monte"].items():
        print(f"   {(duree if nom == TOUT else nom):10}{_pct(q, 1):>9}   ({_nb(n, 0)} valeurs-jours)")
    print("\nDes faits sur le passé, pas une prévision ni un conseil.")


def afficher_fausses_alarmes(res, methodes):
    annees = sorted({p for p, _, _ in res if p != TOUT})
    print(f"LES FAUSSES ALARMES DU TEST ({_pct(NIVEAU, 0)}) : des méthodes SANS talent, sur les vrais lendemains "
          f"d'avant le coffre ({_nb(methodes, 0)} par cas et par période, sur {PAQUETS_BLOCS} tirages des blocs). "
          "Attendu : environ 1 %.")
    print(f"   {'':37}{'tirage du plan':>30}{'moyenne des tirages':>30}")
    print(f"   {'':37}{'1 an':>15}{f'{len(annees)} ans':>15}{'1 an':>15}{f'{len(annees)} ans':>15}")
    for forme, nom in FORMES.items():
        for f in PARTS_BAISSE:
            cellule = []
            for cle in ("plan", "moyenne"):
                un = float(np.mean([res[(a, forme, f)][cle] for a in annees]))
                cellule.append(f"{_pct(un, 1):>15}{_pct(res[(TOUT, forme, f)][cle], 1):>15}")
            print(f"   {'« baisse » ' + _pct(f, 0) + ', ' + nom:37}{''.join(cellule)}")
    print(f"   « 1 an » : la moyenne des {len(annees)} années ({_nb(methodes * len(annees), 0)} méthodes par case).")


# ══════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--frais", action="store_true", help="le mur des frais, sur le cache coupé avant le coffre")
    p.add_argument("--verifier", action="store_true", help="les cas calculés à la main")
    p.add_argument("--fausses-alarmes", action="store_true",
                   help="combien de fois le test dit « vu » sans talent (environ 1 min)")
    args = p.parse_args()
    if args.verifier:
        sys.exit(0 if verifier() else 1)
    try:
        if args.frais:
            afficher(mur_des_frais())
            return
        if args.fausses_alarmes:
            afficher_fausses_alarmes(fausses_alarmes(), FAUSSES_ALARMES_METHODES)
            return
    except (CacheInvalide, FileNotFoundError) as erreur:
        sys.exit(f"Rien n'est calculé : {erreur}")
    p.print_help()


# ══════════════════════════════════════════════════════════════════
#  LES VÉRIFICATIONS (des cas calculés à la main, sans le vrai cache)
# ══════════════════════════════════════════════════════════════════

def _ecrire_cache(dossier, jours, cours, ouverture, cloture):
    """Un faux cache au format de data/market, dans un dossier jetable (jamais data/market)."""
    if os.path.abspath(dossier) == os.path.abspath(MARCHE):
        raise RuntimeError("les vérifications n'écrivent jamais dans data/market")
    idx = pd.Index(jours, name="Date")
    for t, v in cours.items():
        pd.DataFrame({"Close": v, "High": v, "Low": v, "Open": v, "Volume": 1000},
                     index=idx).to_csv(os.path.join(dossier, f"{t}_10y.csv"))
    pd.DataFrame({"Close": cloture, "High": cloture, "Low": ouverture, "Open": ouverture, "Volume": 0},
                 index=idx).to_csv(os.path.join(dossier, f"{CHANGE}_10y.csv"))


def _empreinte(dossier):
    """Le contenu et la date de chaque fichier : pour voir si un calcul y a écrit."""
    return {f: (hashlib.sha256(open(os.path.join(dossier, f), "rb").read()).hexdigest(),
                os.stat(os.path.join(dossier, f)).st_mtime_ns) for f in sorted(os.listdir(dossier))}


def _egaux(a, b):
    """Deux rapports identiques, au bit près (deux NaN sont égaux)."""
    if isinstance(a, dict):
        return isinstance(b, dict) and a.keys() == b.keys() and all(_egaux(a[x], b[x]) for x in a)
    if isinstance(a, (list, tuple)):
        return isinstance(b, (list, tuple)) and len(a) == len(b) and all(_egaux(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return bool(a == b)


def _refuse(fonction, *args, **kwargs):
    """Le message d'erreur si `fonction` refuse le cache (CacheInvalide), sinon None."""
    try:
        fonction(*args, **kwargs)
    except CacheInvalide as erreur:
        return str(erreur)
    return None


def verifier():
    cas = []

    def ok(nom, condition):
        cas.append(bool(condition))
        print(("✓ " if condition else "✗ ") + nom)

    def proche(a, b, tol=1e-9):
        return a is not None and b is not None and abs(a - b) <= tol

    k = (1 - 0.0005) / (1 + 0.0005)          # ce qui reste après les deux demi-écarts de 0,05 %

    print("— Les frais —")
    ok("un demi-écart de chaque côté : il reste 0,9995 / 1,0005 de l'argent placé",
       proche(facteur_ecart(0.001), 0.9995 / 1.0005, 1e-15))
    ok("aller-retour de 200 € : 2 € + 199 € × (1 − 0,9995/1,0005) = 2,19890 €",
       proche(cout_aller_retour(200.0), 2.1989005497251374, 1e-12))
    ok("aller-retour de 50 € : 2,04898 €", proche(cout_aller_retour(50.0), 2.0489755122438781, 1e-12))
    cout_fourchette = 50 - (49 * 79.95 / 80.05 - 1)   # inventé : acheter 49 € à 80,05 €, revendre à 79,95 €
    ok(f"une fourchette inventée (79,95 / 80,05 €) donne le même coût que la formule ({_nb(cout_fourchette, 5)} €)",
       proche(cout_aller_retour(50.0, 0.10 / 80.00), cout_fourchette, 1e-9))
    ok("l'écart n'est pas compté deux fois : 1 000 € placés perdent 0,1 % (0,9995 €), pas 0,2 %",
       proche(cout_aller_retour(1001.0) - 2, 1000 * 0.001 / 1.0005, 1e-9))
    ok("sans écart, un aller-retour coûte les deux ordres : 2 €", proche(cout_aller_retour(200.0, 0.0), 2.0, 1e-12))
    ok("pour ne pas perdre avec 200 € : +1,10608 % (201 / (199 × 0,9995/1,0005) − 1)",
       proche(rendement_necessaire(200.0), 0.011060806785302199, 1e-12))
    ok("… et avec ce rendement, l'aller-retour rend exactement 200 €",
       proche(apres_aller_retour(200.0, rendement_necessaire(200.0)), 200.0, 1e-9))
    ok("pour ne pas perdre avec 50 € : +4,18577 %", proche(rendement_necessaire(50.0), 0.04185766352564037, 1e-12))
    ok("sans écart : 2 / 199 = +1,005 %", proche(rendement_necessaire(200.0, 0.0), 2 / 199, 1e-15))
    ok("sans écart, pour finir 1 % au-dessus : 4 / 199",
       proche(rendement_necessaire(200.0, 0.0, objectif=0.01), 4 / 199, 1e-15))

    print("\n— L'épuisement du capital —")
    ok("sans écart, 200 € perdent 2 € par séance : 99 séances, il reste 2 €",
       seances_jusqu_a(200.0, 2.0, 0.0) == (99, 2.0))
    n, reste = seances_jusqu_a(200.0, 2.0)
    # Forme fermée : C(n) = −1/h + (200 + 1/h) × k^n, avec h = 0,0005 (un demi-écart).
    ok(f"avec l'écart : 95 séances, il reste {_nb(reste, 4)} € (forme fermée : 0,6204 €)",
       n == 95 and proche(reste, -2000 + 2200 * k ** 95, 1e-9) and proche(reste, 0.62043999186, 1e-9))
    ok("la moitié des 200 € est partie en 47 séances (ln(21/22) / ln k = 46,5)",
       seances_jusqu_a(200.0, 100.0)[0] == 47)
    ok("2 € ne paient pas un aller-retour : 0 séance", seances_jusqu_a(2.0, 2.0) == (0, 2.0))

    print("\n— Le taux de bonnes réponses qu'il faudrait —")
    ok("hausse 3 %, baisse 2 %, objectif 1 % : (1 + 2) / (3 + 2) = 60 %",
       proche(taux_necessaire(0.03, 0.02, 0.01), 0.6, 1e-15))
    h, b = hausse_et_baisse([0.02, 0.04, -0.01, -0.03, 0.0, float("nan")])
    ok("petit jeu (+2, +4, −1, −3, 0 %, un vide) : hausse moyenne 3 %, baisse moyenne 4/3 % (0 = une baisse nulle)",
       proche(h, 0.03, 1e-15) and proche(b, 0.04 / 3, 1e-15))
    ok("… taux pour ne pas perdre avec 200 € : 56,294 %",
       proche(taux_necessaire(h, b, rendement_necessaire(200.0)), 0.56294169504543536, 1e-12))
    ok("… avec 50 € : 127 %, au-dessus de 100 % (impossible)",
       proche(taux_necessaire(h, b, rendement_necessaire(50.0)), 1.2736383890532393, 1e-12))
    ok("« ça monte toujours » : 2 hausses sur 4 (0 n'est pas une hausse, le vide ne compte pas)",
       part_de_hausses([0.01, 0.0, -0.01, 0.02, float("nan")]) == (0.5, 4))
    ok("un taux simulé s'affiche par la part des soirs justes (pas le p de la dichotomie), ou « impossible »",
       _simule({"p": 0.7, "juste": 0.75, "ruines": 0.1}) == "75 %" and _simule(None) == "impossible")
    ok("les fourchettes « selon l'année » : du plus petit au plus grand, ou « chaque année » s'ils s'affichent pareil",
       _fourchette([0.015, 0.011, 0.013]) == "de 1,1 point à 1,5 point"
       and _fourchette([0.011, 0.0112]) == "1,1 point chaque année")

    print("\n— Les données —")
    jours = pd.DatetimeIndex(["2025-10-03", "2025-10-06", "2025-10-07", "2025-10-08",
                              "2025-10-09", "2025-10-10", "2025-10-16", "2025-10-22"])
    eur = en_euros(pd.DataFrame({"X": [70.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 80.0]}, index=jours),
                   pd.Series([1.0, 2.0, 4.0, 5.0, 6.0, 8.0], index=pd.DatetimeIndex(
                       ["2025-10-06", "2025-10-07", "2025-10-09", "2025-10-13", "2025-10-21", "2025-10-29"])))["X"]
    ok("euros : clôture du lundi ÷ ouverture du mardi (10 / 2)", proche(eur.iloc[1], 5.0))
    ok("euros : mardi → ouverture du jeudi quand Yahoo n'a pas le mercredi (20 / 4)", proche(eur.iloc[2], 5.0))
    ok("euros : mercredi → jeudi (30 / 4)", proche(eur.iloc[3], 7.5))
    ok("euros : jeudi → lundi, 4 jours plus tard (40 / 5) ; vendredi → lundi (50 / 5)",
       proche(eur.iloc[4], 8.0) and proche(eur.iloc[5], 10.0))
    ok("euros : jeudi → mardi, 5 jours plus tard, comme à Pâques (60 / 6)", proche(eur.iloc[6], 10.0))
    ok("euros : vide quand la ligne de change suivante est à 7 jours", math.isnan(eur.iloc[7]))
    ok("euros : vide avant le début du change (jamais de bfill)", math.isnan(eur.iloc[0]))
    rl = rendements_du_lendemain(pd.Series([100.0, 110.0, 99.0]))
    ok("rendement du lendemain, rangé au soir où l'on décide : +10 %, −10 %, puis vide",
       proche(rl.iloc[0], 0.10, 1e-12) and proche(rl.iloc[1], -0.10, 1e-12) and math.isnan(rl.iloc[2]))
    prix = pd.DataFrame({"X": 100 * np.concatenate([[1.0], np.cumprod(1 + np.array([0.01, -0.01] * 12))])})
    vol = agitation(prix)["X"]
    ok("agitation : ±1 % en alternance → écart-type 1 % × √(20/19), rien avant 20 variations",
       proche(vol.iloc[20], 0.01 * math.sqrt(20 / 19), 1e-12) and vol.iloc[:20].isna().all())
    prix2 = prix.copy()
    prix2.iloc[-1] *= 1.5
    ok("agitation : changer le dernier cours ne change aucun soir d'avant (connue le soir même)",
       agitation(prix2)["X"].iloc[:-1].equals(vol.iloc[:-1]))
    v49 = pd.DataFrame([np.arange(1, 50, dtype=float)], columns=[f"V{i}" for i in range(49)])
    choix = plus_agitees(v49).iloc[0]
    ok("les plus agitées : 5 sur 49, celles qui ont les 5 plus grandes volatilités",
       choix.sum() == 5 and choix.iloc[-5:].all())
    ok("… 1 sur 10, 2 sur 20", plus_agitees(v49.iloc[:, :10]).iloc[0].sum() == 1
       and plus_agitees(v49.iloc[:, :20]).iloc[0].sum() == 2)
    v40 = v49.copy()
    v40.iloc[0, :9] = np.nan
    ok("… une valeur sans volatilité ne compte pas : 40 valeurs → 4", plus_agitees(v40).iloc[0].sum() == 4)

    print("\n— Le cache : lu sans rien écrire, coupé avant le coffre —")
    with tempfile.TemporaryDirectory() as dossier:
        rng = np.random.default_rng(1)
        jours = pd.bdate_range("2023-10-02", "2025-12-31")
        tickers = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
        cours = {t: 50 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, len(jours)))) for t in tickers + [REFERENCE]}
        ouverture = 1.10 * np.exp(np.cumsum(rng.normal(0, 0.004, len(jours))))
        _ecrire_cache(dossier, jours, cours, ouverture, ouverture * 1.01)
        lus, change = charger(dossier, tickers)
        ok("le change pris est l'OUVERTURE, pas la « clôture » (une photo du matin chez Yahoo)",
           np.allclose(change.to_numpy(), ouverture[:len(change)], rtol=1e-12, atol=0))
        ok("rien après le 31/10/2025 n'est gardé à la lecture",
           lus.index.max() <= FIN_AVANT_COFFRE and change.index.max() <= FIN_AVANT_COFFRE)
        empreinte = _empreinte(dossier)
        annees = ((pd.Timestamp("2023-11-01"), pd.Timestamp("2024-10-31")),
                  (pd.Timestamp("2024-11-01"), pd.Timestamp("2025-10-31")))
        reglages = dict(tickers=tickers, annees=annees, methodes=3, chemins=60)
        avant = mur_des_frais(dossier, **reglages)
        ok("le calcul n'écrit rien dans le cache (même contenu, même date pour chaque fichier)",
           _empreinte(dossier) == empreinte)
        ok("la dernière séance notée est le 29/10/2025 : le change du 03/11 est déjà dans le coffre",
           avant["fin"] == pd.Timestamp("2025-10-29"))
        # L'assemblage : chaque chiffre du rapport, recalculé à part avec les fonctions vérifiées ici.
        r_l = rendements_du_lendemain(en_euros(lus, change))
        R_l = r_l.loc[annees[0][0]:annees[-1][1], tickers].dropna(how="all")
        spy_l = r_l.loc[R_l.index, REFERENCE]
        vol_l = agitation(lus[tickers]).reindex(R_l.index)
        agit = plus_agitees(agitation(lus[tickers])).reindex(R_l.index).fillna(False).astype(bool)
        an1 = R_l.loc[annees[0][0]:annees[0][1]]

        def jours_de(x):
            return [ligne[np.isfinite(ligne)] for ligne in np.asarray(x, float)]

        cible_tout = max(garde(spy_l), garde_panier(R_l))
        cible_an1 = max(garde(spy_l.loc[an1.index]), garde_panier(an1))
        ok("assemblage : séances notées, moyenne d'une valeur, plus forte hausse du soir, « ça monte » "
           "(en tout et par année), SPY et panier gardés",
           avant["seances"] == len(R_l) and avant["moyenne_valeur"] == float(np.nanmean(R_l.to_numpy(float)))
           and avant["plus_forte_hausse"] == float(np.mean(np.nanmax(R_l.to_numpy(float), axis=1)))
           and avant["monte"][TOUT] == part_de_hausses(R_l) and avant["monte"]["2023-2024"] == part_de_hausses(an1)
           and avant["spy_garde"] == garde(spy_l) and avant["panier_garde"] == garde_panier(R_l))
        ok("assemblage : les plus agitées = 1 valeur sur 6 chaque soir, avec leur hausse et leur baisse moyennes",
           agit.sum(axis=1).eq(1).all() and _egaux((avant["taux"]["agitees"]["hausse"], avant["taux"]["agitees"]["baisse"]),
                                                    hausse_et_baisse(R_l.where(agit))))
        h_l, b_l = hausse_et_baisse(R_l)
        ok("assemblage : taux pour ne pas perdre à 0,1 %, au contrôle 0,3 % et avec 50 €",
           avant["taux"]["toutes"]["ne_pas_perdre"] == taux_necessaire(h_l, b_l, rendement_necessaire(CAPITAL))
           and avant["taux"]["toutes"]["ne_pas_perdre_controle"]
           == taux_necessaire(h_l, b_l, rendement_necessaire(CAPITAL, ECART_CONTROLE))
           and avant["taux"]["toutes"]["ne_pas_perdre_50"] == taux_necessaire(h_l, b_l, rendement_necessaire(50.0)))
        ok("assemblage : la stratégie simulée face au MEILLEUR de SPY et du panier, 1 fois sur 2 et 9 fois sur 10",
           all(_egaux(avant["taux"]["toutes"]["simule"][part],
                      taux_pour_battre(jours_de(R_l), cible_tout, part, chemins=60, graine=GRAINE))
               for part in PARTS_CHEMINS))
        ok("assemblage : année par année, face au meilleur de SPY et du panier CETTE année-là",
           avant["cibles"] == {TOUT: cible_tout, "2023-2024": cible_an1,
                               "2024-2025": max(garde(spy_l.loc[annees[1][0]:annees[1][1]]),
                                                garde_panier(R_l.loc[annees[1][0]:annees[1][1]]))}
           and _egaux(avant["taux"]["toutes"]["par_annee"]["2023-2024"],
                      taux_pour_battre(jours_de(an1), cible_an1, 0.5, chemins=60, graine=GRAINE)))
        v_l, x_l = vol_l.to_numpy(float), R_l.to_numpy(float)
        ok_l = np.isfinite(v_l) & np.isfinite(x_l)
        haut_l = ok_l & (v_l >= np.quantile(v_l[ok_l], 0.9))
        ok("assemblage : le seuil global de la relecture (les 10 % de valeurs-jours les plus agitées de la période)",
           avant["taux_seuil_global"] == taux_necessaire(*hausse_et_baisse(x_l[haut_l]), rendement_necessaire(CAPITAL)))
        ok("assemblage : l'avance de « ça monte toujours » = f × (2 × part des hausses − 1)",
           proche(avant["visible"][(TOUT, "valeur", 0.2)]["avance"], 0.2 * (2 * part_de_hausses(R_l)[0] - 1), 1e-15))
        ok("assemblage : les chiffres qui ne dépendent pas des cours (2,20 €, 95 et 47 séances)",
           proche(avant["aller_retour"][(CAPITAL, ECART)], 2.1989005497251374, 1e-12)
           and avant["epuise"][0] == 95 and avant["moitie"][0] == 47)
        visible_tout = ce_que_le_test_verra(R_l.to_numpy(float), poids_blocs(len(R_l), BLOC, TIRAGES, GRAINE),
                                            0.2, "valeur", 3, GRAINE * 1000)
        visible_an1 = ce_que_le_test_verra(an1.to_numpy(float), poids_blocs(len(an1), BLOC, TIRAGES, GRAINE + 1),
                                           0.5, "marche", 3, GRAINE * 1000 + 100 + 10 + 1)
        ok("assemblage : ce que le test verra, sur toute la période et sur la première année seule",
           all(avant["visible"][(TOUT, "valeur", 0.2)][c] == visible_tout[c] for c in ("plan", "moyenne", "monte"))
           and all(avant["visible"][("2023-2024", "marche", 0.5)][c] == visible_an1[c]
                   for c in ("plan", "moyenne", "monte")))
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            afficher(avant)
        texte = sortie.getvalue()
        lignes = texte.splitlines()

        def ligne_de(debut):
            return next((x for x in lignes if x.strip().startswith(debut)), "")

        t_a, a_a = avant["taux"]["toutes"], avant["taux"]["agitees"]
        ok("affichage : 2,20 € soit 1,10 %, 95 séances, les réglages vraiment utilisés (60 chemins, 3 méthodes)",
           "Coupé au 31/10/2025" in texte and "2,20 € soit 1,10 %" in texte and "en 95 séances" in texte
           and "sur 60 chemins" in texte and "médiane de 3 méthodes" in texte)
        ok("affichage : « pour ne pas perdre » d'abord avec 200 €, puis le contrôle, puis 50 €",
           f"Pour ne pas perdre : {_pct(avant['necessaire'][(CAPITAL, ECART)], 2, True)} par séance, brut (contrôle : "
           f"{_pct(avant['necessaire'][(CAPITAL, ECART_CONTROLE)], 2, True)} ; avec 50 € : "
           f"{_pct(avant['necessaire'][(50.0, ECART)], 2, True)})" in texte)
        ok("affichage : la ligne des 6 valeurs dans l'ordre (ne pas perdre, 1 fois sur 2, 9 fois sur 10)",
           f"{_pct(t_a['ne_pas_perdre'], 0):>13}{_simule(t_a['simule'][0.5]):>16}{_simule(t_a['simule'][0.9]):>16}"
           in ligne_de("parmi les 6 valeurs"))
        ok("affichage : le contrôle 0,3 % des deux lignes, chacun à sa place",
           f"(au contrôle 0,3 % : {_pct(t_a['ne_pas_perdre_controle'], 0)} et "
           f"{_pct(a_a['ne_pas_perdre_controle'], 0)})" in texte)
        attendu = []
        for forme, nom in FORMES.items():
            for f in PARTS_BAISSE:
                un = [avant["visible"][(a, forme, f)] for a in avant["annees"]]
                tout = avant["visible"][(TOUT, forme, f)]
                attendu.append(f"{_pts(float(np.median([x['plan'] for x in un]))):>12}{_pts(tout['plan']):>12}"
                               f"{_pts(float(np.median([x['monte'] for x in un]))):>15}{_pts(tout['monte']):>15}"
                               in ligne_de(f"« baisse » {_pct(f, 0)}, {nom}"))
        ok("affichage : le tableau du test, colonnes 1 an puis toute la période, tirage du plan puis « ça monte »",
           all(attendu))
        ok("affichage : l'avance de « ça monte toujours » de chaque part de « baisse », avec « de retard » ou « d’avance »",
           all(f"dire « baisse » {_pct(f, 0)} du temps part avec "
               f"{_pts(abs(avant['visible'][(TOUT, 'valeur', f)]['avance']))} "
               f"{'de retard sur' if avant['visible'][(TOUT, 'valeur', f)]['avance'] >= 0 else 'd’avance sur'}"
               in texte for f in PARTS_BAISSE))
        if t_a["simule"][0.5] is not None:
            c_moitie, _ = simuler_200(jours_de(R_l), t_a["simule"][0.5]["p"], chemins=60, graine=GRAINE)
            ruines_ok = (t_a["simule"][0.5]["ruines"] == float(np.mean(c_moitie <= 2 * FRAIS_ORDRE))
                         and f"Au taux « 1 fois sur 2 », {_pct(t_a['simule'][0.5]['ruines'], 0)} des chemins sont ruinés"
                         in texte)
        else:
            ruines_ok = False
        ok("la part des chemins ruinés : comptée à 2 € ou moins, au taux « 1 fois sur 2 », et affichée", ruines_ok)
        ok("assemblage : le taux qui suffit juste pour ne pas être ruiné (finir au-dessus de 2 €)",
           _egaux(t_a["survie"], taux_pour_battre(jours_de(R_l), 2 * FRAIS_ORDRE + 1e-9, 0.5, chemins=60, graine=GRAINE)))
        propose, fourchettes = [], []
        for forme, nom in FORMES.items():
            for f in PARTS_BAISSE:
                un = [avant["visible"][(a, forme, f)] for a in avant["annees"]]
                tout = avant["visible"][(TOUT, forme, f)]
                med = {c: float(np.median([x[c] for x in un])) for c in ("plan", "moyenne")}
                propose.append(f"{nom}, « baisse » {_pct(f, 0)} : {_pts(med['moyenne'])} sur 1 an et "
                               f"{_pts(tout['moyenne'])} sur 2 ans (au lieu de {_pts(med['plan'])} et "
                               f"{_pts(tout['plan'])})." in texte)
                fourchettes.append(f"{nom}, « baisse » {_pct(f, 0)} : {_fourchette([x['plan'] for x in un])} ; "
                                   f"{_fourchette([x['monte'] for x in un])}." in texte)
        ok("affichage : les lignes « Proposé » (la moyenne des tirages, puis les chiffres du plan entre parenthèses)",
           all(propose))
        ok("affichage : les fourchettes « selon l'année » (tirage du plan ; « ça monte toujours »)", all(fourchettes))

        def rendu(rap):
            sortie_r = io.StringIO()
            with contextlib.redirect_stdout(sortie_r):
                afficher(rap)
            return sortie_r.getvalue()

        pareil, autre = copy.deepcopy(avant), copy.deepcopy(avant)
        for x, survie in ((pareil, {"p": 0.8, "juste": 0.8, "ruines": 0.5}),
                          (autre, {"p": 0.75, "juste": 0.42, "ruines": 0.5})):
            x["taux"]["toutes"]["simule"][0.5] = {"p": 0.8, "juste": 0.8, "ruines": 0.5}
            x["taux"]["toutes"]["survie"] = survie
        ok("la phrase sur la ruine est calculée : « c'est la ruine qui fixe ce taux » seulement si le même taux suffit "
           "juste pour ne pas être ruiné, sinon le taux qui suffirait (42 %)",
           "c'est la ruine qui fixe ce taux" in rendu(pareil) and "c'est la ruine qui fixe ce taux" not in rendu(autre)
           and "pour ne pas être ruiné 1 fois sur 2, il suffirait de 42 %" in rendu(autre))
        riche, pauvre = copy.deepcopy(avant), copy.deepcopy(avant)
        for x, meilleure in ((riche, 0.10), (pauvre, 0.01)):
            x["taux"]["toutes"]["ne_pas_perdre_50"], x["plus_forte_hausse"] = 1.5, meilleure
        ok("la phrase des 50 € : « seule la plus forte hausse » quand elle dépasse le seuil (+10 %), "
           "« ne suffit pas » sinon (+1 %)",
           "Seule la plus forte hausse" in rendu(riche) and "ne suffit pas" not in rendu(riche)
           and "ne suffit pas" in rendu(pauvre) and "Seule la plus forte hausse" not in rendu(pauvre))
        devant = copy.deepcopy(avant)
        devant["visible"][(TOUT, "valeur", 0.2)]["avance"] = -0.003
        ok("… et « d’avance » quand « ça monte toujours » part derrière (une période de baisse)",
           "dire « baisse » 20 % du temps part avec 0,3 point d’avance sur" in rendu(devant))
        coffre = jours > FIN_AVANT_COFFRE
        ouv2 = np.where(coffre, ouverture * 2, ouverture)
        _ecrire_cache(dossier, jours, {t: np.where(coffre, v * 3, v) for t, v in cours.items()}, ouv2, ouv2 * 1.01)
        ok("le coffre n'est pas regardé : tripler les cours et doubler le change après le 31/10/2025 "
           "ne change aucun chiffre", _egaux(avant, mur_des_frais(dossier, **reglages)))
        cours3 = {t: v.copy() for t, v in cours.items()}
        cours3["AAA"][jours.get_loc(pd.Timestamp("2025-03-03"))] *= 1.3
        _ecrire_cache(dossier, jours, cours3, ouverture, ouverture * 1.01)
        ok("… alors qu'un cours d'avant la coupure les change (le test n'est pas aveugle)",
           not _egaux(avant, mur_des_frais(dossier, **reglages)))
        _ecrire_cache(dossier, jours, cours, ouverture, ouverture * 1.01)
        fa = fausses_alarmes(dossier, tickers, annees, methodes=20, tirages=1000)

        def recompte(r_p, i, j, l):
            forme, f = list(FORMES)[j], PARTS_BAISSE[l]
            y_p = (np.where(np.isfinite(r_p), r_p, 0.0) > 0).astype(float)
            mg = np.empty((20, 2))
            for g in range(PAQUETS_BLOCS):
                w_p = poids_blocs(len(r_p), BLOC, 1000, GRAINE + 1 + 100 * i + g)
                for k in range(g, 20, PAQUETS_BLOCS):
                    m, base = _une_methode(r_p, f, forme, np.random.default_rng([GRAINE + 1, i, j, l, k]))
                    (d_t, _, d_p), (b_t, _, b_p) = ecarts(m, y_p, np.isfinite(r_p), w_p, base)
                    mg[k] = (d_t - seuil_critique(d_t, b_t), d_p - seuil_critique(d_p, b_p))
            return mg

        cases = [(("2023-2024", "marche", 0.5), recompte(an1.to_numpy(float), 1, 1, 1)),
                 ((TOUT, "valeur", 0.2), recompte(R_l.to_numpy(float), 0, 0, 0))]
        ok(f"les fausses alarmes : chaque période et chaque cas ; deux cases recomptées à part, méthode par méthode, "
           f"sur leurs {PAQUETS_BLOCS} tirages des blocs (2023-2024 tout le marché à 50 % ; toute la période valeur par "
           "valeur à 20 %)",
           len(fa) == 3 * len(FORMES) * len(PARTS_BAISSE)
           and all(np.array_equal(fa[cle]["marges"], mg) and fa[cle]["moyenne"] == float(np.mean(mg[:, 0] > 0))
                   and fa[cle]["plan"] == float(np.mean(mg[:, 1] > 0)) for cle, mg in cases))
        courte = ((pd.Timestamp("2025-08-01"), pd.Timestamp("2025-09-30")),)
        message = _refuse(mur_des_frais, dossier, tickers=tickers, annees=courte, methodes=3, chemins=60)
        ok("une année notée de deux mois est refusée, avec un message clair",
           message is not None and "séances" in message)
        double = pd.read_csv(os.path.join(dossier, "AAA_10y.csv"))
        pd.concat([double, double.iloc[[100]]]).to_csv(os.path.join(dossier, "AAA_10y.csv"), index=False)
        message = _refuse(charger, dossier, tickers)
        ok("une date en double dans une valeur est refusée, avec un message clair",
           message is not None and "AAA_10y.csv" in message and "double" in message)
        for genre, valeur in (("nul", 0.0), ("négatif", -3.0), ("infini", np.inf)):
            abime = {t: v.copy() for t, v in cours.items()}
            abime["BBB"][jours.get_loc(pd.Timestamp("2024-05-06"))] = valeur
            _ecrire_cache(dossier, jours, abime, ouverture, ouverture * 1.01)
            message = _refuse(charger, dossier, tickers)
            ok(f"un cours {genre} est refusé, avec un message clair", message is not None and "BBB_10y.csv" in message)
        _ecrire_cache(dossier, jours, cours, ouverture, ouverture * 1.01)
        chemin = os.path.join(dossier, "CCC_10y.csv")
        df = pd.read_csv(chemin)
        df.loc[50, "Open"] = 0.0
        df.to_csv(chemin, index=False)
        ok("… mais un 0 dans une colonne jamais lue (l'ouverture d'une valeur) ne gêne pas",
           _refuse(charger, dossier, tickers) is None)
        df.drop(columns=["Close"]).to_csv(chemin, index=False)
        message = _refuse(charger, dossier, tickers)
        ok("un fichier sans sa colonne « Close » est refusé avec un message clair, pas une trace",
           message is not None and "CCC_10y.csv" in message and "Close" in message)
        propage = False
        try:
            _refuse(lambda: int("pas un nombre"))
        except ValueError:
            propage = True
        ok("une erreur qui ne vient pas du cache n'est pas prise pour un cache abîmé (elle remonte)", propage)

    faux = {(p_, forme, f): {"plan": 0.0, "moyenne": 0.0} for p_ in (TOUT, "2019-2020", "2020-2021", "2021-2022")
            for forme in FORMES for f in PARTS_BAISSE}
    faux[("2019-2020", "valeur", 0.2)] = {"plan": 0.01, "moyenne": 0.02}
    faux[("2020-2021", "valeur", 0.2)] = {"plan": 0.02, "moyenne": 0.03}
    faux[("2021-2022", "valeur", 0.2)] = {"plan": 0.06, "moyenne": 0.07}
    faux[(TOUT, "valeur", 0.2)] = {"plan": 0.05, "moyenne": 0.06}
    sortie = io.StringIO()
    with contextlib.redirect_stdout(sortie):
        afficher_fausses_alarmes(faux, 7)
    ligne = next((x for x in sortie.getvalue().splitlines() if "« baisse » 20 %, valeur par valeur" in x), "")
    ok("affichage des fausses alarmes : 1 an = la MOYENNE des années (3,0 % et 4,0 %, pas la médiane), puis toute "
       "la période (5,0 % et 6,0 %), tirage du plan puis moyenne des tirages",
       f"{'3,0 %':>15}{'5,0 %':>15}{'4,0 %':>15}{'6,0 %':>15}" in ligne)

    print("\n— Le test de l'étape 1 : les écarts et le bootstrap —")
    t1, m1 = ecarts(np.array([[1.0, 0.0], [1.0, 1.0]]), np.array([[1.0, 0.0], [0.0, 1.0]]), np.ones((2, 2), bool))
    ok("cas 1 : 3 bonnes réponses sur 4 ; le tirage au sort en attend 2, « ça monte » en a 2 → +25 et +25 points",
       proche(t1, 0.25, 1e-12) and proche(m1, 0.25, 1e-12))
    t2, m2 = ecarts(np.array([[0.0, 0.0], [1.0, 1.0]]), np.array([[1.0, 1.0], [1.0, 0.0]]), np.ones((2, 2), bool))
    ok("cas 2 : 1 sur 4 ; le tirage au sort en attend 2, « ça monte » en a 3 → −25 et −50 points",
       proche(t2, -0.25, 1e-12) and proche(m2, -0.5, 1e-12))
    t3, m3 = ecarts(np.array([[1.0, 0.0, 1.0]]), np.array([[1.0, 0.0, np.nan]]), np.array([[True, True, False]]))
    ok("une valeur-jour sans lendemain ne compte pas : 2 sur 2 → +50 et +50 points",
       proche(t3, 0.5, 1e-12) and proche(m3, 0.5, 1e-12))
    _, _, d4 = ecarts(np.array([[1.0, 0.0], [1.0, 1.0]]), np.array([[1.0, 0.0], [0.0, 1.0]]), np.ones((2, 2), bool),
                      base=np.array([[1.0, 1.0], [0.0, 0.0]]))
    ok("face à UN tirage : 3 bonnes réponses sur 4 contre 2 pour ce tirage-là → +25 points", proche(d4, 0.25, 1e-12))
    m = np.array([[1.0, 0.0], [0.0, 0.0], [1.0, 1.0]])
    y = np.array([[1.0, 1.0], [0.0, 1.0], [1.0, 0.0]])
    base = np.array([[0.0, 1.0], [1.0, 0.0], [1.0, 0.0]])
    Wm = np.array([[1, 1, 1], [2, 1, 0], [0, 0, 3], [0, 2, 1]], dtype=np.float32)
    _, tires = ecarts(m, y, np.ones_like(m, bool), Wm, base)
    attendus = []
    for w in Wm.astype(int):
        reprises = np.repeat(np.arange(3), w)
        mm, yy, bb = m[reprises].ravel(), y[reprises].ravel(), base[reprises].ravel()
        hm = np.mean(mm * yy + (1 - mm) * (1 - yy))
        attendus.append((hm - (mm.mean() * yy.mean() + (1 - mm.mean()) * (1 - yy.mean())),
                         hm - yy.mean(), hm - np.mean(bb * yy + (1 - bb) * (1 - yy))))
    ok("bootstrap : chaque tirage recalcule tout (méthode, moyenne des tirages, « ça monte », le tirage du plan), "
       "comme si l'on recopiait les séances reprises une à une",
       all(np.allclose(tires[i], [a[i] for a in attendus], rtol=0, atol=1e-12) for i in range(3)))
    W = poids_blocs(30, 5, 5000, 1)
    ok("bootstrap : chaque tirage reprend exactement les 30 séances", (W.sum(1) == 30).all())
    ok("bootstrap circulaire : chaque séance, la première et la dernière comprises, revient une fois en moyenne",
       np.abs(W.mean(0) - 1).max() < 0.1)
    ok("bootstrap : un bloc aussi long que la série → chaque tirage est la série entière",
       (poids_blocs(12, 12, 200, 1) == 1).all())
    ok("bootstrap : la même graine redonne les mêmes tirages, une autre en donne d'autres",
       np.array_equal(poids_blocs(30, 5, 100, 7), poids_blocs(30, 5, 100, 7))
       and not np.array_equal(poids_blocs(30, 5, 100, 7), poids_blocs(30, 5, 100, 8)))
    x = np.array([0.0] * 50 + [1.0] * 50)
    sd1 = float(np.std((poids_blocs(100, 1, 5000, 3) * x).sum(1) / 100))
    sd50 = float(np.std((poids_blocs(100, 50, 5000, 3) * x).sum(1) / 100))
    ok(f"bootstrap : des blocs de séances CONSÉCUTIVES (50 zéros puis 50 uns : écart-type {_nb(sd50, 3)} "
       f"par blocs de 50, {_nb(sd1, 3)} séance par séance, 0,05 attendu)", abs(sd1 - 0.05) < 0.005 and sd50 > 0.15)
    y = np.array([[1.0], [0.0]] * 200)
    (d_t, d_m), (b_t, b_m) = ecarts(np.zeros_like(y), y, np.ones_like(y, bool), poids_blocs(400, 1, TIRAGES, 5))
    s_m = seuil_critique(d_m, b_m)
    ok(f"seuil à 99 % : 400 séances, une hausse sur deux, la méthode dit toujours « baisse » : {_nb(s_m, 3)} "
       "(la loi binomiale donne 0,115 ; à 95 %, ce serait 0,085)", abs(s_m - 0.115) < 0.01)
    ok("… face à la moyenne des tirages, une méthode qui dit toujours la même chose a un écart nul à chaque tirage",
       d_t == 0.0 and seuil_critique(d_t, b_t) == 0.0)
    ok(f"pour être vu 8 fois sur 10, un écart vrai doit valoir {_nb(facteur_puissance(), 3)} fois le seuil "
       "(1 + 0,8416 / 2,3263)", proche(facteur_puissance(), 1 + 0.8416212335729143 / 2.3263478740408408, 1e-12))
    rng = np.random.default_rng(11)
    y = (rng.random((250, 20)) < 0.52).astype(float)
    W = poids_blocs(250, BLOC, 2000, 12)
    vus = {"moyenne": 0, "plan": 0}
    for i in range(500):
        m, base = _une_methode(np.where(y > 0, 0.01, -0.01), 0.3, "valeur", np.random.default_rng([13, i]))
        (d_t, _, d_p), (b_t, _, b_p) = ecarts(m, y, np.ones_like(y, bool), W, base)
        vus["moyenne"] += int(d_t > seuil_critique(d_t, b_t))
        vus["plan"] += int(d_p > seuil_critique(d_p, b_p))
    ok(f"sans talent, le test dit « vu » {vus['moyenne']} et {vus['plan']} fois sur 500 méthodes "
       "(moyenne des tirages, tirage du plan ; environ 5 attendues à 99 %)", max(vus.values()) <= 12)
    rr = np.where(np.random.default_rng(4).random((60, 8)) < 0.5, 0.01, -0.01)
    W = poids_blocs(60, BLOC, 1000, 3)
    seuils = []
    for i in range(3):
        m, base = _une_methode(rr, 0.2, "valeur", np.random.default_rng([99, i]))
        (d_t, d_m, d_p), (b_t, b_m, b_p) = ecarts(m, (rr > 0).astype(float), np.ones_like(rr, bool), W, base)
        seuils.append((seuil_critique(d_p, b_p), seuil_critique(d_t, b_t), seuil_critique(d_m, b_m)))
    ok("ce que le test verra = la médiane des seuils de chaque méthode tirée (plan, moyenne, « ça monte »)",
       ce_que_le_test_verra(rr, W, 0.2, "valeur", 3, 99)
       == {c: float(np.median([s[i] for s in seuils])) for i, c in enumerate(("plan", "moyenne", "monte"))})
    rng = np.random.default_rng(2)
    mv = methode_sans_talent("valeur", 0.2, 20000, 49, rng)
    mm = methode_sans_talent("marche", 0.2, 20000, 49, rng)
    ok("méthode sans talent : « baisse » 20 % du temps, valeur par valeur comme tout le marché",
       abs(1 - mv.mean() - 0.2) < 0.01 and abs(1 - mm.mean() - 0.2) < 0.01)
    ok("… « tout le marché » dit la même chose pour toutes les valeurs du soir, « valeur par valeur » non",
       (mm == mm[:, :1]).all() and not (mv == mv[:, :1]).all())
    m, base = _une_methode(np.zeros((20000, 10)), 0.2, "marche", np.random.default_rng(3))
    ok("le tirage du plan dit « baisse » aussi souvent que la méthode (20 %), mais valeur par valeur",
       abs(m.mean() - 0.8) < 0.01 and abs(base.mean() - m.mean()) < 0.01 and not (base == base[:, :1]).all())

    print("\n— La stratégie de 200 € —")
    jours = [np.array([0.10, -0.05]), np.array([0.02, -0.20])]
    c, j = simuler_200(jours, 1.0, chemins=3, graine=1)
    ok("toujours raison : 200 € → 217,68 € → 219,79 € ((200 − 1) × k × 1,10 − 1, puis (C − 1) × k × 1,02 − 1)",
       np.allclose(c, 219.79392920181588, rtol=0, atol=1e-9) and j == 1.0)
    c, j = simuler_200(jours, 0.0, chemins=3, graine=1)
    ok("toujours tort : 200 € → 187,86 € → 148,34 €", np.allclose(c, 148.33942145369095, rtol=0, atol=1e-9) and j == 0.0)
    soir = np.array([0.01, 0.02, 0.03, -0.01, -0.02, -0.03])
    for q in (0.5, 0.8):
        c, j = simuler_200([soir], q, chemins=30000, graine=6)
        pris = np.round((c + 1) / (199 * k) - 1, 6)
        frequences = [float(np.mean(np.isclose(pris, x))) for x in soir]
        ok(f"p = {_nb(q, 1)} : chacune des 3 hausses prise {_nb(q / 3 * 100, 1)} fois sur 100, chacune des 3 baisses "
           f"{_nb((1 - q) / 3 * 100, 1)} fois (au hasard, ni la meilleure ni la moins mauvaise), "
           f"{_nb(j * 100, 1)} % de soirs justes",
           np.allclose(frequences, [q / 3] * 3 + [(1 - q) / 3] * 3, rtol=0, atol=0.012) and abs(j - q) < 0.012)
    c, j = simuler_200([np.array([-0.05, -0.08])], 1.0, chemins=2)
    ok("un soir où tout baisse, avoir raison = rester en liquide : 200 € intacts, sans frais",
       np.allclose(c, 200.0, rtol=0, atol=1e-12) and j == 1.0)
    c, j = simuler_200([np.array([-0.05, -0.05])], 0.0, chemins=2)
    ok("… se tromper = acheter une valeur qui baisse", np.allclose(c, 199 * k * 0.95 - 1, rtol=0, atol=1e-9) and j == 0.0)
    c, j = simuler_200([np.array([0.03, 0.03])], 0.0, chemins=2)
    ok("un soir où tout monte, même une erreur achète une valeur qui monte (et compte juste)",
       np.allclose(c, 199 * k * 1.03 - 1, rtol=0, atol=1e-9) and j == 1.0)
    c, _ = simuler_200([np.array([-0.5])] * 3, 0.0, chemins=1, capital=5.0)
    ok("ruine : sous 2 €, plus d'aller-retour (5 € → 1,00 €, puis plus rien)",
       np.allclose(c, 4 * k * 0.5 - 1, rtol=0, atol=1e-12))
    _, j = simuler_200([np.array([-0.5]), np.array([0.1]), np.array([0.1])], 0.0, chemins=1, capital=5.0)
    ok("… un chemin arrêté ne compte plus ses soirs : une seule décision, fausse → 0 % de soirs justes", j == 0.0)
    c, _ = simuler_200([np.array([-0.9])], 0.0, chemins=1, capital=2.5)
    ok("… et jamais sous 0 €", c[0] == 0.0)
    ok("SPY gardé : 200 € → (199 / 1,0005) × 1,10 × 0,90 = 196,91 €",
       proche(garde([0.10, -0.10]), 196.91154422788606, 1e-9))
    ok("le panier gardé, à parts égales et sans rééquilibrer : 199 / 1,0005 × (1,10 + 0,90 × 1,20 + 2,00) / 3",
       proche(garde_panier(np.array([[0.10, -0.10, 1.0], [0.0, 0.20, 0.0]])),
              199 / 1.0005 * (1.10 + 1.08 + 2.00) / 3, 1e-9))
    jours = [np.array([0.05, -0.05])] * 150
    cible = garde(np.zeros(150))
    moitie = taux_pour_battre(jours, cible, 0.5, chemins=400, graine=4)
    neuf = taux_pour_battre(jours, cible, 0.9, chemins=400, graine=4)

    def part_devant(q):
        return float(np.mean(simuler_200(jours, q, chemins=400, graine=4)[0] >= cible))

    p0 = taux_necessaire(0.05, 0.05, rendement_necessaire(200.0))
    ok(f"taux pour finir devant 1 fois sur 2 : {_pct(moitie['p'], 1)} ; la dichotomie tombe juste "
       "(la moitié des chemins y arrive à ce taux, pas juste en dessous)",
       part_devant(moitie["p"]) >= 0.5 and part_devant(moitie["p"] - 2 ** -12) < 0.5)
    ok(f"… proche de la formule ({_pct(p0, 1)}) quand les mouvements sont fixes (±5 %)", abs(moitie["p"] - p0) < 0.05)
    ok(f"… et 9 fois sur 10 demande plus : {_pct(neuf['p'], 1)}, avec moins de chemins ruinés "
       f"({_pct(neuf['ruines'], 0)} contre {_pct(moitie['ruines'], 0)})",
       neuf["p"] > moitie["p"] and neuf["ruines"] < moitie["ruines"] and part_devant(neuf["p"]) >= 0.9)
    parts = [part_devant(q) for q in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)]
    ok("… ici, avoir raison plus souvent ne finit jamais plus bas", all(a <= b for a, b in zip(parts, parts[1:])))
    ok("si les valeurs ne bougent que de 0,1 %, même toujours avoir raison ne bat pas SPY : « impossible »",
       taux_pour_battre([np.array([0.001, -0.001])] * 50, garde(np.zeros(50)), chemins=50) is None)

    print("\n— Les réglages —")
    ok("six années notées, de novembre à octobre, de nov. 2019 à oct. 2025, la dernière finit à la coupure",
       len(ANNEES_NOTEES) == 6 and ANNEES_NOTEES[0][0] == pd.Timestamp("2019-11-01")
       and ANNEES_NOTEES[-1][1] == FIN_AVANT_COFFRE == pd.Timestamp("2025-10-31")
       and all((a.month, a.day, b.month, b.day, b.year - a.year) == (11, 1, 10, 31, 1) for a, b in ANNEES_NOTEES))
    ok("les frais du courtier : 1 € par ordre, écart 0,1 %, contrôle 0,3 %",
       FRAIS_ORDRE == 1.0 and ECART == 0.001 and ECART_CONTROLE == 0.003)
    ok("le test : 99 %, au moins 5 000 tirages, blocs de 10 séances, médiane de 50 méthodes, « vu » 8 fois sur 10",
       NIVEAU == 0.99 and TIRAGES >= 5000 and BLOC == 10 and METHODES == 50 and PUISSANCE == 0.80)
    ok("la stratégie : 1 000 chemins, 1 fois sur 2 et 9 fois sur 10 ; l'agitation : 20 séances, 10 % ; "
       "change à 5 jours au plus ; années d'au moins 200 séances",
       CHEMINS == 1000 and PARTS_CHEMINS == (0.5, 0.9) and VOL_JOURS == 20 and PART_AGITEES == 0.10
       and ECART_CHANGE_MAX == 5 and SEANCES_MIN_ANNEE == 200)

    print(f"\n{sum(cas)}/{len(cas)} vérifications réussies")
    return all(cas)


if __name__ == "__main__":
    main()
