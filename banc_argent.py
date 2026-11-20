"""
Le banc des prévisions — le critère 2 : la stratégie de 200 € de chaque méthode.

CE QUE ÇA FAIT
    Avec les prévisions du critère 1 (data/banc/etape1-previsions.csv.gz :
    les sorties que le critère 1 a jugées, md5 vérifié), chaque méthode
    choisit chaque soir UNE valeur et y place tout son capital : 200 € au
    départ, 1 € par ordre chez le courtier et un demi-écart de 0,05 % de
    chaque côté. La même valeur que la veille : on la garde, sans frais ;
    une autre : on vend, puis on achète ; aucune : le liquide. Deux heures
    d'achat : le soir, au cours de clôture, et le lendemain, à l'ouverture.
    Le critère 2 (plan du 16/11/2026, précisé et validé le 20/11, fin de
    BRIEF.md) : finir au-dessus de 200 € gardés dans SPY ET de 200 € gardés
    dans le panier des 49, aux deux heures, frais compris, avant l'impôt.
    Dessous : « si tu vendais tout à la fin », après l'impôt de 31,4 %.

CE QUE ÇA NE FAIT PAS
    Il ne rouvre aucune porte : la porte A ne dépend que du critère 1
    (fermée le 20/11/2026). Aucune prévision du jour, aucun conseil, aucun
    ordre. Le coffre (après le 31/10/2025) n'est pas lu.

ÉCRIT
    Seulement par la vraie mesure : data/banc/critere2.json (jamais
    écrasé) et data/banc/critere2-chemins.csv.gz (chaque soir, pour chaque
    méthode et aux deux heures : le choix, la valeur détenue et le capital).

LANCER
    python3 banc.py --critere2            # la mesure, une seule fois
    python3 banc.py --critere2 --a-blanc  # toute la chaîne, sorties mélangées et rendements au hasard, rien d'écrit
    python3 banc.py --verifier            # les cas calculés à la main (étapes 0 et 1, critère 2)
"""

import contextlib
import copy
import datetime
import hashlib
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

import banc
import banc_methodes as bm
from banc import CacheInvalide

DOSSIER_BANC = bm.DOSSIER_BANC
NOM_RESULTAT = "critere2.json"
NOM_CHEMINS = "critere2-chemins.csv.gz"
CAPITAL, FRAIS = banc.CAPITAL, banc.FRAIS_ORDRE           # 200 € au départ ; 1 € par ordre
ECART, ECART_CONTROLE = banc.ECART, banc.ECART_CONTROLE   # l'aller-retour : 0,1 % ; le contrôle : 0,3 %
TAUX_IMPOT, REPORT_ANS = 0.314, 10                        # comme simulateur_200.Reglages (vérifié par --verifier)
GRAINE_DEPARTAGE = 20261122            # un générateur par méthode : +0 (a), +1 (b), +2 (c), +3 (d), +4 (« comme hier »)
GRAINE_DEPARTAGE_EN_PLUS = 20261200    # les 100 autres graines de départage de a et d (ne décident rien)
GRAINES_EN_PLUS = 100
GRAINE_TIRAGE = 20261130               # le tirage au sort, en argent
TIRAGES_ARGENT = 1000
GRAINE_A_BLANC = 20261131
FICHIERS_DU_CODE = ("banc.py", "banc_methodes.py", "banc_argent.py", "stock_engine.py")
HEURES = ("soir", "matin")
METHODES, CANDIDATES, NOMS, SEUILS = bm.METHODES, bm.CANDIDATES, bm.NOMS, bm.SEUILS


# ══════════════════════════════════════════════════════════════════
#  LES DONNÉES : LES PRÉVISIONS GARDÉES ET LES COURS
# ══════════════════════════════════════════════════════════════════

@dataclass
class Argent:
    dates: pd.DatetimeIndex   # les soirs notés au critère 1 (T)
    valeurs: list             # les 49 valeurs ; les rendements ont SPY en plus, en dernière colonne
    sorties: dict             # méthode → (T, 49) : force, momentum, probabilité, rendement du jour
    valide: np.ndarray        # (T, 49) : la valeur-jour comptée au critère 1
    r: dict                   # heure → (T, 50) : rendement de chaque valeur jusqu'au moment suivant, en euros
    jours: dict               # heure → (T,) : la date du moment où s'exécute la décision du soir
    soirs: dict               # heure → le nombre de soirs exécutés (le matin : un de moins)
    dernier: dict             # heure → la date du dernier moment compté


def lire_previsions(dossier_banc=DOSSIER_BANC):
    """Les prévisions gardées du critère 1, si leur md5 est celui que garde etape1.json. Rend (prévisions, etape1)."""
    with open(os.path.join(dossier_banc, bm.NOM_MESURE), encoding="utf-8") as f:
        etape1 = json.load(f)
    chemin = os.path.join(dossier_banc, bm.NOM_PREVISIONS)
    if bm._md5(chemin) != etape1["previsions"]["md5"]:
        raise CacheInvalide("les prévisions gardées ne sont pas celles de etape1.json (md5 différent) : rien n'est calculé")
    return pd.read_csv(chemin, float_precision="round_trip"), etape1


def preparer_argent(prev, cours, change, tickers):
    """
    Les matrices du critère 2. Le soir : le rendement de la clôture de J à
    celle de J+1, en euros, celui des prévisions gardées (49 valeurs) ; SPY
    par la même règle. Le matin : de l'ouverture de J+1 à celle de J+2,
    chaque ouverture divisée par l'ouverture de la ligne SUIVANTE du change
    (banc.en_euros). Le matin s'arrête au dernier soir qui a ses deux
    ouvertures converties : la décision du 29/10/2025 se vendrait au change
    du 03/11, dans le coffre.
    """
    tickers = list(tickers)
    dates_txt = list(dict.fromkeys(prev["date"]))
    dates = pd.DatetimeIndex(dates_txt)
    if not dates.is_monotonic_increasing or len(prev) != len(dates) * len(tickers):
        raise CacheInvalide("les prévisions gardées n'ont pas la forme attendue : rien n'est calculé")

    def matrice(colonne):
        return (prev.pivot(index="date", columns="valeur", values=colonne)
                .reindex(index=dates_txt, columns=tickers).to_numpy(float))

    tous = tickers + [banc.REFERENCE]
    clotures = pd.DataFrame({t: cours[t]["Close"] for t in tous}).sort_index()
    seances = clotures.index
    r_clot = banc.rendements_du_lendemain(banc.en_euros(clotures, change)).reindex(dates)
    r_prev = matrice("rendement_eur")
    if not np.array_equal(r_clot[tickers].to_numpy(float), r_prev, equal_nan=True):
        raise CacheInvalide("les rendements des prévisions gardées ne sont pas ceux du cache : rien n'est calculé")
    ouvertures = pd.DataFrame({t: cours[t]["Open"] for t in tous}).reindex(seances)
    o_eur = banc.en_euros(ouvertures, change)
    r_ouv = (o_eur.shift(-2) / o_eur.shift(-1) - 1).reindex(dates).to_numpy(float)
    suivante = pd.Series(seances, index=seances).shift(-1)
    lendemain = pd.DatetimeIndex(suivante.reindex(dates))
    au_matin = np.isfinite(r_ouv).any(axis=1)
    n_matin = int(np.flatnonzero(au_matin).max()) + 1 if au_matin.any() else 0
    if not au_matin[:n_matin].all():
        raise CacheInvalide("un soir sans ouverture au milieu de la période : rien n'est calculé")
    dernier_soir = lendemain[-1]
    dernier_matin = suivante[lendemain[n_matin - 1]] if n_matin else pd.NaT
    return Argent(dates=dates, valeurs=tickers, sorties={m: matrice(m) for m in METHODES},
                  valide=matrice("comptee") == 1,
                  r={"soir": np.column_stack([r_prev, r_clot[banc.REFERENCE].to_numpy(float)]), "matin": r_ouv},
                  jours={"soir": dates, "matin": lendemain}, soirs={"soir": len(dates), "matin": n_matin},
                  dernier={"soir": dernier_soir, "matin": dernier_matin})


def melanger_a_blanc(argent, graine=GRAINE_A_BLANC):
    """
    L'essai à blanc : les sorties mélangées entre les valeurs, chaque soir,
    et des rendements tirés au hasard. Sans le mélange, les choix, les
    ordres, les frais et les dates d'arrêt seraient les vrais : ils ne
    dépendent que des sorties.
    """
    rng = np.random.default_rng(graine)
    b = copy.deepcopy(argent)
    for m in METHODES:
        for t in range(len(b.dates)):
            b.sorties[m][t] = b.sorties[m][t, rng.permutation(len(b.valeurs))]
    for h in HEURES:
        b.r[h] = np.where(np.isfinite(b.r[h]), rng.normal(0.0005, 0.02, b.r[h].shape), np.nan)
    return b


# ══════════════════════════════════════════════════════════════════
#  LES CHOIX DE CHAQUE SOIR
# ══════════════════════════════════════════════════════════════════

def choix_d_une_methode(sortie, valide, seuil, graine):
    """
    La valeur choisie chaque soir (−1 = aucune) : la plus forte sortie
    parmi les valeurs comptées qui passent le seuil (la plus forte force
    parmi les « hausse » de a, le plus fort momentum positif de b, la plus
    forte probabilité au-dessus de 50 % de c et d, la plus forte hausse du
    jour de « comme hier »). À égalité : la valeur choisie la veille si elle
    en fait partie, sinon une valeur tirée au hasard dans le groupe de tête.
    Le générateur tire chaque soir, même quand il ne sert pas. Les choix ne
    dépendent pas du capital : les mêmes aux deux heures et aux deux écarts.
    """
    rng = np.random.default_rng(graine)
    choix = np.full(sortie.shape[0], -1, dtype=int)
    veille = -1
    for t in range(sortie.shape[0]):
        u = rng.random()                                   # un tirage chaque soir, même quand il ne sert pas
        x = sortie[t]
        ok = valide[t] & np.isfinite(x) & (x > seuil)
        if ok.any():
            tete = np.flatnonzero(ok & (x == x[ok].max()))
            c = veille if veille in tete else int(tete[min(int(u * len(tete)), len(tete) - 1)])
        else:
            c = -1
        choix[t] = veille = c
    return choix


def choix_au_hasard(valide, tirages, graine):
    """Le tirage au sort, en argent : chaque soir, pour chaque tirage, une valeur comptée prise au hasard."""
    rng = np.random.default_rng(graine)
    choix = np.full((tirages, valide.shape[0]), -1, dtype=int)
    for t in range(valide.shape[0]):
        ok = np.flatnonzero(valide[t])
        if len(ok):
            choix[:, t] = ok[np.minimum((rng.random(tirages) * len(ok)).astype(int), len(ok) - 1)]
    return choix


# ══════════════════════════════════════════════════════════════════
#  L'ARGENT : UNE SEULE FONCTION POUR TOUT
# ══════════════════════════════════════════════════════════════════

def valoriser(choix, R, annees, ecart=ECART, capital=CAPITAL, frais=FRAIS, chemins=False):
    """
    Des suites de choix (P × T ; −1 = aucune) donnent des capitaux. Au
    moment t, la décision s'exécute :
      - la même valeur que celle détenue : rien ;
      - sinon, l'argent disponible (la vente avant son frais, valeur ×
        (1 − demi-écart), ou le liquide) ; à 2 € ou moins, plus aucun
        ordre jusqu'à la fin : elle garde ce qu'elle a ;
      - vendre : valeur × (1 − demi-écart) − 1 € ; le gain (ce que rapporte
        la vente moins ce que la ligne a coûté, frais d'achat compris)
        compte dans l'année civile du moment ;
      - acheter : (argent − 1 €) ÷ (1 + demi-écart).
    Puis la valeur détenue suit son rendement R[t] jusqu'au moment suivant
    (une séance sans cours : elle ne bouge pas). Le liquide ne rapporte rien.
    Rend, pour chaque suite : la valeur au dernier moment SANS la dernière
    vente, la valeur détenue, son coût, les ordres, les frais (1 € par
    ordre) et l'écart payés, le moment et le capital de l'arrêt, les gains
    réalisés par année ; avec `chemins`, la valeur détenue et le capital à
    chaque moment.
    """
    choix = np.atleast_2d(np.asarray(choix, int))
    R = np.nan_to_num(np.asarray(R, float), nan=0.0)
    P, T = choix.shape
    h = ecart / 2
    ans = sorted({int(a) for a in annees})
    col_an = np.array([ans.index(int(a)) for a in annees], dtype=int)
    V = np.full(P, float(capital))
    detenue = np.full(P, -1, dtype=int)
    cout = np.zeros(P)
    arrete = np.zeros(P, bool)
    arret = np.full(P, -1, dtype=int)
    capital_arret = np.full(P, np.nan)
    ordres = np.zeros(P, dtype=int)
    ecart_paye = np.zeros(P)
    gains = np.zeros((P, len(ans)))
    garde_det, garde_cap = (np.empty((P, T), dtype=int), np.empty((P, T))) if chemins else (None, None)
    for t in range(T):
        cible = choix[:, t]
        change = (cible != detenue) & ~arrete
        dispo = np.where(detenue >= 0, V * (1 - h), V)
        stop = change & (dispo <= 2 * frais)
        arret[stop], capital_arret[stop] = t, V[stop]
        arrete |= stop
        agit = change & ~stop
        vend = agit & (detenue >= 0)
        produit = V * (1 - h) - frais
        np.add.at(gains, (np.flatnonzero(vend), col_an[t]), (produit - cout)[vend])
        ecart_paye += np.where(vend, V * h, 0.0)
        V = np.where(vend, produit, V)
        detenue = np.where(vend, -1, detenue)
        achete = agit & (cible >= 0)
        place = (V - frais) / (1 + h)
        ecart_paye += np.where(achete, V - frais - place, 0.0)
        cout = np.where(achete, V, cout)
        V = np.where(achete, place, V)
        detenue = np.where(achete, cible, detenue)
        ordres += vend.astype(int) + achete.astype(int)
        V = V * (1 + np.where(detenue >= 0, R[t, np.maximum(detenue, 0)], 0.0))
        if chemins:
            garde_det[:, t], garde_cap[:, t] = detenue, V
    return {"valeur": V, "detenue": detenue, "cout": cout, "ordres": ordres, "frais": ordres * float(frais),
            "ecart": ecart_paye, "arret": arret, "capital_arret": capital_arret, "gains": gains, "annees": ans,
            "chemin_detenue": garde_det, "chemin_capital": garde_cap}


def impots_par_annee(gains, taux=TAUX_IMPOT, report=REPORT_ANS):
    """
    {année : gain net réalisé} → {année : impôt}, les années dans l'ordre,
    comme simulateur_200.Portefeuille.impot_de_l_annee (vérifié par
    --verifier) : gains et pertes d'une même année se compensent ; une
    perte nette se reporte et s'impute sur les gains des `report` années
    suivantes, les plus anciennes d'abord. Le simulateur n'est pas importé
    ici : il change le dossier courant et tait tous les avertissements.
    """
    pertes, impots = [], {}
    for annee in sorted(gains):
        net = gains[annee]
        pertes = [[a, m] for a, m in pertes if annee - a <= report]
        if net < 0:
            pertes.append([annee, -net])
            impots[annee] = 0.0
            continue
        for perte in pertes:
            imputee = min(perte[1], net)
            perte[1] -= imputee
            net -= imputee
        pertes = [p for p in pertes if p[1] > 1e-9]
        impots[annee] = net * taux
    return impots


def si_tu_vendais_tout(res, annee_finale, ecart=ECART, frais=FRAIS, capital=CAPITAL):
    """
    « Si tu vendais tout à la fin » : la dernière vente (s'il reste une
    valeur détenue : valeur × (1 − demi-écart) − 1 €, jamais sous 0), son
    gain dans l'année finale, puis l'impôt de chaque année (payé de ta
    poche l'année suivante, il ne sort pas du capital de la stratégie).
    Rend (ce qui te resterait, les impôts payés en tout), pour chaque suite.
    """
    h = ecart / 2
    ans = sorted(set(res["annees"]) | {int(annee_finale)})
    gains = np.zeros((len(res["valeur"]), len(ans)))
    for i, a in enumerate(res["annees"]):
        gains[:, ans.index(a)] = res["gains"][:, i]
    tient = res["detenue"] >= 0
    produit = np.where(tient, np.maximum(res["valeur"] * (1 - h) - frais, 0.0), res["valeur"])
    gains[:, ans.index(int(annee_finale))] += np.where(tient, produit - res["cout"], 0.0)
    impots = np.array([sum(impots_par_annee(dict(zip(ans, g))).values()) for g in gains])
    return produit - impots, impots


def garder_le_panier(valeurs_finales, ecart=ECART, frais=FRAIS, capital=CAPITAL):
    """Le panier gardé (la moyenne des valeurs gardées une à une), puis une seule vente à 1 € à la fin et l'impôt de son gain net."""
    v = float(np.mean(valeurs_finales))
    produit = max(v * (1 - ecart / 2) - frais, 0.0)
    return v, produit - TAUX_IMPOT * max(produit - capital, 0.0)


# ══════════════════════════════════════════════════════════════════
#  LA MESURE
# ══════════════════════════════════════════════════════════════════

def _quantiles(x):
    x = np.asarray(x, float)
    return {"mediane": float(np.median(x)), "bas": float(np.quantile(x, 0.1)), "haut": float(np.quantile(x, 0.9)),
            "min": float(np.min(x)), "max": float(np.max(x))}


def calculer(argent, ecarts=(ECART, ECART_CONTROLE), tirages=TIRAGES_ARGENT, graines_en_plus=GRAINES_EN_PLUS):
    """
    Le critère 2 et ses repères, aux deux heures et aux deux écarts. Rend
    (le rapport, les chemins des méthodes à l'écart principal).
    """
    nv = len(argent.valeurs)
    choix = {m: choix_d_une_methode(argent.sorties[m], argent.valide, SEUILS[m], GRAINE_DEPARTAGE + i)
             for i, m in enumerate(METHODES)}
    hasard = choix_au_hasard(argent.valide, tirages, GRAINE_TIRAGE)
    en_plus = {m: np.array([choix_d_une_methode(argent.sorties[m], argent.valide, SEUILS[m],
                                                GRAINE_DEPARTAGE_EN_PLUS + k) for k in range(graines_en_plus)])
               for m in ("a", "d")}
    rap = {"debut": argent.dates[0], "soirs": {h: argent.soirs[h] for h in HEURES},
           "premier": {h: argent.jours[h][0] for h in HEURES}, "dernier": dict(argent.dernier),
           "dernier_execute": {h: argent.jours[h][argent.soirs[h] - 1] for h in HEURES},
           "valeurs": nv, "reglages": {"capital": CAPITAL, "frais": FRAIS, "ecart": ECART, "controle": ECART_CONTROLE,
                                       "tirages": tirages, "graines_en_plus": graines_en_plus},
           "resultats": {}, "egalites": {m: int(_egalites(argent.sorties[m], argent.valide, SEUILS[m]))
                                         for m in ("a", "d")}}
    lignes = []
    for heure in HEURES:
        T = argent.soirs[heure]
        R, ans = argent.r[heure][:T], argent.jours[heure][:T].year
        annee_finale = int(argent.dernier[heure].year)
        for e in ecarts:
            cle = f"{heure}-{e}"
            principal = e == ecarts[0]
            strat = valoriser(np.array([choix[m][:T] for m in METHODES]), R, ans, e, chemins=principal)
            gardes = valoriser(np.repeat(np.arange(nv + 1)[:, None], T, axis=1), R, ans, e)
            net_s, imp_s = si_tu_vendais_tout(strat, annee_finale, e)
            net_g, imp_g = si_tu_vendais_tout(gardes, annee_finale, e)
            panier, panier_net = garder_le_panier(gardes["valeur"][:nv], e)
            spy = float(gardes["valeur"][nv])
            barre = max(spy, panier)
            res = {"spy": spy, "panier": panier, "barre": barre, "spy_net": float(net_g[nv]), "panier_net": panier_net,
                   "valeurs": {t: float(v) for t, v in zip(argent.valeurs, gardes["valeur"][:nv])}, "methodes": {}}
            for i, m in enumerate(METHODES):
                a_arrete = strat["arret"][i] >= 0
                res["methodes"][m] = {
                    "final": float(strat["valeur"][i]), "passe_ici": bool(strat["valeur"][i] > barre),
                    "ordres": int(strat["ordres"][i]), "frais": float(strat["frais"][i]),
                    "ecart": float(strat["ecart"][i]),
                    "arret": argent.jours[heure][strat["arret"][i]] if a_arrete else None,
                    "capital_arret": float(strat["capital_arret"][i]) if a_arrete else None,
                    "net": float(net_s[i]), "impots": float(imp_s[i]),
                    "detenue_fin": argent.valeurs[strat["detenue"][i]] if strat["detenue"][i] >= 0 else None,
                    "soirs_en_liquide": int(np.sum(choix[m][:T] < 0))}
            if principal:
                res["hasard"] = _quantiles(valoriser(hasard[:, :T], R, ans, e)["valeur"])
                res["departage"] = {m: _quantiles(valoriser(en_plus[m][:, :T], R, ans, e)["valeur"]) for m in en_plus}
                res["survivant"] = {}
                for i, m in enumerate(METHODES):
                    fin = strat["arret"][i] if strat["arret"][i] >= 0 else T
                    det = strat["chemin_detenue"][i][:fin]        # avant l'arrêt : la position bloquée n'est plus un choix
                    tenues = []
                    for j in np.unique(det[det >= 0]):
                        quand = det == j
                        tenues.append((argent.valeurs[j], int(quand.sum()),
                                       float(np.prod(1 + np.nan_to_num(R[:fin][quand, j])) - 1)))
                    tenues.sort(key=lambda x: (-x[1], x[0]))
                    res["survivant"][m] = {"bat": int(np.sum(gardes["valeur"][:nv] < strat["valeur"][i])),
                                           "longues": tenues[:3]}
                    for t in range(T):
                        d = strat["chemin_detenue"][i, t]
                        lignes.append((argent.dates[t].strftime("%Y-%m-%d"), heure, m,
                                       argent.valeurs[choix[m][t]] if choix[m][t] >= 0 else "",
                                       argent.valeurs[d] if d >= 0 else "", float(strat["chemin_capital"][i, t])))
            rap["resultats"][cle] = res
    for e in ecarts:
        rap[f"passent-{e}"] = [m for m in CANDIDATES
                               if all(rap["resultats"][f"{h}-{e}"]["methodes"][m]["passe_ici"] for h in HEURES)]
    rap["passent"] = rap[f"passent-{ecarts[0]}"]
    rap["choix_md5"] = {m: hashlib.md5(choix[m].astype("<i8").tobytes()).hexdigest() for m in METHODES}
    chemins = pd.DataFrame(lignes, columns=["date", "heure", "methode", "choix", "detenue", "capital"])
    return rap, chemins


def _egalites(sortie, valide, seuil):
    """Le nombre de soirs où plusieurs valeurs sont à égalité en tête."""
    n = 0
    for t in range(sortie.shape[0]):
        x = sortie[t]
        ok = valide[t] & np.isfinite(x) & (x > seuil)
        n += bool(ok.any() and np.sum(ok & (x == x[ok].max())) > 1)
    return n


def cibles_de_l_etape0(argent):
    """Les repères gardés du soir par les fonctions de l'étape 0 (banc.garde, banc.garde_panier) : (SPY, panier)."""
    nv = len(argent.valeurs)
    return banc.garde(argent.r["soir"][:, nv]), banc.garde_panier(argent.r["soir"][:, :nv])


# ══════════════════════════════════════════════════════════════════
#  L'AFFICHAGE
# ══════════════════════════════════════════════════════════════════

def _eur(x):
    return banc._nb(x, 2) + " €"


def _soirs(n):
    return f"{banc._nb(n, 0)} soir{'s' if n > 1 else ''}"


def rendu(rap):
    """Le rapport du critère 2, en texte (des faits sur le passé : jamais de conseil ni de prévision)."""
    lignes = []
    dire = lignes.append
    p = ECART
    soir, matin = rap["resultats"][f"soir-{p}"], rap["resultats"][f"matin-{p}"]
    if rap.get("a_blanc"):
        dire("À BLANC : les sorties sont MÉLANGÉES et les rendements TIRÉS AU HASARD. Rien de ce rapport ne parle des "
             "vraies méthodes.")
    duree = round((rap["dernier"]["soir"] - rap["premier"]["soir"]).days / 365.25)
    dire(f"LE CRITÈRE 2 — la stratégie de 200 € de chaque méthode, sur {duree} ans (étape 1 du banc des prévisions)")
    dire("Les sorties : les prévisions du critère 1 (md5 vérifié). Les cours : le cache, lu sans rien écrire, coupé au "
         f"{banc._date(banc.FIN_AVANT_COFFRE)} : le coffre n'est pas lu.")
    dire(f"{_eur(rap['reglages']['capital'])} au départ, tout sur une seule valeur ; {_eur(rap['reglages']['frais'])} par "
         f"ordre et un demi-écart de {banc._pct(p / 2, 2)} de chaque côté. Le liquide ne rapporte rien ici (le courtier "
         "le rémunère : ce n'est pas compté).")
    dire(f"Le soir : la décision du soir J s'exécute à la clôture de J, du {banc._date(rap['premier']['soir'])} au "
         f"{banc._date(rap['dernier_execute']['soir'])} ; tout est compté à la clôture du "
         f"{banc._date(rap['dernier']['soir'])}.")
    dire(f"Le matin : à l'ouverture du lendemain, du {banc._date(rap['premier']['matin'])} au "
         f"{banc._date(rap['dernier_execute']['matin'])} ; tout est compté à l'ouverture du "
         f"{banc._date(rap['dernier']['matin'])}.")

    dire("\n1. LE CRITÈRE 2 (avant l'impôt, frais compris, sans la dernière vente)")
    dire("   Il faut finir au-dessus du meilleur de SPY gardé et du panier des 49 gardé, le soir ET le matin.")
    dire(f"   {'':30}{'le soir':>14}{'le matin':>14}{'ordres (soir)':>15}{'frais (soir)':>15}{'critère 2':>16}")
    for m in METHODES:
        s, ma = soir["methodes"][m], matin["methodes"][m]
        verdict = "ne concourt pas" if m not in CANDIDATES else "PASSE" if m in rap["passent"] else "ne passe pas"
        dire(f"   {NOMS[m] + (' (repère)' if m == 'hier' else ''):30}{_eur(s['final']):>14}{_eur(ma['final']):>14}"
             f"{banc._nb(s['ordres'], 0):>15}{_eur(s['frais'] + s['ecart']):>15}{verdict:>16}")
    dire("   Les frais : 1 € par ordre, plus l'écart payé à chaque achat et à chaque vente.")
    dire(f"   Les repères gardés (1 € et un demi-écart une seule fois) : SPY {_eur(soir['spy'])} le soir, "
         f"{_eur(matin['spy'])} le matin ; le panier des {len(soir['valeurs'])} {_eur(soir['panier'])} le soir, {_eur(matin['panier'])} "
         "le matin.")
    for m in CANDIDATES:
        s, ma = soir["methodes"][m], matin["methodes"][m]
        dire(f"     {NOMS[m]} : il lui fallait plus de {_eur(soir['barre'])} le soir et de {_eur(matin['barre'])} le "
             f"matin ; elle a fini à {_eur(s['final'])} et {_eur(ma['final'])}.")
    for m in METHODES:
        for heure, res in (("le soir", soir), ("le matin", matin)):
            x = res["methodes"][m]
            if x["arret"] is not None:
                dire(f"     {NOMS[m]}, {heure} : arrêtée le {banc._date(x['arret'])} avec {_eur(x['capital_arret'])} : "
                     "plus aucun ordre ensuite ; ce qu'elle garde suit sa dernière valeur.")
    if not rap["passent"]:
        dire("   Aucune candidate ne passe le critère 2.")
    for m in rap["passent"]:
        longue = soir["survivant"][m]["longues"]
        phrase = (f"   {NOMS[m]} finit au-dessus des deux repères, le soir et le matin : elle passe le critère 2. La porte A "
                  "reste fermée (le critère 1) : rien de plus.")
        if longue:
            phrase += (f" Elle passe, mais garder {longue[0][0]} seule faisait {_eur(soir['valeurs'][longue[0][0]])} "
                       "(le soir).")
        dire(phrase)

    dire("\n2. LES AUTRES REPÈRES (ne décident rien)")
    hs, hm = soir["hasard"], matin["hasard"]
    dire(f"   Le tirage au sort (une valeur au hasard chaque soir, {banc._nb(rap['reglages']['tirages'], 0)} tirages) : "
         f"médiane {_eur(hs['mediane'])} le soir (8 sur 10 entre {_eur(hs['bas'])} et {_eur(hs['haut'])}), "
         f"{_eur(hm['mediane'])} le matin (8 sur 10 entre {_eur(hm['bas'])} et {_eur(hm['haut'])}).")
    dire("   Il change de valeur presque chaque soir, et ses frais le ruinent vite : faire mieux que lui montre surtout "
         "qu'on change moins souvent, pas un talent.")
    v = soir["valeurs"]
    tri = sorted(v, key=lambda t: v[t])
    bat = sum(x > soir["panier"] for x in v.values())
    dire(f"   Chaque valeur gardée seule (le soir) : de {_eur(v[tri[0]])} ({tri[0]}) à {_eur(v[tri[-1]])} ({tri[-1]}), "
         f"médiane {_eur(float(np.median(list(v.values()))))} ; {bat} sur {len(v)} battent le panier.")
    for m in CANDIDATES:
        sv = soir["survivant"][m]
        tenues = ", ".join(f"{t} ({_soirs(n)}, {banc._pct(r, 1, signe=True)})" for t, n, r in sv["longues"])
        dire(f"     {NOMS[m]} (le soir) : elle fait mieux que {sv['bat']} des {len(v)} valeurs gardées ; détenues le plus "
             f"longtemps : {tenues or 'aucune'}.")
    for m, d in soir["departage"].items():
        dm = matin["departage"][m]
        dire(f"   Le départage de {NOMS[m]} ({_soirs(rap['egalites'][m])} à égalité en tête) : avec "
             f"{banc._nb(rap['reglages']['graines_en_plus'], 0)} autres graines, médiane {_eur(d['mediane'])} le soir "
             f"(de {_eur(d['min'])} à {_eur(d['max'])}), {_eur(dm['mediane'])} le matin (de {_eur(dm['min'])} à "
             f"{_eur(dm['max'])}).")

    dire("\n3. SI TU VENDAIS TOUT À LA FIN (la dernière vente, puis l'impôt de 31,4 %, année civile par année civile, "
         "pertes reportées, payé de ta poche l'année suivante)")
    for m in METHODES:
        s, ma = soir["methodes"][m], matin["methodes"][m]
        dire(f"     {NOMS[m]} : il te resterait {_eur(s['net'])} le soir ({_eur(s['impots'])} d'impôt en tout), "
             f"{_eur(ma['net'])} le matin ({_eur(ma['impots'])} d'impôt).")
    dire(f"   SPY gardé : {_eur(soir['spy_net'])} le soir, {_eur(matin['spy_net'])} le matin ; le panier gardé : "
         f"{_eur(soir['panier_net'])} et {_eur(matin['panier_net'])} (une seule vente et l'impôt à la fin).")
    for m in METHODES:
        for heure, res in (("le soir", soir), ("le matin", matin)):
            x = res["methodes"][m]
            if x["net"] < 0:
                dire(f"     {NOMS[m]}, {heure} : tu aurais perdu plus que tes 200 € : {_eur(x['impots'])} d'impôt payés "
                     "de ta poche les bonnes années, plus que ce qui reste à vendre.")

    dire(f"\n4. LE CONTRÔLE À {banc._pct(ECART_CONTROLE, 1)} D'ÉCART (ne décide rien)")
    sc, mc = rap["resultats"][f"soir-{ECART_CONTROLE}"], rap["resultats"][f"matin-{ECART_CONTROLE}"]
    for m in CANDIDATES:
        dire(f"     {NOMS[m]} : {_eur(sc['methodes'][m]['final'])} le soir, {_eur(mc['methodes'][m]['final'])} le matin ; "
             f"il fallait plus de {_eur(sc['barre'])} et {_eur(mc['barre'])}.")

    dire("\n5. À SAVOIR")
    dire("   Le critère 2 ne rouvre pas la porte A : elle ne dépend que du critère 1, qu'aucune candidate n'a passé.")
    dire("   Le biais du survivant : les 49 valeurs ont été choisies en 2026, toutes ont survécu. Les repères gardés en "
         "sont flattés, et une méthode peut l'être aussi.")
    dire("   Des faits sur le passé, pas une prévision ni un conseil.")
    return "\n".join(lignes)


# ══════════════════════════════════════════════════════════════════
#  LE RÉSULTAT ÉCRIT (une seule fois)
# ══════════════════════════════════════════════════════════════════

def empreinte_du_code(dossier=banc.ROOT, noms=FICHIERS_DU_CODE):
    """Le commit git, « modifie » si un fichier du code du critère 2 diffère de ce commit (ou si git ne répond pas), et les md5."""
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=dossier, capture_output=True, text=True,
                                check=True).stdout.strip()
        etat = subprocess.run(["git", "status", "--porcelain", "--", *noms], cwd=dossier, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit, etat = None, "git ne répond pas"
    return {"commit": commit, "modifie": bool(etat), "md5": bm.md5_des_fichiers(dossier, noms)}


def ecrire_critere2(rap, chemins, texte, code, dossier_banc=DOSSIER_BANC, donnees=None, bibliotheques=None,
                    previsions_md5=None, maintenant=None):
    """
    Écrit le résultat (critere2.json) et les chemins, une seule fois : les
    chemins d'abord, le JSON en dernier, par un lien, qui échoue s'il
    existe déjà (FileExistsError). Le JSON garde le md5 de SES chemins.
    Rien n'est jamais écrit dans data/market.
    """
    if os.path.abspath(dossier_banc).startswith(os.path.abspath(banc.MARCHE)):
        raise RuntimeError("le banc n'écrit jamais dans data/market")
    os.makedirs(dossier_banc, exist_ok=True)
    chemin = os.path.join(dossier_banc, NOM_RESULTAT)
    if os.path.exists(chemin):
        raise FileExistsError(f"{chemin} existe déjà : une mesure ne s'écrase pas")
    maintenant = maintenant or datetime.datetime.now(datetime.timezone.utc).astimezone()
    contenu = {"date_mesure": maintenant.isoformat(timespec="seconds"), "code": code, "donnees": donnees or {},
               "bibliotheques": bibliotheques or {}, "previsions_etape1_md5": previsions_md5,
               "resultat": bm._en_json(rap), "rapport": texte}
    fd, temp = tempfile.mkstemp(dir=dossier_banc, prefix=".critere2-", suffix=".tmp")
    fd2, temp_ch = tempfile.mkstemp(dir=dossier_banc, prefix=".chemins-", suffix=".tmp")
    os.close(fd2)
    try:
        chemins.to_csv(temp_ch, index=False, compression="gzip")
        contenu["chemins"] = {"fichier": NOM_CHEMINS, "md5": bm._md5(temp_ch), "lignes": len(chemins)}
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(contenu, f, ensure_ascii=False, indent=1, allow_nan=False)
        os.replace(temp_ch, os.path.join(dossier_banc, NOM_CHEMINS))      # les chemins d'abord…
        os.link(temp, chemin)                       # refusé si le résultat existe déjà
    finally:
        os.remove(temp)
        if os.path.exists(temp_ch):
            os.remove(temp_ch)
    return chemin


def lancer_le_critere2(a_blanc=False, dossier=banc.MARCHE, dossier_banc=DOSSIER_BANC, racine_du_code=banc.ROOT,
                       tirages=TIRAGES_ARGENT, graines_en_plus=GRAINES_EN_PLUS):
    """
    `python3 banc.py --critere2` : la mesure, une seule fois, sur du code
    commité, avec les prévisions et les cours du critère 1 (md5 vérifiés).
    Le rapport ne s'affiche qu'une fois écrit, relu dans le fichier. Si
    elle a déjà été faite, son rapport est réaffiché tel quel.
    `--a-blanc` : sorties mélangées et rendements tirés au hasard, rien d'écrit.
    """
    chemin = os.path.join(dossier_banc, NOM_RESULTAT)
    if not a_blanc and os.path.exists(chemin):
        with open(chemin, encoding="utf-8") as f:
            deja = json.load(f)
        print(f"La mesure du critère 2 a déjà été faite le {deja['date_mesure']} (commit {deja['code']['commit']}) : "
              "elle ne s'écrase pas. Voici son rapport.\n")
        print(deja["rapport"])
        return deja
    code = empreinte_du_code(racine_du_code)
    if not a_blanc and code["modifie"]:
        raise SystemExit("Rien n'est calculé : banc.py, banc_methodes.py, banc_argent.py ou stock_engine.py n'est pas "
                         "commité tel quel. La mesure doit tourner sur du code enregistré (git commit d'abord).")
    prev, etape1 = lire_previsions(dossier_banc)
    tickers = list(dict.fromkeys(prev["valeur"]))
    donnees = bm.empreinte_des_donnees(dossier, tickers)
    if donnees != etape1.get("donnees"):
        raise CacheInvalide("les fichiers de cours ne sont plus ceux du critère 1 (md5 différents) : rien n'est calculé")
    _, cours, change = bm.charger_cours(dossier, tickers)
    argent = preparer_argent(prev, cours, change, tickers)
    if a_blanc:
        argent = melanger_a_blanc(argent)
    rap, chemins = calculer(argent, tirages=tirages, graines_en_plus=graines_en_plus)
    spy0, panier0 = cibles_de_l_etape0(argent)
    soir = rap["resultats"][f"soir-{ECART}"]
    if abs(soir["spy"] - spy0) > 0.005 or abs(soir["panier"] - panier0) > 0.005:
        raise RuntimeError("les repères gardés ne redonnent pas les cibles de l'étape 0 : rien n'est affiché")
    rap["a_blanc"] = a_blanc
    texte = rendu(rap)
    if a_blanc:
        print(texte)
        return rap
    ecrire_critere2(rap, chemins, texte, code, dossier_banc, donnees=donnees,
                    bibliotheques=bm.versions_des_bibliotheques(), previsions_md5=etape1["previsions"]["md5"])
    with open(chemin, encoding="utf-8") as f:
        print(json.load(f)["rapport"])
    print(f"\nÉcrit, une fois pour toutes : {chemin} et {os.path.join(dossier_banc, NOM_CHEMINS)}.")
    return rap


# ══════════════════════════════════════════════════════════════════
#  LES VÉRIFICATIONS (des cas à la main et des cours inventés, jamais le vrai cache)
# ══════════════════════════════════════════════════════════════════

def _impot_du_simulateur(gains):
    """
    L'impôt de simulateur_200.Portefeuille.impot_de_l_annee, année par
    année. Son import (qui change le dossier courant et tait tous les
    avertissements) est contenu : les filtres et le dossier sont rendus.
    """
    dossier = os.getcwd()
    try:
        with warnings.catch_warnings():
            import simulateur_200
            pf = simulateur_200.Portefeuille(simulateur_200.Reglages())
            pf.gains = dict(gains)
            return ({a: pf.impot_de_l_annee(a) for a in sorted(gains)},
                    (simulateur_200.REGLAGES.taux_impot, simulateur_200.REGLAGES.report_pertes_ans))
    finally:
        os.chdir(dossier)


def verifier():
    from unittest import mock
    cas = []

    def ok(nom, condition):
        cas.append(bool(condition))
        print(("✓ " if condition else "✗ ") + nom)

    def proche(a, b, tol=1e-9):
        return a is not None and b is not None and abs(a - b) <= tol

    h = ECART / 2
    print("— Les réglages —")
    ok("200 € au départ, 1 € par ordre, un demi-écart de 0,05 % de chaque côté, le contrôle à 0,3 %, l'impôt à 31,4 % "
       "avec 10 ans de report ; les graines du départage (20261122 + n° de la méthode), des 100 autres (20261200 + k) "
       "et du tirage au sort (20261130, 1 000 tirages)",
       CAPITAL == 200.0 and FRAIS == 1.0 and ECART == 0.001 and ECART_CONTROLE == 0.003 and TAUX_IMPOT == 0.314
       and REPORT_ANS == 10 and GRAINE_DEPARTAGE == 20261122 and GRAINE_DEPARTAGE_EN_PLUS == 20261200
       and GRAINES_EN_PLUS == 100 and GRAINE_TIRAGE == 20261130 and TIRAGES_ARGENT == 1000 and HEURES == ("soir", "matin"))
    ok("l'empreinte du code compte banc.py, banc_methodes.py, banc_argent.py et stock_engine.py",
       FICHIERS_DU_CODE == ("banc.py", "banc_methodes.py", "banc_argent.py", "stock_engine.py"))
    texte_du_module = open(os.path.abspath(__file__), encoding="utf-8").read().lower()
    ok("le module ne nomme pas le courtier (dépôt public)",
       ("trade" + " republic") not in texte_du_module and ("147," + "46") not in texte_du_module)

    print("\n— L'argent, à la main —")
    R3 = np.array([[0.10, 0.00, -0.05], [0.02, 0.03, 0.00], [0.00, -0.10, 0.04]])
    res3 = valoriser(np.array([[0, 0, 1], [-1, 2, -1]]), R3, np.array([2020, 2020, 2021]), chemins=True)
    v1 = (200 - 1) / (1 + h) * 1.10 * 1.02                 # achète la valeur 0, la garde un soir sans frais
    vente1 = v1 * (1 - h) - 1
    v1_fin = (vente1 - 1) / (1 + h) * (1 - 0.10)           # vend, puis achète la valeur 1
    v2 = (200 - 1) / (1 + h)                               # en liquide, puis la valeur 2 (rendement nul)
    vente2 = v2 * (1 - h) - 1                              # puis de nouveau en liquide
    ok("acheter : (200 − 1) ÷ 1,0005 ; garder un soir sans frais (+10 % puis +2 %) ; changer : vendre (× 0,9995 − 1), "
       "puis racheter ; 3 ordres, 3 € de frais",
       proche(res3["valeur"][0], v1_fin) and res3["ordres"][0] == 3 and proche(res3["frais"][0], 3.0)
       and res3["detenue"][0] == 1)
    ok("en liquide, rien ne bouge ; acheter puis revenir au liquide : 2 ordres, ce que rapporte la vente reste en liquide",
       proche(res3["valeur"][1], vente2) and res3["ordres"][1] == 2 and res3["detenue"][1] == -1)
    ok("chaque vente compte son gain dans l'année du moment où elle se fait (2021 pour les deux), rien en 2020 ; le "
       "coût de la ligne détenue : l'argent placé, frais d'achat compris",
       res3["annees"] == [2020, 2021] and proche(res3["gains"][0, 1], vente1 - 200)
       and proche(res3["gains"][1, 1], vente2 - 200) and res3["gains"][0, 0] == 0 and res3["gains"][1, 0] == 0
       and proche(res3["cout"][0], vente1))
    ok("les chemins : la valeur détenue et le capital après chaque moment ; l'écart payé : un demi-écart à chaque achat "
       "et à chaque vente",
       list(res3["chemin_detenue"][0]) == [0, 0, 1] and proche(res3["chemin_capital"][0, 2], v1_fin)
       and proche(res3["chemin_capital"][0, 0], v2 * 1.10) and proche(res3["ecart"][1], (199 - v2) + v2 * h))
    c_trou = 2.0004 * (1 + h) + 1                          # après l'achat, la position vaut 2,0004 €
    res_trou = valoriser(np.array([[0, 1, 1]]), np.zeros((3, 2)), np.array([2020, 2020, 2020]), capital=c_trou)
    res_liq = valoriser(np.array([[-1, 0]]), np.zeros((2, 1)), np.array([2020, 2020]), capital=2.0)
    ok("l'arrêt à 2 € : une position de 2,0004 € ne rapporterait que 1,9994 € à la vente : plus aucun ordre (sans cette "
       "règle, l'achat suivant plaçait −0,0006 €), elle garde sa valeur ; avec 2 € pile en liquide, plus aucun ordre",
       res_trou["arret"][0] == 1 and proche(res_trou["capital_arret"][0], 2.0004, 1e-12) and res_trou["ordres"][0] == 1
       and res_trou["detenue"][0] == 0 and proche(res_trou["valeur"][0], 2.0004, 1e-12)
       and res_liq["arret"][0] == 1 and res_liq["ordres"][0] == 0)

    print("\n— Les choix de chaque soir —")
    # 3 valeurs à égalité ; une graine où le 1er, le 2e et le 4e nombre tombent sur trois valeurs différentes : garder
    # la veille, tirer chaque soir et oublier la veille après « aucune » changent alors tous le résultat.
    graine = next(g for g in range(1, 1000) if len({int(x * 3) for x in np.random.default_rng(g).random(4)[[0, 1, 3]]}) == 3)
    u = (np.random.default_rng(graine).random(4) * 3).astype(int)
    sortie = np.array([[0.5, 0.5, 0.5], [0.5, 0.5, 0.5], [-0.1, -0.2, -0.3], [0.7, 0.7, 0.7]])
    ok("à égalité, un tirage dans le groupe de tête, puis la valeur de la veille gardée tant qu'elle en fait partie ; "
       "aucune au-dessus du seuil : « aucune » ; après « aucune », un nouveau tirage, avec le 4e nombre du générateur "
       "(il tire chaque soir, même quand il ne sert pas)",
       list(choix_d_une_methode(sortie, np.ones((4, 3), bool), 0.0, graine)) == [u[0], u[0], -1, u[3]])
    sortie2 = np.array([[0.9, np.nan, 0.7], [0.5, 0.6, 0.6]])
    ok("une valeur non comptée ou sans sortie n'est jamais choisie, même la plus forte ; la veille gardée à égalité ; "
       "« au-dessus » est strict (50 % pile n'est pas une « hausse » pour c et d)",
       list(choix_d_une_methode(sortie2, np.array([[False, True, True], [True, True, True]]), 0.0, 1)) == [2, 2]
       and list(choix_d_une_methode(np.array([[0.5, 0.4]]), np.ones((1, 2), bool), 0.5, 1)) == [-1])
    hz = choix_au_hasard(np.array([[True, False, True]] * 3), 400, 3)
    ok("le tirage au sort ne prend que des valeurs comptées, chacune à peu près aussi souvent",
       set(np.unique(hz)) == {0, 2} and 0.4 < float(np.mean(hz == 0)) < 0.6)

    print("\n— L'impôt —")
    cas_impot = ({2020: 100.0}, {2020: -30.0, 2021: 50.0}, {2020: -50.0, 2030: 80.0}, {2020: -50.0, 2031: 80.0},
                 {2019: 10.0, 2020: -40.0, 2021: 15.0, 2022: 30.0})
    attendus = ({2020: 31.4}, {2020: 0.0, 2021: 0.314 * 20}, {2020: 0.0, 2030: 0.314 * 30},
                {2020: 0.0, 2031: 0.314 * 80}, {2019: 3.14, 2020: 0.0, 2021: 0.0, 2022: 0.314 * 5})
    ok("à la main : 31,4 % d'un gain ; une perte imputée l'année suivante ; encore imputable 10 ans après, plus 11 ans "
       "après ; imputée sur les gains suivants",
       all(all(proche(impots_par_annee(g)[a], v) for a, v in att.items()) for g, att in zip(cas_impot, attendus)))
    dossier_avant, filtres_avant = os.getcwd(), list(warnings.filters)
    simules = [_impot_du_simulateur(g) for g in cas_impot]
    ok("le même impôt que simulateur_200.Portefeuille.impot_de_l_annee sur ces 5 cas, avec les mêmes réglages (31,4 %, "
       "10 ans), et l'import du simulateur ne change ni le dossier courant ni les filtres des avertissements",
       all(all(proche(impots_par_annee(g)[a], s[a]) for a in g) for g, (s, _) in zip(cas_impot, simules))
       and all(r == (TAUX_IMPOT, REPORT_ANS) for _, r in simules) and os.getcwd() == dossier_avant
       and list(warnings.filters) == filtres_avant)
    net3, imp3 = si_tu_vendais_tout(res3, 2021)
    fin1 = res3["valeur"][0] * (1 - h) - 1
    ok("si tu vendais tout : la dernière vente (× 0,9995 − 1) et son gain dans l'année finale, qui compense le gain de "
       "2021 (pas d'impôt) ; en liquide, rien à vendre",
       proche(net3[0], fin1) and proche(imp3[0], 0.0) and proche(net3[1], vente2) and imp3[1] == 0)
    resn = valoriser(np.array([[0, 1, 1]]), np.array([[1.0, 0.0], [0.0, -0.999], [0.0, 0.0]]),
                     np.array([2020, 2020, 2021]))
    netn, impn = si_tu_vendais_tout(resn, 2021)
    gain_n = (199 / (1 + h) * 2) * (1 - h) - 1 - 200
    ok("un gain imposé en 2020, puis la ruine en 2021 : la vente finale ne rapporte rien (jamais sous 0), la perte ne "
       "remonte pas sur 2020 : il te resterait moins que rien (−31,4 % du gain de 2020)",
       proche(impn[0], 0.314 * gain_n) and proche(netn[0], -0.314 * gain_n) and netn[0] < 0)

    print("\n— Les repères gardés —")
    Rg = np.random.default_rng(11).normal(0.0005, 0.02, (300, 4))
    Rg[5, 2] = np.nan                                      # une séance sans cours : la valeur ne bouge pas
    resg = valoriser(np.repeat(np.arange(4)[:, None], 300, axis=1), Rg, np.full(300, 2021))
    ok("une valeur gardée de bout en bout par la fonction de valorisation = banc.garde ; le panier (la moyenne des "
       "valeurs gardées) = banc.garde_panier, à 10⁻⁹ près ; un seul ordre chacune",
       all(proche(resg["valeur"][j], banc.garde(np.nan_to_num(Rg[:, j])), 1e-9) for j in range(4))
       and proche(float(np.mean(resg["valeur"][:3])), banc.garde_panier(Rg[:, :3]), 1e-9) and (resg["ordres"] == 1).all())
    pv, pnet = garder_le_panier(resg["valeur"][:3])
    ok("le panier à la fin : une seule vente à 1 € (× 0,9995 − 1), puis 31,4 % de son gain net",
       proche(pv, float(np.mean(resg["valeur"][:3]))) and proche(pnet, (pv * (1 - h) - 1) - 0.314 * max(pv * (1 - h) - 201, 0)))

    print("\n— Sur des cours inventés : les données et la chaîne —")
    with tempfile.TemporaryDirectory() as racine:
        base = os.path.join(racine, "base")
        os.makedirs(base)
        bm._faux_cache(base, graine=5, signal=0.5)
        rap1, prev = bm.mesurer(base, list(bm.FAUX), bm.ANNEES_FAUSSES)
        tickers, cours, change = bm.charger_cours(base, list(bm.FAUX))
        ar = preparer_argent(prev, cours, change, tickers)
        seances = cours[tickers[0]].index
        t, j = 10, 1
        s1, s2 = seances[seances > ar.dates[t]][:2]

        def o_eur(jour):
            return cours[tickers[j]].loc[jour, "Open"] / change[change.index > jour].iloc[0]

        def spy_eur(jour):
            return cours[banc.REFERENCE].loc[jour, "Close"] / change[change.index > jour].iloc[0]

        r_prev = prev.pivot(index="date", columns="valeur", values="rendement_eur").reindex(columns=tickers).to_numpy(float)
        ok("le matin : de l'ouverture de J+1 à celle de J+2, chaque ouverture ÷ l'ouverture de la ligne SUIVANTE du change "
           "(recalculé à la main) ; le soir : les rendements des prévisions gardées ; SPY en dernière colonne",
           proche(ar.r["matin"][t, j], o_eur(s2) / o_eur(s1) - 1, 1e-12)
           and np.array_equal(ar.r["soir"][:, :len(tickers)], r_prev, equal_nan=True) and ar.r["soir"].shape[1] == 7
           and ar.jours["matin"][t] == s1 and proche(ar.r["soir"][t, -1], spy_eur(s1) / spy_eur(ar.dates[t]) - 1, 1e-12))
        ok("le soir : du premier au dernier soir noté, compté à la clôture du lendemain (30/10/2025) ; le matin : un soir de "
           "moins, la dernière décision exécutée est celle du 28/10, comptée à l'ouverture du 30/10",
           ar.soirs["matin"] == ar.soirs["soir"] - 1 and ar.dates[-1] == pd.Timestamp("2025-10-29")
           and ar.dernier["soir"] == pd.Timestamp("2025-10-30") and ar.dernier["matin"] == pd.Timestamp("2025-10-30")
           and ar.jours["matin"][ar.soirs["matin"] - 1] == pd.Timestamp("2025-10-29"))
        prev_faux = prev.copy()
        prev_faux.loc[100, "rendement_eur"] += 0.001
        refus = None
        try:
            preparer_argent(prev_faux, cours, change, tickers)
        except CacheInvalide as erreur:
            refus = str(erreur)
        ok("des prévisions dont un rendement n'est plus celui du cache sont refusées", refus is not None and "rendements" in refus)

        rap, chemins = calculer(ar, tirages=30, graines_en_plus=3)
        soir = rap["resultats"][f"soir-{ECART}"]
        spy0, pan0 = cibles_de_l_etape0(ar)
        ok("les repères gardés du soir redonnent les cibles de l'étape 0 (banc.garde, banc.garde_panier) au centime ; le "
           "panier est la moyenne des valeurs gardées",
           abs(soir["spy"] - spy0) < 0.005 and abs(soir["panier"] - pan0) < 0.005
           and proche(soir["panier"], float(np.mean(list(soir["valeurs"].values()))))
           and soir["barre"] == max(soir["spy"], soir["panier"]))
        q = _quantiles(np.arange(101.0))
        ok("le tirage au sort, en argent : la médiane et l'intervalle où tombent 8 sur 10 (10 % et 90 %)",
           q["mediane"] == 50 and q["bas"] == 10 and q["haut"] == 90 and q["min"] == 0 and q["max"] == 100)
        par = {k: g.reset_index(drop=True) for k, g in chemins.groupby(["heure", "methode"])}
        ok("les chemins : une ligne par soir, par méthode et par heure ; les mêmes choix aux deux heures ; le dernier "
           "capital de chaque chemin = son capital final",
           len(chemins) == len(METHODES) * (ar.soirs["soir"] + ar.soirs["matin"])
           and all(par[("soir", m)]["choix"][:ar.soirs["matin"]].equals(par[("matin", m)]["choix"]) for m in METHODES)
           and all(proche(float(par[(hh, m)]["capital"].iloc[-1]), rap["resultats"][f"{hh}-{ECART}"]["methodes"][m]["final"])
                   for hh in HEURES for m in METHODES))
        ok("le placebo : sur des cours au hasard, aucune candidate ne passe le critère 2", rap["passent"] == [])
        oracle = copy.deepcopy(ar)
        oracle.sorties["b"] = np.nan_to_num(ar.r["soir"][:, :len(tickers)], nan=-1.0)    # b « sait » le lendemain
        rap_o, _ = calculer(oracle, tirages=10, graines_en_plus=2)
        sans_matin = copy.deepcopy(oracle)
        sans_matin.r["matin"] = np.where(np.isfinite(ar.r["matin"]), -0.01, np.nan)        # le matin, tout perd 1 %
        rap_s, _ = calculer(sans_matin, tirages=10, graines_en_plus=2)
        ok("le témoin positif : une méthode qui sait le rendement du lendemain finit très au-dessus des repères et passe ; "
           "si elle perd le matin, elle ne passe pas (le soir ET le matin)",
           rap_o["resultats"][f"soir-{ECART}"]["methodes"]["b"]["final"] > 10 * rap_o["resultats"][f"soir-{ECART}"]["barre"]
           and "b" in rap_o["passent"] and rap_s["resultats"][f"soir-{ECART}"]["methodes"]["b"]["passe_ici"]
           and "b" not in rap_s["passent"])
        J = 250
        futur = copy.deepcopy(ar)
        rng = np.random.default_rng(99)
        for m in METHODES:
            futur.sorties[m][J + 1:] = rng.normal(0, 1, futur.sorties[m][J + 1:].shape)
        for hh in HEURES:
            futur.r[hh][J + 1:] = rng.normal(0, 0.02, futur.r[hh][J + 1:].shape)
        passe = copy.deepcopy(ar)
        passe.r["soir"][:J + 1] = passe.r["soir"][:J + 1] * 1.5
        _, ch_futur = calculer(futur, tirages=10, graines_en_plus=2)
        _, ch_passe = calculer(passe, tirages=10, graines_en_plus=2)
        _, ch_ref = calculer(ar, tirages=10, graines_en_plus=2)

        def jusqu_a_j(ch):
            return ch[pd.to_datetime(ch["date"]) <= ar.dates[J]].reset_index(drop=True)

        ok("aucun regard vers le futur : changer les sorties et les rendements après le soir J ne change aucun choix ni "
           "aucun capital jusqu'au soir J (son capital est compté au moment suivant, avec le rendement du soir J) ; le "
           "contrôle inverse : changer les rendements d'avant change bien les capitaux",
           jusqu_a_j(ch_futur).equals(jusqu_a_j(ch_ref)) and not jusqu_a_j(ch_passe).equals(jusqu_a_j(ch_ref)))
        arretees = [m for m in METHODES if soir["methodes"][m]["arret"] is not None]
        ok("le départage de a et d avec d'autres graines, les égalités en tête comptées ; le rang de chaque candidate parmi "
           "les valeurs gardées ; ses 3 valeurs détenues le plus longtemps, comptées AVANT un arrêt à 2 € (la position "
           "bloquée ensuite n'est plus un choix)",
           set(soir["departage"]) == {"a", "d"} and set(rap["egalites"]) == {"a", "d"} and arretees
           and all(0 <= soir["survivant"][m]["bat"] <= len(tickers) and len(soir["survivant"][m]["longues"]) <= 3
                   for m in METHODES)
           and all(sum(n for _, n, _ in soir["survivant"][m]["longues"])
                   <= list(ar.dates).index(soir["methodes"][m]["arret"]) for m in arretees))

        print("\n— La mesure : écrite une seule fois, sur du code commité —")
        banc_dir = os.path.join(racine, "banc")
        bm.ecrire_mesure(rap1, prev, "le rapport de l'étape 1", {"commit": "0" * 40, "modifie": False, "md5": {}},
                         banc_dir, donnees=bm.empreinte_des_donnees(base, list(bm.FAUX)))
        depot = os.path.join(racine, "depot")
        os.makedirs(depot)
        for nom in ("banc.py", "banc_methodes.py", "banc_argent.py", "stock_engine.py"):
            shutil.copy(os.path.join(banc.ROOT, nom), depot)
        git = ["git", "-c", "user.name=banc", "-c", "user.email=banc@exemple.invalid"]
        for commande in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "essai"]):
            subprocess.run(git + commande, cwd=depot, check=True, capture_output=True)
        reglages = {"dossier": base, "dossier_banc": banc_dir, "racine_du_code": depot, "tirages": 10, "graines_en_plus": 2}
        with open(os.path.join(depot, "banc_argent.py"), "a", encoding="utf-8") as f:
            f.write("\n# une retouche pas commitée\n")
        sortie_txt, refus = io.StringIO(), None
        with contextlib.redirect_stdout(sortie_txt):
            try:
                lancer_le_critere2(**reglages)
            except SystemExit as erreur:
                refus = str(erreur)
        ok("sur du code pas commité (banc_argent.py retouché), le critère 2 refuse : rien n'est calculé, affiché ni écrit",
           refus is not None and "banc_argent.py" in refus and sortie_txt.getvalue() == ""
           and not os.path.exists(os.path.join(banc_dir, NOM_RESULTAT)))
        subprocess.run(git + ["checkout", "-q", "--", "banc_argent.py"], cwd=depot, check=True, capture_output=True)
        sortie_txt = io.StringIO()
        with contextlib.redirect_stdout(sortie_txt):
            rap_b = lancer_le_critere2(a_blanc=True, **reglages)
        ok("à blanc : le rapport le dit en tête, rien n'est écrit ; les choix ne sont pas les vrais (sorties mélangées) et "
           "les rendements non plus (SPY gardé change)",
           sortie_txt.getvalue().startswith("À BLANC") and not os.path.exists(os.path.join(banc_dir, NOM_RESULTAT))
           and all(rap_b["choix_md5"][m] != rap["choix_md5"][m] for m in METHODES)
           and rap_b["resultats"][f"soir-{ECART}"]["spy"] != soir["spy"])
        sortie_txt, echec = io.StringIO(), None
        with mock.patch(f"{__name__}.cibles_de_l_etape0", return_value=(0.0, 0.0)), contextlib.redirect_stdout(sortie_txt):
            try:
                lancer_le_critere2(**reglages)
            except RuntimeError as erreur:
                echec = str(erreur)
        ok("si les repères gardés ne redonnent pas les cibles de l'étape 0, rien n'est affiché ni écrit",
           echec is not None and sortie_txt.getvalue() == "" and not os.path.exists(os.path.join(banc_dir, NOM_RESULTAT)))
        sortie_txt, echec = io.StringIO(), None
        with mock.patch(f"{__name__}.ecrire_critere2", side_effect=OSError("disque plein")), \
                contextlib.redirect_stdout(sortie_txt):
            try:
                lancer_le_critere2(**reglages)
            except OSError as erreur:
                echec = erreur
        coupe = os.path.join(racine, "banc-coupe")
        coupure = None
        with mock.patch("os.replace", side_effect=OSError("coupure")):
            try:
                ecrire_critere2(rap, chemins, "x", {"commit": None, "modifie": False, "md5": {}}, coupe)
            except OSError as erreur:
                coupure = erreur
        ok("si l'écriture échoue, rien n'est affiché ; une coupure en posant les chemins ne laisse ni résultat sans eux, "
           "ni fichier temporaire",
           echec is not None and sortie_txt.getvalue() == "" and coupure is not None and os.listdir(coupe) == [])
        sortie_txt = io.StringIO()
        with contextlib.redirect_stdout(sortie_txt):
            lancer_le_critere2(**reglages)
        with open(os.path.join(banc_dir, NOM_RESULTAT), encoding="utf-8") as f:
            ecrit = json.load(f)
        with open(os.path.join(banc_dir, bm.NOM_MESURE), encoding="utf-8") as f:
            etape1 = json.load(f)
        ok("sur du code commité : écrit (les chemins d'abord, avec leur md5), PUIS affiché, relu dans le fichier ; le "
           "fichier garde le commit, les md5 des prévisions et des cours de l'étape 1, et les versions",
           sortie_txt.getvalue().startswith(ecrit["rapport"] + "\n") and ecrit["code"]["commit"] is not None
           and not ecrit["code"]["modifie"] and ecrit["chemins"]["md5"] == bm._md5(os.path.join(banc_dir, NOM_CHEMINS))
           and ecrit["previsions_etape1_md5"] == etape1["previsions"]["md5"] and ecrit["donnees"] == etape1["donnees"]
           and "scikit-learn" in ecrit["bibliotheques"] and "banc_argent.py" in ecrit["code"]["md5"])
        sortie_txt = io.StringIO()
        with contextlib.redirect_stdout(sortie_txt):
            lancer_le_critere2(**{**reglages, "dossier": os.path.join(racine, "inexistant")})
        ok("relancer --critere2 réaffiche le premier rapport, sans rien recalculer",
           "ne s'écrase pas" in sortie_txt.getvalue() and ecrit["rapport"] in sortie_txt.getvalue())
        refus_prev = refus_cours = None
        autre = os.path.join(racine, "banc-autre")
        shutil.copytree(banc_dir, autre)
        os.remove(os.path.join(autre, NOM_RESULTAT))
        with open(os.path.join(autre, bm.NOM_PREVISIONS), "ab") as f:
            f.write(b"\n")
        try:
            lancer_le_critere2(**{**reglages, "dossier_banc": autre})
        except CacheInvalide as erreur:
            refus_prev = str(erreur)
        base2 = os.path.join(racine, "base2")
        shutil.copytree(base, base2)
        with open(os.path.join(base2, "AAA_10y.csv"), "a", encoding="utf-8") as f:
            f.write("\n")                         # une ligne vide : les mêmes cours, un autre fichier
        autre2 = os.path.join(racine, "banc-autre2")
        shutil.copytree(banc_dir, autre2)
        os.remove(os.path.join(autre2, NOM_RESULTAT))
        try:
            lancer_le_critere2(**{**reglages, "dossier": base2, "dossier_banc": autre2})
        except CacheInvalide as erreur:
            refus_cours = str(erreur)
        ok("refusé si les prévisions ne sont plus celles de etape1.json, ou si un fichier de cours a changé (md5), même "
           "avec les mêmes cours ; rien d'écrit",
           refus_prev is not None and "prévisions" in refus_prev and refus_cours is not None
           and "fichiers de cours" in refus_cours and not os.path.exists(os.path.join(autre2, NOM_RESULTAT)))

    print("\n— La commande —")
    essai_cli = ("import runpy, sys, banc, banc_argent\n"
                 "def note(**k):\n    print('APPEL', k)\n    raise banc.CacheInvalide('un essai du critère 2')\n"
                 "banc_argent.lancer_le_critere2 = note\n"
                 "sys.argv = ['banc.py', '--critere2', '--a-blanc']\n"
                 "runpy.run_path(banc.__file__, run_name='__main__')\n")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
    r_cli = subprocess.run([sys.executable, "-c", essai_cli], cwd=banc.ROOT, capture_output=True, encoding="utf-8",
                           timeout=120, env=env)
    r_seul = subprocess.run([sys.executable, "banc.py", "--a-blanc"], cwd=banc.ROOT, capture_output=True,
                            encoding="utf-8", timeout=120, env=env)
    ok("en commande : --critere2 --a-blanc lance le critère 2 à blanc ; un cache refusé donne « Rien n'est calculé : … » "
       "et le code 1 ; --a-blanc seul est refusé",
       r_cli.returncode == 1 and "APPEL {'a_blanc': True}" in r_cli.stdout
       and "Rien n'est calculé : un essai du critère 2" in r_cli.stderr and "Traceback" not in r_cli.stderr
       and r_seul.returncode == 2 and "--a-blanc va avec --mesurer ou --critere2" in r_seul.stderr)

    print("\n— Le rapport —")
    faux = copy.deepcopy(rap)
    faux["a_blanc"] = False
    s, m_ = faux["resultats"][f"soir-{ECART}"], faux["resultats"][f"matin-{ECART}"]
    s["barre"], m_["barre"] = 701.23, 698.5
    s["methodes"]["a"].update(final=123.45, arret=pd.Timestamp("2021-03-04"), capital_arret=1.87, net=-12.5, impots=40.0,
                              frais=150.0, ecart=12.34, ordres=150)
    m_["methodes"]["a"].update(final=120.0, arret=None, capital_arret=None)
    texte = rendu(faux)
    ligne_a = next((l for l in texte.splitlines() if l.strip().startswith("a. le score technique ")), "")
    ok("la barre et le capital final de chaque candidate aux deux heures ; sa ligne : les deux capitaux, les ordres et les "
       "frais du soir (1 € par ordre plus l'écart : 162,34 €) ; « le panier des 6 » ; « Aucune candidate ne passe »",
       "a. le score technique : il lui fallait plus de 701,23 € le soir et de 698,50 € le matin ; elle a fini à "
       "123,45 € et 120,00 €." in texte and "Aucune candidate ne passe le critère 2." in texte
       and "123,45 €" in ligne_a and "120,00 €" in ligne_a and " 150 " in ligne_a and "162,34 €" in ligne_a
       and "le panier des 6 " in texte)
    ok("l'arrêt à 2 € daté, avec son capital ; un résultat négatif après l'impôt, dit en clair",
       "a. le score technique, le soir : arrêtée le 04/03/2021 avec 1,87 € : plus aucun ordre ensuite" in texte
       and "a. le score technique, le soir : tu aurais perdu plus que tes 200 € : 40,00 € d'impôt payés de ta poche" in texte
       and texte.count("tu aurais perdu plus que tes 200 €") == 1 and "a. le score technique, le matin : arrêtée" not in texte)
    passe = copy.deepcopy(faux)
    passe["passent"] = ["b"]
    passe["resultats"][f"soir-{ECART}"]["survivant"]["b"]["longues"] = [("CCC", 300, 0.5)]
    passe["resultats"][f"soir-{ECART}"]["valeurs"]["CCC"] = 812.5
    texte_p = rendu(passe)
    ok("une candidate qui passe : elle le fait aux deux heures, la porte A reste fermée, et « garder X seule faisait Y € »",
       "b. le momentum 12-1 finit au-dessus des deux repères, le soir et le matin : elle passe le critère 2. La porte A "
       "reste fermée (le critère 1) : rien de plus. Elle passe, mais garder CCC seule faisait 812,50 € (le soir)."
       in texte_p and "Aucune candidate ne passe" not in texte_p)
    ok("le tirage au sort et ce qu'il montre ; le rappel de la porte A, le biais du survivant, la fin",
       "faire mieux que lui montre surtout qu'on change moins souvent, pas un talent" in texte
       and "Le critère 2 ne rouvre pas la porte A" in texte and "biais du survivant" in texte
       and texte.endswith("Des faits sur le passé, pas une prévision ni un conseil."))

    print(f"\n{sum(cas)}/{len(cas)} vérifications réussies (critère 2)")
    return all(cas)
