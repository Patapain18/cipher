"""
Le mur des frais pendant la séance — de combien bouge une des 49 valeurs
pendant la séance de New York, face à ce que coûte un achat suivi d'une
vente chez le courtier.

CE QUE ÇA FAIT
    Sur les barres de 5 minutes de Yahoo (60 séances au plus : Yahoo ne garde
    pas ces barres plus longtemps), pour les 49 valeurs du bot :
      - le mouvement médian dans cinq fenêtres : le premier quart d'heure, les
        45 minutes suivantes, un quart d'heure et 45 minutes à tout moment, et
        la séance entière ;
      - la part des fois où une HAUSSE a dépassé ce qu'il faut pour gagner
        quelque chose avec un achat suivi d'une vente, avec 200 € et avec
        50 € (les frais du banc : `banc.rendement_necessaire`) ;
      - le mouvement médian de chaque valeur, dans chaque fenêtre ;
      - dans le cache journalier (data/market), de combien la part de la
        séance entière change d'une période de 60 séances à l'autre.
    `--mesurer` écrit data/seance.json (des COMPTES, jamais des parts toutes
    faites : une part ne peut pas contredire ses comptes) et
    data/seance-barres.csv.gz (les barres lues, pour un recompte à part).
    Le serveur relit la mesure par `section()` : il ne lit jamais Yahoo.

LES RÈGLES (le plan du 23/11/2026, validé par Mathis le même jour, fin de BRIEF.md)
    - Les barres de 5 minutes de Yahoo (period="60d", prepost=False,
      auto_adjust=False), une valeur à la fois (2 s de pause, 3 essais à
      10 s d'écart), les heures ramenées à New York. Une valeur avec deux
      barres à la même heure est refusée, comme une valeur que Yahoo refuse.
    - Seulement les séances d'avant aujourd'hui (date de New York) : une
      séance pas finie n'entre jamais. Les séances sont celles où au moins
      une valeur a des barres ; une valeur à qui il en manque une voit ses
      fenêtres de ce jour-là retirées.
    - Le mouvement d'une fenêtre : de l'Open de la barre de son début au
      Close de la barre de sa fin (celle qui commence 5 minutes avant la
      fin), fin / début − 1. La fenêtre compte si ces deux barres existent,
      avec des prix finis et positifs ; sinon elle est retirée, et comptée.
      Un trou au milieu ne la retire pas.
    - Ce qui paie : une HAUSSE strictement plus grande que le rendement
      nécessaire du banc. Une baisse ne paie jamais (on achète, puis on
      revend) ; une hausse égale au seuil rend juste l'argent placé.
    - Le mouvement médian : en valeur absolue (hausses et baisses mêlées),
      toutes les valeurs mêlées ; la médiane exacte.
    - Les textes : un seul arrondi, au plus proche, une moitié pile vers le
      haut, sur la valeur exacte de chaque nombre (Fraction).

CE QUE ÇA NE FAIT PAS
    Aucune règle pour deviner le sens, aucun conseil, aucune prédiction :
    seulement la taille des mouvements passés.

LANCER (avec /usr/bin/python3 : pandas et numpy sont dans le dossier de l'utilisateur, pas de -I)
    python3 seance.py --mesurer    # lit Yahoo (environ 2 min), puis écrit les deux fichiers
    python3 seance.py --afficher   # le rapport de la mesure écrite (sans réseau)
    python3 seance.py --verifier   # les cas calculés à la main (sans réseau)
"""

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from fractions import Fraction
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

MESURE = os.path.join(ROOT, "data", "seance.json")
BARRES = os.path.join(ROOT, "data", "seance-barres.csv.gz")
CACHE_JOURNALIER = os.path.join(ROOT, "data", "market")   # les cours journaliers du banc, figés

VERSION = 1
CAPITAUX = (200.0, 50.0)     # la stratégie de 200 € du banc, et 50 €
VALEURS_MIN = 45             # en dessous, rien n'est écrit (« Yahoo n'a pas donné assez de barres »)
SEANCES_MIN = 20
PAUSE = 2                    # secondes entre deux valeurs : Yahoo refuse les rafales
ESSAIS = 3
ATTENTE = 10                 # secondes entre deux essais pour une même valeur
PERIODE = 60                 # séances : la variation dans le cache journalier

NY = ZoneInfo("America/New_York")
PARIS = ZoneInfo("Europe/Paris")
OUVERTURE_NY = 9 * 60 + 30   # en minutes depuis minuit, à New York
FIN_NY = 16 * 60
DECALAGE_PARIS = 6 * 60      # Paris a 6 h d'avance, sauf quelques semaines par an (dit dans une note)

# Les cinq fenêtres : (début en minutes à New York, durée en minutes), une ou plusieurs par séance.
FENETRES = {
    "ouverture": ((OUVERTURE_NY, 15),),
    "suivantes": ((OUVERTURE_NY + 15, 45),),
    "quart": tuple((OUVERTURE_NY + 15 * i, 15) for i in range(26)),
    "tranche": tuple((OUVERTURE_NY + 15 * i, 45) for i in range(24)),
    "seance": ((OUVERTURE_NY, FIN_NY - OUVERTURE_NY),),
}
NOMS = {
    "ouverture": "Le premier quart d'heure",
    "suivantes": "Les 45 minutes suivantes",
    "quart": "Un quart d'heure",
    "tranche": "45 minutes",
    "seance": "Toute la séance",
}
A_TOUT_MOMENT = ("quart", "tranche")
SYMBOLE = re.compile(r"[A-Z][A-Z.\-]{0,5}")

NBSP = " "     # l'espace insécable de PP.pct (avant « % », « : », « ; », « € »)
MILLIERS = " "  # l'espace fine des milliers, comme toLocaleString('fr-FR')
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre")


class MesureIllisible(ValueError):
    """La mesure écrite n'a pas la forme attendue ; le message dit pourquoi, en français."""


def _insecables(texte):
    """La typographie française : une espace insécable avant « : », « ; », « % », « € » et « » », après « « »."""
    for avant, apres in ((" :", NBSP + ":"), (" ;", NBSP + ";"), (" %", NBSP + "%"), (" €", NBSP + "€"),
                         ("« ", "«" + NBSP), (" »", NBSP + "»")):
        texte = texte.replace(avant, apres)
    return texte


# ══════════════════════════════════════════════════════════════════
#  LES MOUVEMENTS (purs : aucun réseau, vérifiés à la main par --verifier)
# ══════════════════════════════════════════════════════════════════

def preparer(barres):
    """
    Les barres d'une valeur, telles que Yahoo les rend (Open, Close, index
    avec fuseau), ramenées à New York : {jour: {minute: (open, close)}}.
    Seules comptent les barres calées sur 5 minutes entières, de 9 h 30 à
    15 h 55 (prepost=False n'en rend pas d'autres). Des heures sans fuseau,
    ou deux barres à la même heure : ValueError (la valeur est refusée).
    """
    index = barres.index
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        raise ValueError("des heures sans fuseau")
    if index.duplicated().any():
        raise ValueError("deux barres à la même heure")
    ouvertures = barres["Open"].to_numpy(dtype=float)
    clotures = barres["Close"].to_numpy(dtype=float)
    jours = {}
    for t, o, c in zip(index.tz_convert(NY), ouvertures, clotures):
        minute = t.hour * 60 + t.minute
        if t.second or t.microsecond or minute % 5 or not OUVERTURE_NY <= minute < FIN_NY:
            continue
        jours.setdefault(t.date(), {})[minute] = (float(o), float(c))
    return jours


def _prix(x):
    """Un prix utilisable : fini et plus grand que zéro."""
    return math.isfinite(x) and x > 0


def mouvements(jours, seances):
    """
    Une fonction pure. Pour une valeur (`preparer`) et les séances retenues :
      {"mouvements": {fenetre: [(jour, mouvement signé), …]},
       "retirees": {fenetre: {jour: nombre de fenêtres retirées}}}
    Le mouvement : du prix d'ouverture de la barre du début au prix de
    clôture de la barre de la fin (celle qui commence 5 minutes avant la
    fin). Une séance sans aucune barre pour cette valeur : toutes ses
    fenêtres de ce jour-là sont retirées.
    """
    faits = {f: [] for f in FENETRES}
    retirees = {f: {} for f in FENETRES}
    for jour in seances:
        b = jours.get(jour, {})
        for f, liste in FENETRES.items():
            for debut, duree in liste:
                a, z = b.get(debut), b.get(debut + duree - 5)
                if a is None or z is None or not _prix(a[0]) or not _prix(z[1]):
                    retirees[f][jour] = retirees[f].get(jour, 0) + 1
                else:
                    faits[f].append((jour, z[1] / a[0] - 1))
    return {"mouvements": faits, "retirees": retirees}


def paie(mouvement, seuil):
    """
    Un achat suivi d'une vente gagne-t-il quelque chose ? Seulement sur une
    HAUSSE strictement plus grande que le seuil : une baisse ne paie jamais
    (on achète, puis on revend), et une hausse égale rend juste l'argent placé.
    """
    return mouvement > seuil


def mediane(valeurs):
    """La médiane exacte, en Fraction : celle du milieu, ou la moyenne des deux du milieu. None sans valeur."""
    v = sorted(Fraction(x) for x in valeurs)
    n = len(v)
    if not n:
        return None
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def _flottant(x):
    return None if x is None else float(x)


def variation(journalier, seuil, periode=PERIODE):
    """
    Dans le cache journalier ({valeur: DataFrame avec Open et Close, une
    ligne par séance}) : la part des hausses de plus de `seuil` de
    l'ouverture à la clôture d'une même séance, toutes les valeurs mêlées,
    pour chaque période de `periode` séances qui se suivent.
    Renvoie la plus basse et la plus haute ([hausses, comptées, dernière
    séance de la période], à égalité la première) et la médiane ; None
    s'il y a moins de `periode` séances.
    """
    rapports = {}
    for valeur, d in journalier.items():
        o = pd.to_numeric(d["Open"], errors="coerce")
        c = pd.to_numeric(d["Close"], errors="coerce")
        bons = np.isfinite(o) & np.isfinite(c) & (o > 0) & (c > 0)
        rapports[valeur] = (c / o - 1).where(bons)
    if not rapports:
        return None
    df = pd.DataFrame(rapports).sort_index()
    if len(df) < periode:
        return None
    hausses = np.concatenate([[0], np.cumsum([sum(1 for x in ligne if paie(x, seuil))
                                              for ligne in df.to_numpy(dtype=float)])])
    comptees = np.concatenate([[0], np.cumsum(df.notna().sum(axis=1).to_numpy(dtype=int))])
    periodes = []
    for fin in range(periode, len(df) + 1):
        h, n = int(hausses[fin] - hausses[fin - periode]), int(comptees[fin] - comptees[fin - periode])
        if n:
            periodes.append((Fraction(h, n), h, n, df.index[fin - 1]))
    if not periodes:
        return None
    bas = min(periodes, key=lambda p: p[0])
    haut = max(periodes, key=lambda p: p[0])
    jour = lambda d: d.isoformat() if isinstance(d, date) else pd.Timestamp(d).date().isoformat()  # noqa: E731
    return {"debut": jour(df.index[0]), "fin": jour(df.index[-1]), "periode": periode, "valeurs": len(rapports),
            "min": [bas[1], bas[2], jour(bas[3])], "max": [haut[1], haut[2], jour(haut[3])],
            "mediane": float(mediane(p[0] for p in periodes))}


def mesurer(barres, maintenant, refusees=None, journalier=None):
    """
    Les comptes de la mesure, prêts pour data/seance.json.
      barres : {valeur: DataFrame à la Yahoo (Open, Close, index avec fuseau)} ;
      maintenant : l'heure de la mesure, avec son fuseau ; les séances de ce
        jour-là (à New York) et d'après sont ignorées ;
      refusees : {valeur: raison} pour les valeurs que Yahoo a refusées ;
      journalier : {valeur: DataFrame journalier} pour la variation, ou None.
    Les seuils viennent du banc (`banc.rendement_necessaire`, importé ici
    seulement : le serveur, qui ne fait que relire, ne l'importe jamais).
    Lève ValueError s'il reste moins de VALEURS_MIN valeurs ou SEANCES_MIN séances.
    """
    import banc
    aujourd_hui = maintenant.astimezone(NY).date()
    refusees = dict(refusees or {})
    preparees = {}
    for valeur in sorted(barres):
        try:
            p = preparer(barres[valeur])
        except (ValueError, KeyError, TypeError) as e:
            refusees[valeur] = str(e) or type(e).__name__
            continue
        if not any(j < aujourd_hui for j in p):
            refusees[valeur] = "aucune barre avant aujourd'hui"
            continue
        preparees[valeur] = p
    seances = sorted({j for p in preparees.values() for j in p if j < aujourd_hui})
    if len(preparees) < VALEURS_MIN or len(seances) < SEANCES_MIN:
        raise ValueError(f"Yahoo n'a pas donné assez de barres : {len(preparees)} valeurs (il en faut "
                         f"{VALEURS_MIN}) et {len(seances)} séances (il en faut {SEANCES_MIN})")
    seuils = [banc.rendement_necessaire(c) for c in CAPITAUX]
    couts = [banc.cout_aller_retour(c) for c in CAPITAUX]
    faits = {v: mouvements(p, seances) for v, p in preparees.items()}
    fenetres, par_jour = {}, {}
    for f in FENETRES:
        tous = [x for r in faits.values() for _, x in r["mouvements"][f]]
        retirees = 0
        for r in faits.values():
            for jour, n in r["retirees"][f].items():
                retirees += n
                compte = par_jour.setdefault(jour.isoformat(), {})
                compte[f] = compte.get(f, 0) + n
        fenetres[f] = {
            "comptes": len(tous),
            "hausses": [sum(1 for x in tous if paie(x, s)) for s in seuils],
            "mediane": _flottant(mediane(abs(x) for x in tous)),
            "retirees": retirees,
            "par_valeur": {v: _flottant(mediane(abs(x) for _, x in r["mouvements"][f])) for v, r in faits.items()},
        }
    ouverture = [(j, x) for r in faits.values() for j, x in r["mouvements"]["ouverture"]]
    return {
        "version": VERSION,
        "date_mesure": maintenant.astimezone(PARIS).isoformat(timespec="seconds"),
        "aujourd_hui": aujourd_hui.isoformat(),
        "seances": len(seances), "debut": seances[0].isoformat(), "fin": seances[-1].isoformat(),
        "valeurs": sorted(preparees), "refusees": dict(sorted(refusees.items())),
        "capitaux": list(CAPITAUX), "seuils": seuils, "couts": couts, "ecart": float(banc.ECART),
        "fenetres": fenetres,
        "retirees_par_jour": dict(sorted(par_jour.items())),
        "ouverture_par_jour": {"jours": len({j for j, _ in ouverture}),
                               "avec_une": len({j for j, x in ouverture if paie(x, seuils[0])})},
        "variation": variation({v: journalier[v] for v in preparees if v in journalier}, seuils[0]) if journalier else None,
    }


# ══════════════════════════════════════════════════════════════════
#  LA RELECTURE DE LA MESURE ÉCRITE (celle du serveur)
# ══════════════════════════════════════════════════════════════════

def _exige(condition, raison):
    if not condition:
        raise MesureIllisible(raison)


def _entier(x, quoi, mini=0):
    _exige(isinstance(x, int) and not isinstance(x, bool) and x >= mini, f"{quoi} n'est pas un entier d'au moins {mini}")
    return x


def _fini(x, quoi, mini=None):
    _exige(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x), f"{quoi} n'est pas un nombre fini")
    _exige(mini is None or x >= mini, f"{quoi} est trop petit")
    return x


def _jour(x, quoi):
    _exige(isinstance(x, str), f"{quoi} n'est pas une date")
    try:
        d = date.fromisoformat(x)
    except ValueError:
        raise MesureIllisible(f"{quoi} n'est pas une date écrite en entier") from None
    _exige(d.isoformat() == x, f"{quoi} n'est pas une date écrite en entier")
    return d


def _symbole(x):
    return isinstance(x, str) and SYMBOLE.fullmatch(x) is not None


def valider(m):
    """
    Refuse ce qui n'a pas la forme d'une mesure (MesureIllisible, la raison en
    français). Les comptes doivent se tenir : pour chaque fenêtre, comptées +
    retirées = séances × valeurs × fenêtres par séance, et les retirées jour
    par jour font le total.
    """
    _exige(isinstance(m, dict), "ce n'est pas un objet JSON")
    _exige(m.get("version") == VERSION, "version inconnue")
    dm = m.get("date_mesure")
    _exige(isinstance(dm, str) and dm[10:11] == "T", "l'heure de la mesure est illisible")
    try:
        moment = datetime.fromisoformat(dm)
    except ValueError:
        raise MesureIllisible("l'heure de la mesure est illisible") from None
    _exige(moment.tzinfo is not None, "l'heure de la mesure n'a pas de fuseau")
    aujourd_hui = _jour(m.get("aujourd_hui"), "la date de la mesure")
    _exige(moment.astimezone(NY).date() == aujourd_hui, "la date de la mesure ne suit pas son heure")
    seances = _entier(m.get("seances"), "le nombre de séances", SEANCES_MIN)
    debut, fin = _jour(m.get("debut"), "la première séance"), _jour(m.get("fin"), "la dernière séance")
    _exige(debut <= fin < aujourd_hui, "les dates des séances ne se suivent pas")
    valeurs = m.get("valeurs")
    _exige(isinstance(valeurs, list) and all(_symbole(v) for v in valeurs), "la liste des valeurs n'a pas la bonne forme")
    _exige(len(set(valeurs)) == len(valeurs) and len(valeurs) >= VALEURS_MIN,
           f"il faut au moins {VALEURS_MIN} valeurs différentes")
    refusees = m.get("refusees")
    _exige(isinstance(refusees, dict) and all(_symbole(k) and isinstance(r, str) for k, r in refusees.items()),
           "la liste des valeurs refusées n'a pas la bonne forme")
    _exige(not set(refusees) & set(valeurs), "une valeur est à la fois mesurée et refusée")
    _exige(m.get("capitaux") == list(CAPITAUX), "les capitaux ne sont pas 200 € et 50 €")
    for cle in ("seuils", "couts"):
        _exige(isinstance(m.get(cle), list) and len(m[cle]) == 2, f"« {cle} » n'a pas deux nombres")
        for x in m[cle]:
            _fini(x, f"un nombre de « {cle} »")
            _exige(x > 0, f"un nombre de « {cle} » n'est pas positif")
    _exige(m["seuils"][0] < m["seuils"][1] < 1, "les seuils ne sont pas dans l'ordre")
    _fini(m.get("ecart"), "l'écart du courtier", 0)
    _exige(m["ecart"] < 0.1, "l'écart du courtier est trop grand")
    fenetres = m.get("fenetres")
    _exige(isinstance(fenetres, dict) and set(fenetres) == set(FENETRES), "les cinq fenêtres ne sont pas là")
    for f, liste in FENETRES.items():
        x = fenetres[f]
        _exige(isinstance(x, dict), f"la fenêtre « {f} » n'a pas la bonne forme")
        n = _entier(x.get("comptes"), f"les comptes de « {f} »", 1)
        _exige(isinstance(x.get("hausses"), list) and len(x["hausses"]) == 2, f"les hausses de « {f} » n'ont pas deux comptes")
        h200, h50 = (_entier(h, f"les hausses de « {f} »") for h in x["hausses"])
        _exige(h50 <= h200 <= n, f"les hausses de « {f} » ne suivent pas ses comptes")
        _fini(x.get("mediane"), f"la médiane de « {f} »", 0)
        retirees = _entier(x.get("retirees"), f"les fenêtres retirées de « {f} »")
        _exige(n + retirees == seances * len(valeurs) * len(liste),
               f"« {f} » : les fenêtres comptées et retirées ne font pas le total")
        pv = x.get("par_valeur")
        _exige(isinstance(pv, dict) and sorted(pv) == sorted(valeurs), f"« {f} » n'a pas une médiane par valeur")
        for y in pv.values():
            _exige(y is None or (_fini(y, f"une médiane de « {f} »", 0) is not None), f"une médiane de « {f} » est illisible")
    par_jour = m.get("retirees_par_jour")
    _exige(isinstance(par_jour, dict), "les fenêtres retirées jour par jour n'ont pas la bonne forme")
    totaux = dict.fromkeys(FENETRES, 0)
    for jour, comptes in par_jour.items():
        _exige(debut <= _jour(jour, "un jour de fenêtres retirées") <= fin, "un jour de fenêtres retirées est hors des séances")
        _exige(isinstance(comptes, dict) and set(comptes) <= set(FENETRES), "les fenêtres retirées d'un jour n'ont pas la bonne forme")
        for f, k in comptes.items():
            totaux[f] += _entier(k, "un compte de fenêtres retirées", 1)
    _exige(all(totaux[f] == fenetres[f]["retirees"] for f in FENETRES),
           "les fenêtres retirées jour par jour ne font pas le total")
    opj = m.get("ouverture_par_jour")
    _exige(isinstance(opj, dict), "les jours de l'ouverture n'ont pas la bonne forme")
    jours = _entier(opj.get("jours"), "les jours de l'ouverture", 1)
    avec = _entier(opj.get("avec_une"), "les jours avec une hausse")
    h0 = fenetres["ouverture"]["hausses"][0]
    _exige(jours <= seances and avec <= jours and avec <= h0 and (avec == 0) == (h0 == 0),
           "les jours de l'ouverture ne suivent pas ses comptes")
    v = m.get("variation")
    if v is not None:
        _exige(isinstance(v, dict), "la variation n'a pas la bonne forme")
        d0, d1 = _jour(v.get("debut"), "le début du cache journalier"), _jour(v.get("fin"), "la fin du cache journalier")
        _exige(d0 < d1, "les dates du cache journalier ne se suivent pas")
        _entier(v.get("periode"), "la période de la variation", 1)
        _entier(v.get("valeurs"), "le nombre de valeurs de la variation", 1)
        for cle in ("min", "max"):
            x = v.get(cle)
            _exige(isinstance(x, list) and len(x) == 3, f"la variation « {cle} » n'a pas la bonne forme")
            h, n = _entier(x[0], "une hausse de la variation"), _entier(x[1], "un compte de la variation", 1)
            _exige(h <= n and d0 <= _jour(x[2], "une date de la variation") <= d1, f"la variation « {cle} » ne se tient pas")
        _exige(Fraction(v["min"][0], v["min"][1]) <= Fraction(v["max"][0], v["max"][1]), "la variation est à l'envers")
        _fini(v.get("mediane"), "la médiane de la variation", 0)
    _exige(isinstance(m.get("commit"), str) and isinstance(m.get("modifie"), bool) and isinstance(m.get("yfinance"), str),
           "la provenance de la mesure n'a pas la bonne forme")
    _exige(isinstance(m.get("md5_barres"), str) and re.fullmatch(r"[0-9a-f]{32}", m["md5_barres"]) is not None,
           "l'empreinte des barres n'a pas la bonne forme")
    return m


def lire(chemin=MESURE):
    """La mesure écrite, vérifiée (MesureIllisible sinon ; FileNotFoundError si elle n'existe pas)."""
    try:
        with open(chemin, encoding="utf-8") as f:
            m = json.load(f)
    except UnicodeDecodeError:
        raise MesureIllisible("ce n'est pas du texte UTF-8") from None
    except json.JSONDecodeError:
        raise MesureIllisible("ce n'est pas du JSON valide") from None
    return valider(m)


def section(chemin=MESURE):
    """
    Ce que /api/seance renvoie, sans jamais lever : {"etat": "mesure", "textes": …},
    {"etat": "absent", "message": …} ou {"etat": "illisible", "detail": …, "message": …}.
    """
    if not os.path.exists(chemin):
        return {"etat": "absent", "message": _insecables(
            f"Le mur des frais pendant la séance n'est pas encore mesuré ({os.path.relpath(chemin, ROOT)} n'existe pas).")}
    if not os.path.isfile(chemin):      # un dossier, ou un tube qui bloquerait open() pour toujours
        raison = "ce n'est pas un fichier"
    else:
        try:
            m = lire(chemin)
            return {"etat": "mesure", "date_mesure": m["date_mesure"], "textes": textes(m)}
        except MesureIllisible as e:
            raison = str(e)
        except OSError:
            raison = "le fichier ne se lit pas"
        except Exception as e:      # un fichier abîmé ne fait jamais tomber la route
            raison = f"erreur inattendue ({type(e).__name__})"
    return {"etat": "illisible", "detail": raison, "message": _insecables(
        f"La mesure du mur des frais pendant la séance est illisible ({raison}).")}


# ══════════════════════════════════════════════════════════════════
#  LES TEXTES (une seule fonction par texte affiché ; la page ne fait
#  que les insérer, et --verifier les teste tous)
# ══════════════════════════════════════════════════════════════════

def _arrondi(x, decimales):
    """x ≥ 0, valeur exacte, arrondi au plus proche, une moitié pile vers le haut : un entier en 10^−decimales."""
    return math.floor(Fraction(x) * 10 ** decimales + Fraction(1, 2))


def _ecrire(unites, decimales):
    """(1234, 2) → « 12,34 » ; (7, 1) → « 0,7 » ; (5, 0) → « 5 »."""
    s = str(unites).rjust(decimales + 1, "0")
    entier = s[:-decimales] if decimales else s
    entier = f"{int(entier):,}".replace(",", MILLIERS)
    return f"{entier},{s[-decimales:]}" if decimales else entier


def texte_pct(x, decimales):
    """Une fraction de 1 en pourcentage : (0.0052, 2) → « 0,52 % » (x ≥ 0)."""
    return f"{_ecrire(_arrondi(Fraction(x) * 100, decimales), decimales)}{NBSP}%"


def texte_part(h, n):
    """
    La part h / n, au dixième de point : « 9,8 % ». Jamais « 0,0 % » ni
    « 100,0 % » trompeurs : « 0 % » si h = 0, « moins de 0,1 % » si elle
    s'écrirait 0,0 sans être nulle, « 100 % » si h = n, « plus de 99,9 % »
    si elle s'écrirait 100,0 sans être pleine.
    """
    if h == 0:
        return f"0{NBSP}%"
    if h == n:
        return f"100{NBSP}%"
    unites = _arrondi(Fraction(100 * h, n), 1)
    if unites == 0:
        return f"moins de 0,1{NBSP}%"
    if unites == 1000:
        return f"plus de 99,9{NBSP}%"
    return f"{_ecrire(unites, 1)}{NBSP}%"


def texte_seuil(x):
    """Un rendement nécessaire, au millième de point : 0.0110608… → « 1,106 % »."""
    return texte_pct(x, 3)


def texte_euros(x):
    """2.1989… → « 2,20 € »."""
    return f"{_ecrire(_arrondi(x, 2), 2)}{NBSP}€"


def texte_capital(x):
    """200.0 → « 200 € » (un montant rond sans ses centimes)."""
    return f"{_ecrire(int(x), 0)}{NBSP}€" if float(x).is_integer() else texte_euros(x)


def texte_moyenne(h, n):
    """La moyenne h / n au dixième : (288, 60) → « 4,8 »."""
    return _ecrire(_arrondi(Fraction(h, n), 1), 1)


def texte_date(d):
    """date(2026, 8, 28) → « 28 août 2026 » ; le 1er : « 1ᵉʳ septembre 2026 »."""
    return f"{'1ᵉʳ' if d.day == 1 else d.day} {MOIS[d.month - 1]} {d.year}"


def texte_date_courte(d):
    """date(2026, 11, 5) → « 05/11/2026 »."""
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def texte_mois(d):
    """date(2016, 9, 6) → « septembre 2016 »."""
    return f"{MOIS[d.month - 1]} {d.year}"


def texte_heure(minutes_ny):
    """Une heure de New York (en minutes), écrite à l'heure de Paris : 570 → « 15 h 30 », 960 → « 22 h »."""
    m = minutes_ny + DECALAGE_PARIS
    return f"{m // 60}{NBSP}h{NBSP}{m % 60:02d}" if m % 60 else f"{m // 60}{NBSP}h"


def texte_moment(iso):
    """Une heure de mesure, à Paris : « 23 novembre 2026 à 13 h 40 »."""
    p = datetime.fromisoformat(iso).astimezone(PARIS)
    return f"{texte_date(p.date())} à {p.hour}{NBSP}h{NBSP}{p.minute:02d}"


def precision(f):
    """Les heures d'une fenêtre, à Paris : « 15 h 30 – 15 h 45 », ou « à tout moment »."""
    if f in A_TOUT_MOMENT:
        return "à tout moment"
    (debut, duree), = FENETRES[f]
    return f"{texte_heure(debut)}{NBSP}– {texte_heure(debut + duree)}"


def _liste(mots):
    return mots[0] if len(mots) == 1 else f"{', '.join(mots[:-1])} et {mots[-1]}"


def textes(m):
    """Tous les textes de la section, calculés depuis les comptes de la mesure (vérifiée avant)."""
    fen, nv, s = m["fenetres"], len(m["valeurs"]), m["seances"]
    (s200, s50), (c200, c50) = m["seuils"], m["capitaux"]
    cap200, cap50 = texte_capital(c200), texte_capital(c50)
    debut, fin = date.fromisoformat(m["debut"]), date.fromisoformat(m["fin"])
    o, e = fen["ouverture"], fen["seance"]
    rien = lambda x: texte_part(x["comptes"] - x["hausses"][0], x["comptes"])   # noqa: E731
    jours, avec = m["ouverture_par_jour"]["jours"], m["ouverture_par_jour"]["avec_une"]
    if avec:
        une = (f"Au moins une a monté assez dès le premier quart d'heure {avec} jour{'s' if avec > 1 else ''} "
               f"sur {jours}, mais {texte_moyenne(o['hausses'][0], jours)} sur {nv} en moyenne : il fallait savoir "
               "d'avance lesquelles.")
    else:
        une = f"Aucune n'a monté assez dès le premier quart d'heure, sur {jours} séance{'s' if jours > 1 else ''}."
    v = m["variation"]
    periode = "" if v is None else (
        f" La part des hausses de plus de {texte_seuil(s200)} change beaucoup d'une période à l'autre : sur la séance "
        f"entière, les cours journaliers donnent de {texte_part(v['min'][0], v['min'][1])} à "
        f"{texte_part(v['max'][0], v['max'][1])} selon la période de {v['periode']} séances, entre "
        f"{texte_mois(date.fromisoformat(v['debut']))} et {texte_mois(date.fromisoformat(v['fin']))}.")
    notes = [
        "Le mouvement médian mêle les hausses et les baisses : un achat suivi d'une vente ne gagne que sur une hausse.",
        f"{s} séances seulement (Yahoo ne garde pas les barres de 5 minutes plus longtemps), et les {nv} valeurs "
        f"bougent ensemble : un sondage, pas une loi.{periode}",
        f"En dollars, aux cours de New York. L'écart du courtier, vu à {texte_pct(m['ecart'], 1)} un matin, n'a pas "
        "été mesuré à l'ouverture, où il peut être plus large.",
        "Les heures sont celles de Paris. Quelques semaines par an, au changement d'heure, la séance de New York "
        "commence une heure plus tôt à Paris : les fenêtres suivent New York.",
    ]
    if m["refusees"]:
        notes.append(f"Yahoo n'a pas donné les barres de {_liste(sorted(m['refusees']))} : la mesure porte sur {nv} valeurs.")
    notes.append(f"Mesuré le {texte_moment(m['date_mesure'])}, sur les barres de 5 minutes de Yahoo. Des faits sur "
                 "le passé, pas une prévision ni un conseil.")
    t = {
        "pastille": f"{s} séances",
        "intro": (f"Le banc a mesuré le soir pour le lendemain ; voici l'intérieur de la séance. Avec {cap200}, un "
                  f"achat puis une vente coûtent {texte_euros(m['couts'][0])} quand le cours ne bouge pas : il faut "
                  f"une hausse de plus de {texte_seuil(s200)} pour gagner quelque chose (plus de "
                  f"{texte_seuil(s50)} avec {cap50})."),
        "legende": (f"Une des {nv} valeurs, prise au hasard, sur les {s} dernières séances (du {texte_date(debut)} "
                    f"au {texte_date(fin)})."),
        "colonnes": ["Fenêtre", "Mouvement médian", f"Une hausse de plus de {texte_seuil(s200)} ({cap200})",
                     f"De plus de {texte_seuil(s50)} ({cap50})"],
        "lignes": [{"cle": f, "nom": NOMS[f], "precision": precision(f), "mediane": texte_pct(fen[f]["mediane"], 2),
                    "parts": [texte_part(h, fen[f]["comptes"]) for h in fen[f]["hausses"]]} for f in FENETRES],
        "conclusion": (f"Achetée au hasard avec {cap200}, une des {nv} n'a donc rien rapporté {rien(o)} des fois dans "
                       f"le premier quart d'heure, et {rien(e)} des fois sur la séance entière. {une}"),
        "detail": "Valeur par valeur",
        "entete_valeur": "Valeur",
        "colonnes_valeurs": [{"nom": NOMS[f], "precision": precision(f)} for f in FENETRES],
        "valeurs": [{"valeur": val, "medianes": [("—" if fen[f]["par_valeur"][val] is None
                                                  else texte_pct(fen[f]["par_valeur"][val], 2)) for f in FENETRES]}
                    for val in sorted(m["valeurs"])],
        "legende_valeurs": f"Le mouvement médian de chaque valeur, hausses et baisses mêlées, sur les {s} dernières séances.",
        "notes": notes,
    }
    for cle in ("pastille", "intro", "legende", "conclusion", "legende_valeurs"):
        t[cle] = _insecables(t[cle])
    t["colonnes"] = [_insecables(x) for x in t["colonnes"]]
    t["notes"] = [_insecables(x) for x in t["notes"]]
    return t


def rapport(m):
    """Le texte du terminal : les textes de la page, puis les comptes, les fenêtres retirées et la provenance."""
    t, fen = textes(m), m["fenetres"]
    d0, d1 = date.fromisoformat(m["debut"]), date.fromisoformat(m["fin"])
    lignes = [f"LE MUR DES FRAIS PENDANT LA SÉANCE — mesuré le {texte_moment(m['date_mesure'])}",
              f"{len(m['valeurs'])} valeurs, {m['seances']} séances (du {texte_date_courte(d0)} au "
              f"{texte_date_courte(d1)}), barres de 5 minutes de Yahoo.", "", t["intro"], "", t["legende"]]
    noms = [f"{x['nom']}, {x['precision']}" for x in t["lignes"]]
    large = max(len(n) for n in noms)
    lignes.append(f"  {'Fenêtre':<{large}}  {'Médiane':>8}  {'> seuil 200 €':>16}  {'> seuil 50 €':>16}  comptes")
    for nom, x, f in zip(noms, t["lignes"], FENETRES):
        c = fen[f]
        lignes.append(f"  {nom:<{large}}  {x['mediane']:>8}  {x['parts'][0]:>16}  {x['parts'][1]:>16}  "
                      f"{c['hausses'][0]} et {c['hausses'][1]} hausses sur {c['comptes']} ({c['retirees']} retirées)")
    lignes += ["", t["conclusion"], "", f"{t['detail']} (le mouvement médian) :",
               "  " + " ".join(["      "] + [f"{f:>12}" for f in FENETRES])]
    for x in t["valeurs"]:
        lignes.append("  " + " ".join([f"{x['valeur']:<6}"] + [f"{y:>12}" for y in x["medianes"]]))
    lignes += ["", "Fenêtres retirées, jour par jour :"]
    for jour, comptes in m["retirees_par_jour"].items():
        lignes.append(f"  {texte_date_courte(date.fromisoformat(jour))} : "
                      + ", ".join(f"{n} « {f} »" for f, n in comptes.items()))
    if not m["retirees_par_jour"]:
        lignes.append("  aucune")
    lignes.append("Valeurs refusées par Yahoo : " + (", ".join(f"{k} ({r})" for k, r in m["refusees"].items()) or "aucune"))
    v = m["variation"]
    if v:
        lignes.append(f"Variation (cache journalier, {v['valeurs']} valeurs, du {v['debut']} au {v['fin']}, par "
                      f"{v['periode']} séances) : de {texte_part(*v['min'][:2])} (période finie le {v['min'][2]}) à "
                      f"{texte_part(*v['max'][:2])} (finie le {v['max'][2]}), médiane {texte_pct(v['mediane'], 1)}.")
    lignes += ["", "Notes :"] + [f"  · {n}" for n in t["notes"]]
    lignes.append(f"\nCommit {m['commit']}{' (seance.py modifié)' if m['modifie'] else ''}, yfinance {m['yfinance']}, "
                  f"md5 des barres {m['md5_barres']}.")
    return "\n".join(lignes)


# ══════════════════════════════════════════════════════════════════
#  YAHOO, LE CACHE JOURNALIER ET L'ÉCRITURE
# ══════════════════════════════════════════════════════════════════

def lire_barres(tickers, dormir=time.sleep):
    """
    Les barres de 5 minutes de chaque valeur, une à la fois (PAUSE secondes
    entre deux : Yahoo refuse les rafales, et le bot le lit toutes les 5
    minutes), ESSAIS essais à ATTENTE secondes d'écart. Renvoie
    ({valeur: DataFrame (Open, Close)}, {valeur refusée: raison}).
    """
    import yfinance as yf
    barres, refusees = {}, {}
    for i, t in enumerate(tickers):
        if i:
            dormir(PAUSE)
        erreur = None
        for n in range(ESSAIS):
            try:
                h = yf.Ticker(t).history(period="60d", interval="5m", prepost=False, auto_adjust=False)
                if h is not None and not h.empty:
                    barres[t] = h[["Open", "Close"]]
                    break
                erreur = "réponse vide"
            except Exception as e:          # yfinance lève des erreurs variées quand Yahoo refuse
                erreur = str(e) or type(e).__name__
            if n < ESSAIS - 1:
                dormir(ATTENTE)
        else:
            refusees[t] = f"{ESSAIS} essais : {erreur}"
    return barres, refusees


def lire_journalier(valeurs, dossier=CACHE_JOURNALIER):
    """Le cache journalier du banc (Open et Close), pour les valeurs qui y sont ; un fichier illisible est ignoré."""
    journalier = {}
    for v in valeurs:
        chemin = os.path.join(dossier, f"{v}_10y.csv")
        if not os.path.isfile(chemin):
            continue
        try:
            d = pd.read_csv(chemin, index_col=0, float_precision="round_trip")
            d.index = pd.Index(pd.to_datetime(d.index, utc=True).date)
            journalier[v] = d[~d.index.duplicated(keep="last")][["Open", "Close"]].sort_index()
        except Exception:       # un cache abîmé ne fait rien tomber : la variation se passe de cette valeur
            continue
    return journalier


def csv_des_barres(barres):
    """Le CSV des barres lues : valeur, heure (UTC, écrite en entier avec son fuseau), open, close, sans perte (repr)."""
    sortie = io.StringIO()
    w = csv.writer(sortie, lineterminator="\n")
    w.writerow(["valeur", "heure", "open", "close"])
    for v in sorted(barres):
        d = barres[v]
        for t, o, c in zip(d.index.tz_convert("UTC"), d["Open"].to_numpy(dtype=float), d["Close"].to_numpy(dtype=float)):
            w.writerow([v, t.isoformat(), repr(float(o)), repr(float(c))])
    return sortie.getvalue()


def relire_barres(chemin=BARRES):
    """Les barres d'un fichier écrit par `ecrire` : {valeur: DataFrame (Open, Close, index en UTC)}."""
    d = pd.read_csv(chemin, float_precision="round_trip", dtype={"valeur": str, "heure": str})
    d.index = pd.DatetimeIndex(pd.to_datetime(d["heure"], utc=True))
    d = d.rename(columns={"open": "Open", "close": "Close"})
    return {v: g[["Open", "Close"]] for v, g in d.groupby("valeur", sort=True)}


def _ecrire_atomique(chemin, donnees):
    """Un fichier temporaire au nom unique dans le même dossier, puis os.replace : jamais un fichier à moitié écrit."""
    dossier = os.path.dirname(chemin)
    os.makedirs(dossier, exist_ok=True)
    fd, temporaire = tempfile.mkstemp(dir=dossier, prefix=".seance-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(donnees)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporaire, chemin)
    except BaseException:
        if os.path.exists(temporaire):
            os.unlink(temporaire)
        raise


def provenance():
    """Le commit, seance.py modifié ou non, et la version de yfinance (pour retrouver d'où vient une mesure)."""
    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return ""
    try:
        import yfinance
        version = str(yfinance.__version__)
    except Exception:
        version = "inconnue"
    return {"commit": git("rev-parse", "--short", "HEAD") or "inconnu",
            "modifie": bool(git("status", "--porcelain", "--", "seance.py")), "yfinance": version}


def ecrire(mesure, barres, chemin_mesure=MESURE, chemin_barres=BARRES):
    """
    Écrit les barres des valeurs mesurées (gzip sans date d'écriture : les mêmes
    barres donnent les mêmes octets), puis la mesure avec le md5 du CSV
    décompressé. Renvoie la mesure écrite.
    """
    donnees = csv_des_barres({v: barres[v] for v in mesure["valeurs"]}).encode("utf-8")
    mesure = dict(mesure, md5_barres=hashlib.md5(donnees).hexdigest())
    valider(mesure)
    _ecrire_atomique(chemin_barres, gzip.compress(donnees, mtime=0))
    _ecrire_atomique(chemin_mesure, (json.dumps(mesure, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    return mesure


def faire_la_mesure(tickers, maintenant=None, chemin_mesure=MESURE, chemin_barres=BARRES,
                    dossier_journalier=CACHE_JOURNALIER, dormir=time.sleep):
    """Yahoo, puis la mesure, puis l'écriture. ValueError (et rien d'écrit) s'il n'y a pas assez de barres."""
    maintenant = maintenant or datetime.now(timezone.utc)
    barres, refusees = lire_barres(tickers, dormir=dormir)
    mesure = mesurer(barres, maintenant, refusees, lire_journalier(sorted(barres), dossier_journalier))
    mesure.update(provenance())
    return ecrire(mesure, barres, chemin_mesure, chemin_barres)


# ══════════════════════════════════════════════════════════════════
#  LES VÉRIFICATIONS (des barres inventées, calculées à la main)
# ══════════════════════════════════════════════════════════════════

def verifier():
    import types
    from unittest import mock
    import banc

    cas = []

    def ok(nom, condition):
        cas.append(bool(condition))
        print(("✓ " if condition else "✗ ") + nom)

    n_ = NBSP
    S200, S50 = banc.rendement_necessaire(200.0), banc.rendement_necessaire(50.0)
    GRILLE = list(range(OUVERTURE_NY, FIN_NY, 5))        # les 78 barres d'une séance

    def faire(jours, tz=NY):
        """{date: {minute: (open, close)}} → un DataFrame à la Yahoo, index à New York (ou converti en `tz`)."""
        heures, ouv, clo = [], [], []
        for j in sorted(jours):
            for minute in sorted(jours[j]):
                heures.append(pd.Timestamp(datetime(j.year, j.month, j.day, minute // 60, minute % 60), tz=NY))
                ouv.append(jours[j][minute][0])
                clo.append(jours[j][minute][1])
        index = pd.DatetimeIndex(heures).tz_convert(tz)
        return pd.DataFrame({"Open": ouv, "Close": clo}, index=index)

    def plate(prix=100.0, sauf=None, manque=()):
        """Une séance entière à prix constant, avec quelques barres changées ou absentes."""
        b = {m: (prix, prix) for m in GRILLE if m not in manque}
        b.update(sauf or {})
        return b

    D = date.fromisoformat
    J1, J2 = D("2026-10-30"), D("2026-11-02")      # un vendredi à l'heure d'été (UTC−4), un lundi d'hiver (UTC−5)

    # 1. Le prix d'une fenêtre : l'Open de la barre du début, le Close de celle de la fin.
    b = preparer(faire({J1: plate(sauf={570: (100.0, 101.0), 580: (102.0, 103.0)})}))
    r = mouvements(b, [J1])
    x = dict(r["mouvements"]["ouverture"])[J1]
    ok("fenêtre : de l'Open de 9 h 30 (100) au Close de 9 h 40 (103) : +3 % (pas 103/101, pas 102/100)",
       x == 103.0 / 100.0 - 1)
    ok("fenêtre : les 45 minutes suivantes partent de l'Open de 9 h 45 (100), pas du Close de 9 h 40 (103)",
       dict(r["mouvements"]["suivantes"])[J1] == 0.0)
    ok("fenêtre : la séance entière va de l'Open de 9 h 30 au Close de 15 h 55 (100 → 100)",
       dict(r["mouvements"]["seance"])[J1] == 0.0)
    ok("une séance entière : 26 quarts d'heure et 24 tranches de 45 minutes, aucune retirée",
       len(r["mouvements"]["quart"]) == 26 and len(r["mouvements"]["tranche"]) == 24
       and not any(r["retirees"][f] for f in FENETRES))
    debuts_q = [OUVERTURE_NY + 15 * i for i in range(26)]
    ok("les quarts d'heure partent de 9 h 30, 9 h 45… 15 h 45 ; les tranches de 9 h 30 à 15 h 15",
       [d for d, _ in FENETRES["quart"]] == debuts_q and FENETRES["tranche"][-1] == (15 * 60 + 15, 45)
       and all(d == 15 for _, d in FENETRES["quart"]) and all(d == 45 for _, d in FENETRES["tranche"]))
    b = preparer(faire({J1: plate(sauf={570: (100.0, 100.0), 600: (100.0, 100.0), 610: (100.0, 104.0)})}))
    r = mouvements(b, [J1])
    ok("le premier quart d'heure ne voit pas 10 h 10 ; la première tranche (9 h 30 → 10 h 15) le voit : +4 %",
       dict(r["mouvements"]["ouverture"])[J1] == 0.0 and r["mouvements"]["tranche"][0][1] == 104.0 / 100.0 - 1)

    # 2. Le seuil : une HAUSSE strictement plus grande.
    ok("seuil : les seuils sont ceux du banc (200 € et 50 €)",
       abs(S200 - (201 / (199 * 0.9995 / 1.0005) - 1)) < 1e-15 and abs(S50 - (51 / (49 * 0.9995 / 1.0005) - 1)) < 1e-15)
    ok("seuil : une hausse juste au-dessus paie", paie(math.nextafter(S200, 1), S200))
    ok("seuil : une hausse égale au seuil ne paie pas", not paie(S200, S200))
    ok("seuil : une baisse plus grande que le seuil ne paie jamais", not paie(-0.05, S200) and not paie(-0.05, S50))
    ok("seuil : 3 % paie avec 200 € mais pas avec 50 €", paie(0.03, S200) and not paie(0.03, S50))

    # 3. Les médianes, exactes, en valeur absolue.
    ok("médiane : impaire, celle du milieu", mediane([3, 1, 2]) == 2)
    ok("médiane : paire, la moyenne des deux du milieu (exacte)", mediane([4, 1, 3, 2]) == Fraction(5, 2))
    ok("médiane : aucune valeur, None", mediane([]) is None)
    ok("médiane de mouvements en valeur absolue : −3 %, +1 %, +2 % → 2 %",
       mediane(abs(x) for x in (-0.03, 0.01, 0.02)) == Fraction(0.02))

    # 4. Les barres absentes : début ou fin, la fenêtre est retirée ; au milieu, elle reste.
    b = preparer(faire({J1: plate(manque=(570,))}))
    r = mouvements(b, [J1])
    ok("sans la barre de 9 h 30 : l'ouverture, le premier quart d'heure, la première tranche et la séance sont retirés",
       r["retirees"]["ouverture"] == {J1: 1} and r["retirees"]["quart"] == {J1: 1} and r["retirees"]["tranche"] == {J1: 1}
       and r["retirees"]["seance"] == {J1: 1} and not r["retirees"]["suivantes"])
    b = preparer(faire({J1: plate(manque=(580,))}))
    r = mouvements(b, [J1])
    ok("sans la barre de 9 h 40 (la fin du premier quart d'heure) : seuls l'ouverture et ce quart d'heure sont retirés",
       r["retirees"]["ouverture"] == {J1: 1} and r["retirees"]["quart"] == {J1: 1} and not r["retirees"]["tranche"]
       and not r["retirees"]["seance"])
    b = preparer(faire({J1: plate(sauf={570: (100.0, 100.0), 955: (110.0, 110.0)}, manque=range(600, 900, 5))}))
    r = mouvements(b, [J1])
    ok("un trou de 10 h à 14 h 55 au milieu : la séance entière compte encore (+10 %)",
       dict(r["mouvements"]["seance"])[J1] == 110.0 / 100.0 - 1 and not r["retirees"]["seance"])
    # Le trou du 05/11/2026 : de 12 h 00 à 13 h 30 (barres 720 à 810) à la Bourse de New York,
    # de 12 h 15 à 13 h 45 (735 à 825) au Nasdaq. Quarts retirés : début ou fin (début + 10) dans le trou.
    for nom, trou, attendus in (("de 12 h 00 à 13 h 30", range(720, 815, 5), 7), ("de 12 h 15 à 13 h 45", range(735, 830, 5), 7)):
        r = mouvements(preparer(faire({J1: plate(manque=trou)})), [J1])
        q = r["retirees"]["quart"].get(J1, 0)
        t = r["retirees"]["tranche"].get(J1, 0)
        # Tranches : début dans le trou, ou fin (début + 40) dans le trou.
        t_attendu = sum(1 for d, _ in FENETRES["tranche"] if d in trou or d + 40 in trou)
        ok(f"un trou {nom} : {attendus} quarts d'heure et {t_attendu} tranches retirés, l'ouverture et la séance gardées",
           q == attendus and t == t_attendu and not r["retirees"]["ouverture"] and not r["retirees"]["seance"])
    ok("le trou du 05/11 à la Bourse de New York retire 9 tranches (comme la relecture l'a compté)",
       sum(1 for d, _ in FENETRES["tranche"] if d in range(720, 815, 5) or d + 40 in range(720, 815, 5)) == 9)

    # 5. Un prix nul, négatif ou non fini : la fenêtre est retirée.
    for nom, prix in (("nul", 0.0), ("négatif", -1.0), ("infini", float("inf")), ("absent (NaN)", float("nan"))):
        r = mouvements(preparer(faire({J1: plate(sauf={570: (prix, 100.0)})})), [J1])
        ok(f"un prix d'ouverture {nom} à 9 h 30 : l'ouverture est retirée", r["retirees"]["ouverture"] == {J1: 1})
    r = mouvements(preparer(faire({J1: plate(sauf={955: (100.0, float("nan"))})})), [J1])
    ok("une clôture absente à 15 h 55 : la séance entière est retirée", r["retirees"]["seance"] == {J1: 1})

    # 6. Le changement d'heure : 13 h 30 UTC le 30/10 et 14 h 30 UTC le 02/11 sont toutes deux 9 h 30 à New York.
    en_utc = faire({J1: plate(sauf={570: (100.0, 100.0), 580: (100.0, 102.0)}),
                    J2: plate(sauf={570: (100.0, 100.0), 580: (100.0, 101.0)})}, tz=timezone.utc)
    premieres = {d: min(t for t in en_utc.index if t.date() == d).strftime("%H:%M") for d in (J1, J2)}
    b = preparer(en_utc)
    r = mouvements(b, [J1, J2])
    ok("changement d'heure : les barres en UTC (13 h 30 puis 14 h 30) sont ramenées à 9 h 30 à New York",
       premieres == {J1: "13:30", J2: "14:30"})
    ok("changement d'heure : l'ouverture du 30/10 (+2 %) et du 02/11 (+1 %), aucune fenêtre retirée",
       dict(r["mouvements"]["ouverture"]) == {J1: 102.0 / 100.0 - 1, J2: 101.0 / 100.0 - 1}
       and not any(r["retirees"][f] for f in FENETRES))

    # 7. Deux barres à la même heure, des heures sans fuseau : la valeur est refusée.
    double = pd.concat([en_utc.iloc[:3], en_utc.iloc[:1]])
    for nom, df, raison in (("deux barres à la même heure", double, "deux barres à la même heure"),
                            ("des heures sans fuseau", en_utc.tz_localize(None), "des heures sans fuseau")):
        try:
            preparer(df)
            ok(f"{nom} : refusé", False)
        except ValueError as e:
            ok(f"{nom} : refusé (« {e} »)", str(e) == raison)

    # 8. Une mesure entière, sur des barres inventées calculables à la main.
    #    45 valeurs, 22 séances (du 01/09 au 01/10/2026, jours ouvrés sauf le 07/09, Labor Day).
    #    Chaque séance : prix plats, sauf le premier quart d'heure : 100 → 102 les jours pairs (+2 %),
    #    100 → 99 les jours impairs (−1 %), puis plat à ce niveau jusqu'au soir.
    jours = [d.date() for d in pd.bdate_range("2026-09-01", "2026-10-01") if d.date() != D("2026-09-07")][:22]
    valeurs = [f"V{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(45)]

    def seance_type(k):
        x = 102.0 if k % 2 == 0 else 99.0
        b = {m: (x, x) for m in GRILLE}
        b.update({570: (100.0, 100.0), 575: (100.0, 100.0), 580: (100.0, x)})
        return b

    aujourd_hui = D("2026-10-02")
    synth = {v: faire({**{j: seance_type(k) for k, j in enumerate(jours)},
                       aujourd_hui: plate(sauf={570: (100.0, 100.0), 580: (100.0, 150.0)})}) for v in valeurs}
    maintenant = datetime(2026, 10, 2, 11, 0, tzinfo=NY)       # pendant la séance du 02/10, ignorée
    m = mesurer(synth, maintenant)
    o = m["fenetres"]["ouverture"]
    ok("mesure : la séance du jour (+50 % à l'ouverture) est ignorée ; 22 séances, du 01/09 au 01/10",
       m["seances"] == 22 and m["debut"] == "2026-09-01" and m["fin"] == "2026-10-01" and o["hausses"][1] == 0)
    ok("mesure : l'ouverture, 990 comptées, 495 hausses de +2 % au-dessus de 1,106 %, aucune au-dessus de 4,186 %",
       o["comptes"] == 990 and o["hausses"] == [495, 0] and o["retirees"] == 0)
    ok("mesure : sa médiane, (1 % + 2 %) / 2 = 1,5 % (495 mouvements de chaque taille)",
       o["mediane"] == float((abs(Fraction(99.0 / 100.0 - 1)) + Fraction(102.0 / 100.0 - 1)) / 2))
    q, t_, e = m["fenetres"]["quart"], m["fenetres"]["tranche"], m["fenetres"]["seance"]
    ok("mesure : 45 × 22 × 26 = 25 740 quarts d'heure, 495 hausses (le premier quart des jours pairs), médiane 0",
       q["comptes"] == 25740 and q["hausses"] == [495, 0] and q["mediane"] == 0.0)
    ok("mesure : 45 × 22 × 24 = 23 760 tranches, 495 hausses ; la séance entière, 990 et 495",
       t_["comptes"] == 23760 and t_["hausses"] == [495, 0] and e["comptes"] == 990 and e["hausses"] == [495, 0])
    ok("mesure : les 45 minutes suivantes ne bougent pas (médiane 0, aucune hausse)",
       m["fenetres"]["suivantes"]["mediane"] == 0.0 and m["fenetres"]["suivantes"]["hausses"] == [0, 0])
    ok("mesure : valeur par valeur, la même médiane qu'en tout (les 45 valeurs sont pareilles)",
       set(o["par_valeur"].values()) == {o["mediane"]} and sorted(o["par_valeur"]) == valeurs)
    ok("mesure : 22 jours d'ouverture, dont 11 avec au moins une hausse au-dessus de 1,106 %",
       m["ouverture_par_jour"] == {"jours": 22, "avec_une": 11})
    m_nuit = mesurer(synth, datetime(2026, 10, 3, 0, 30, tzinfo=PARIS))     # 18 h 30 le 02/10 à New York
    ok("minuit passé à Paris (00 h 30 le 03/10), pas à New York : la séance du 02/10 reste « aujourd'hui », ignorée",
       m_nuit["seances"] == 22 and m_nuit["fenetres"]["ouverture"]["hausses"] == [495, 0] and m_nuit["aujourd_hui"] == "2026-10-02")
    diff = dict(synth)
    diff["VAA"] = faire({j: {**seance_type(k), 580: (100.0, 105.0)} for k, j in enumerate(jours)})
    md = mesurer(diff, maintenant)["fenetres"]["ouverture"]["par_valeur"]
    ok("valeur par valeur : VAA (+5 % chaque jour) a sa propre médiane, VAB garde 1,5 %",
       md["VAA"] == 105.0 / 100.0 - 1 and md["VAB"] == o["mediane"])
    sans = {v: faire({j: ({mm: x for mm, x in seance_type(k).items() if mm != 570} if k == 5 else seance_type(k))
                      for k, j in enumerate(jours)}) for v in valeurs}
    ms = mesurer(sans, maintenant)
    ok("sans barre de 9 h 30 pour aucune valeur un jour : 21 jours d'ouverture sur 22 séances, 945 comptées",
       ms["ouverture_par_jour"] == {"jours": 21, "avec_une": 11} and ms["fenetres"]["ouverture"]["comptes"] == 945)
    ok("mesure : les seuils et les coûts sont ceux du banc, l'écart aussi",
       m["seuils"] == [S200, S50] and m["couts"] == [banc.cout_aller_retour(200.0), banc.cout_aller_retour(50.0)]
       and m["ecart"] == banc.ECART)

    # Une valeur à qui il manque une séance : ses fenêtres de ce jour-là sont retirées.
    manque = dict(synth)
    manque["VZZ"] = faire({j: seance_type(k) for k, j in enumerate(jours) if j != jours[3]})
    m2 = mesurer(manque, maintenant)
    ok("une valeur sans la séance du 04/09 : 1 + 1 + 26 + 24 + 1 fenêtres retirées ce jour-là, le total se tient",
       m2["retirees_par_jour"] == {"2026-09-04": {"ouverture": 1, "suivantes": 1, "quart": 26, "tranche": 24, "seance": 1}}
       and m2["fenetres"]["quart"]["comptes"] + m2["fenetres"]["quart"]["retirees"] == 22 * 46 * 26)
    # Une demi-séance (fermée à 13 h) : pas de séance entière, mais l'ouverture et les quarts jusqu'à 13 h.
    demi = {k: v for k, v in seance_type(0).items() if k < 13 * 60}
    rd = mouvements(preparer(faire({J1: demi})), [J1])
    ok("une demi-séance : la séance entière retirée, l'ouverture gardée, 14 quarts et 12 tranches comptés (12 retirés chacun)",
       rd["retirees"]["seance"] == {J1: 1} and len(rd["mouvements"]["ouverture"]) == 1
       and len(rd["mouvements"]["quart"]) == 14 and rd["retirees"]["quart"] == {J1: 12}
       and len(rd["mouvements"]["tranche"]) == 12 and rd["retirees"]["tranche"] == {J1: 12})
    # Pas assez de valeurs ou de séances : rien n'est mesuré.
    for nom, barres_ in (("44 valeurs", {v: synth[v] for v in valeurs[:44]}),
                         ("19 séances", {v: faire({j: seance_type(k) for k, j in enumerate(jours[:19])}) for v in valeurs})):
        try:
            mesurer(barres_, maintenant)
            ok(f"{nom} : refusé", False)
        except ValueError as ex:
            ok(f"{nom} : refusé (« {str(ex)[:60]}… »)", "pas donné assez de barres" in str(ex))
    seul_aujourdhui = dict(synth)
    seul_aujourdhui["VZZ"] = faire({aujourd_hui: plate()})
    ok("une valeur qui n'a que des barres d'aujourd'hui est refusée, et dite",
       mesurer(seul_aujourdhui, maintenant)["refusees"] == {"VZZ": "aucune barre avant aujourd'hui"})

    # 9. La variation dans le cache journalier (période de 3 séances, seuil 1,106 %).
    j5 = [D("2026-01-05"), D("2026-01-06"), D("2026-01-07"), D("2026-01-08"), D("2026-01-09")]
    jour_ = lambda ouv, clo: pd.DataFrame({"Open": ouv, "Close": clo}, index=pd.Index(j5))   # noqa: E731
    cache = {"A": jour_([100, 100, 100, 100, 100], [102, 100, 102, 102, 100]),     # hausses : j1, j3, j4
             "B": jour_([100, 100, 100, 100, 100], [100, 100, 101, 102, float("nan")])}  # hausse : j4 ; j5 absent
    v = variation(cache, S200, periode=3)
    # Périodes : (j1-j3) : 2 hausses sur 6 ; (j2-j4) : 3 sur 6 ; (j3-j5) : 3 sur 5.
    ok("variation : la plus basse 2 sur 6 (finie le 07/01), la plus haute 3 sur 5 (finie le 09/01), une valeur absente un jour",
       v["min"] == [2, 6, "2026-01-07"] and v["max"] == [3, 5, "2026-01-09"] and v["periode"] == 3 and v["valeurs"] == 2)
    ok("variation : la médiane des trois parts (1/3, 1/2, 3/5) vaut 1/2", v["mediane"] == 0.5)
    ok("variation : moins de séances que la période, None", variation(cache, S200, periode=6) is None)
    ok("variation : une seule période de 5 séances, 4 hausses sur 9 (B n'a pas le 09/01)",
       variation(cache, S200, periode=5)["min"] == [4, 9, "2026-01-09"] == variation(cache, S200, periode=5)["max"])
    nul = {"A": jour_([0, 100, 100, 100, 100], [102, 102, 100, 100, 100])}
    ok("variation : une ouverture nulle est écartée (1 hausse sur 4, pas 2 sur 5)",
       variation(nul, S200, periode=5)["min"] == [1, 4, "2026-01-09"])
    with tempfile.TemporaryDirectory(prefix="seance-cache-") as dc:
        with open(os.path.join(dc, "AAA_10y.csv"), "w", encoding="utf-8") as f:
            f.write("Date,Close,High,Low,Open,Volume\n2026-01-05,102.0,103,99,100.0,10\n"
                    "2026-01-06,100.0,101,99,100.0,10\n2026-01-06,101.5,101,99,100.0,10\n")
        with open(os.path.join(dc, "BBB_10y.csv"), "w", encoding="utf-8") as f:
            f.write("Date,Close\npas,une date\n")
        j = lire_journalier(["AAA", "BBB", "CCC"], dc)
        ok("cache journalier : Open et Close par date (une date en double : la dernière), un fichier illisible et un absent ignorés",
           list(j) == ["AAA"] and list(j["AAA"].columns) == ["Open", "Close"]
           and list(j["AAA"].index) == [D("2026-01-05"), D("2026-01-06")] and j["AAA"]["Close"].iloc[1] == 101.5)

    # 10. Les textes, sur des valeurs écrites à la main (jamais avec les fonctions d'affichage elles-mêmes).
    ok("texte : 0,0052 → « 0,52 % »", texte_pct(0.0052, 2) == f"0,52{n_}%")
    ok("texte : une moitié pile (0,125 %) s'arrondit vers le haut : « 0,13 % » (pas au pair)",
       texte_pct(Fraction(1, 800), 2) == f"0,13{n_}%")
    ok("texte : 197 sur 2 000 = 9,85 % pile → « 9,9 % » (Python, au pair, écrirait 9,8)", texte_part(197, 2000) == f"9,9{n_}%")
    ok("texte : 0,00535 vaut un peu moins en binaire (0,0053499…) : « 0,53 % » (en flottants, 53,5 + 0,5 donnerait 0,54)",
       texte_pct(0.00535, 2) == f"0,53{n_}%" and Fraction(0.00535) < Fraction(535, 100000) and 0.00535 * 10000 == 53.5)
    ok("texte : 288 sur 2 940 → « 9,8 % » ; 576 sur 2 940 → « 19,6 % »",
       texte_part(288, 2940) == f"9,8{n_}%" and texte_part(576, 2940) == f"19,6{n_}%")
    ok("texte : 0 sur 10 → « 0 % » ; 10 sur 10 → « 100 % »", texte_part(0, 10) == f"0{n_}%" and texte_part(10, 10) == f"100{n_}%")
    ok("texte : 1 sur 3 000 → « moins de 0,1 % » ; 1 sur 2 000 (0,05 % pile) → « 0,1 % »",
       texte_part(1, 3000) == f"moins de 0,1{n_}%" and texte_part(1, 2000) == f"0,1{n_}%")
    ok("texte : 9 999 sur 10 000 → « plus de 99,9 % »", texte_part(9999, 10000) == f"plus de 99,9{n_}%")
    ok("texte : les seuils « 1,106 % » et « 4,186 % », le coût « 2,20 € », le capital « 200 € »",
       texte_seuil(S200) == f"1,106{n_}%" and texte_seuil(S50) == f"4,186{n_}%"
       and texte_euros(banc.cout_aller_retour(200.0)) == f"2,20{n_}€" and texte_capital(200.0) == f"200{n_}€")
    ok("texte : la moyenne 288 / 60 → « 4,8 » ; 1 / 4 (0,25 pile) → « 0,3 » ; 45 / 1 → « 45,0 »",
       texte_moyenne(288, 60) == "4,8" and texte_moyenne(1, 4) == "0,3" and texte_moyenne(45, 1) == "45,0")
    ok("texte : les milliers avec l'espace fine (« 1 234,5 »)", _ecrire(12345, 1) == f"1{MILLIERS}234,5")
    ok("texte : « 1ᵉʳ septembre 2026 », « 28 août 2026 », « 05/11/2026 »",
       texte_date(D("2026-09-01")) == "1ᵉʳ septembre 2026" and texte_date(D("2026-08-28")) == "28 août 2026"
       and texte_date_courte(D("2026-11-05")) == "05/11/2026")
    ok("texte : 9 h 30 à New York s'écrit « 15 h 30 » (Paris), 16 h « 22 h »",
       texte_heure(570) == f"15{n_}h{n_}30" and texte_heure(960) == f"22{n_}h")
    ok("texte : les heures des fenêtres, à Paris",
       precision("ouverture") == f"15{n_}h{n_}30{n_}– 15{n_}h{n_}45" and precision("suivantes") == f"15{n_}h{n_}45{n_}– 16{n_}h{n_}30"
       and precision("seance") == f"15{n_}h{n_}30{n_}– 22{n_}h" and precision("quart") == precision("tranche") == "à tout moment")
    ok("texte : l'heure de la mesure, à Paris : « 2 octobre 2026 à 17 h 00 »",
       texte_moment("2026-10-02T11:00:00-04:00") == f"2 octobre 2026 à 17{n_}h{n_}00")

    # 11. La chaîne entière : un faux Yahoo, l'écriture, la relecture par le serveur.
    class FauxTicker:
        appels = {}
        refus = set()

        def __init__(self, t):
            self.t = t

        def history(self, period=None, interval=None, prepost=None, auto_adjust=None):
            FauxTicker.appels[self.t] = FauxTicker.appels.get(self.t, 0) + 1
            if (period, interval, prepost, auto_adjust) != ("60d", "5m", False, False):
                raise ValueError("mauvaise demande")
            if self.t in FauxTicker.refus:
                raise TypeError("'NoneType' object is not subscriptable")
            return synth[self.t].assign(High=1.0, Low=1.0, Volume=0)

    ancien_yf = sys.modules.get("yfinance")
    sys.modules["yfinance"] = types.SimpleNamespace(Ticker=FauxTicker, __version__="0.0-faux")
    dormi = []
    try:
        with tempfile.TemporaryDirectory(prefix="seance-verif-") as dossier:
            cm, cb = os.path.join(dossier, "seance.json"), os.path.join(dossier, "seance-barres.csv.gz")
            FauxTicker.refus = {"VZZ"}
            mesure = faire_la_mesure(valeurs + ["VZZ"], maintenant, cm, cb, dossier_journalier=os.path.join(dossier, "rien"),
                                     dormir=dormi.append)
            ok("Yahoo : une valeur refusée trois fois est dite, les 45 autres mesurées",
               FauxTicker.appels["VZZ"] == 3 and mesure["refusees"] == {"VZZ": "3 essais : 'NoneType' object is not subscriptable"}
               and mesure["valeurs"] == valeurs)
            ok("Yahoo : 2 s entre deux valeurs, 10 s entre deux essais (45 pauses et 2 attentes)",
               dormi.count(PAUSE) == 45 and dormi.count(ATTENTE) == 2 and len(dormi) == 47)
            s = section(cm)
            t = s.get("textes", {})
            ok("relecture : la mesure écrite est relue par le serveur (« mesure »)", s["etat"] == "mesure")
            ok("relecture : le recalcul depuis le fichier des barres donne la même mesure",
               mesurer(relire_barres(cb), maintenant)["fenetres"] == mesure["fenetres"])
            ok("relecture : le md5 est celui du CSV décompressé",
               hashlib.md5(gzip.decompress(open(cb, "rb").read())).hexdigest() == mesure["md5_barres"])
            octets = open(cb, "rb").read()
            with mock.patch("time.time", return_value=12345.0):     # une autre heure : gzip l'écrirait sans mtime=0
                ecrire(mesure, synth, os.path.join(dossier, "b.json"), os.path.join(dossier, "b.csv.gz"))
            ok("les mêmes barres écrites deux fois donnent les mêmes octets (gzip sans date)",
               open(os.path.join(dossier, "b.csv.gz"), "rb").read() == octets)
            avec_jour = pd.read_csv(cb, dtype=str)
            ok("le fichier des barres garde l'heure en UTC avec son fuseau, et les prix sans perte",
               avec_jour["heure"].iloc[0].endswith("+00:00") and avec_jour["open"].iloc[0] == "100.0")

            # Les textes de cette mesure, écrits à la main.
            ok("texte : la pastille", t.get("pastille") == f"22 séances")
            ok("texte : l'introduction",
               t.get("intro") == (f"Le banc a mesuré le soir pour le lendemain{n_}; voici l'intérieur de la séance. Avec "
                                  f"200{n_}€, un achat puis une vente coûtent 2,20{n_}€ quand le cours ne bouge pas{n_}: il "
                                  f"faut une hausse de plus de 1,106{n_}% pour gagner quelque chose (plus de 4,186{n_}% avec 50{n_}€)."))
            ok("texte : la légende du tableau",
               t.get("legende") == "Une des 45 valeurs, prise au hasard, sur les 22 dernières séances (du 1ᵉʳ septembre 2026 au 1ᵉʳ octobre 2026).")
            ok("texte : les colonnes",
               t.get("colonnes") == ["Fenêtre", "Mouvement médian", f"Une hausse de plus de 1,106{n_}% (200{n_}€)",
                                     f"De plus de 4,186{n_}% (50{n_}€)"])
            lignes_ = t.get("lignes", [])
            ok("texte : les cinq lignes, dans l'ordre, avec leur médiane et leurs parts",
               [(x["cle"], x["mediane"], x["parts"]) for x in lignes_] == [
                   ("ouverture", f"1,50{n_}%", [f"50,0{n_}%", f"0{n_}%"]),
                   ("suivantes", f"0,00{n_}%", [f"0{n_}%", f"0{n_}%"]),
                   ("quart", f"0,00{n_}%", [f"1,9{n_}%", f"0{n_}%"]),
                   ("tranche", f"0,00{n_}%", [f"2,1{n_}%", f"0{n_}%"]),
                   ("seance", f"1,50{n_}%", [f"50,0{n_}%", f"0{n_}%"])])
            ok("texte : la conclusion",
               t.get("conclusion") == (f"Achetée au hasard avec 200{n_}€, une des 45 n'a donc rien rapporté 50,0{n_}% des fois "
                                       f"dans le premier quart d'heure, et 50,0{n_}% des fois sur la séance entière. Au moins "
                                       f"une a monté assez dès le premier quart d'heure 11 jours sur 22, mais 22,5 sur 45 en "
                                       f"moyenne{n_}: il fallait savoir d'avance lesquelles."))
            ok("texte : 45 valeurs dans l'ordre alphabétique, cinq médianes chacune",
               [x["valeur"] for x in t.get("valeurs", [])] == valeurs
               and t["valeurs"][0]["medianes"] == [f"1,50{n_}%", f"0,00{n_}%", f"0,00{n_}%", f"0,00{n_}%", f"1,50{n_}%"])
            notes_ = t.get("notes", [])
            ok("texte : les notes (sans cache journalier : pas de variation ; la valeur refusée ; la date)",
               len(notes_) == 6 and notes_[1] == (f"22 séances seulement (Yahoo ne garde pas les barres de 5 minutes plus "
                                                  f"longtemps), et les 45 valeurs bougent ensemble{n_}: un sondage, pas une loi.")
               and notes_[4] == f"Yahoo n'a pas donné les barres de VZZ{n_}: la mesure porte sur 45 valeurs."
               and notes_[5].startswith(f"Mesuré le 2 octobre 2026 à 17{n_}h{n_}00, sur les barres"))

            # La variation, dite dans la note 2.
            m3 = dict(mesure, variation={"debut": "2016-09-06", "fin": "2026-09-03", "periode": 60, "valeurs": 49,
                                         "min": [212, 2940, "2017-05-16"], "max": [1032, 2940, "2020-05-21"], "mediane": 0.179})
            ok("texte : la variation, quand le cache journalier est là",
               textes(m3)["notes"][1].endswith(
                   f"un sondage, pas une loi. La part des hausses de plus de 1,106{n_}% change beaucoup d'une période à "
                   f"l'autre{n_}: sur la séance entière, les cours journaliers donnent de 7,2{n_}% à 35,1{n_}% selon la "
                   f"période de 60 séances, entre septembre 2016 et septembre 2026."))
            m5 = dict(mesure, ouverture_par_jour={"jours": 21, "avec_une": 11})
            ok("texte : 11 jours sur 21, et la moyenne par jour d'ouverture (495 / 21 = 23,57 → « 23,6 »)",
               textes(m5)["conclusion"].endswith(f"11 jours sur 21, mais 23,6 sur 45 en moyenne{n_}: il fallait savoir d'avance lesquelles."))
            m4 = dict(mesure, ouverture_par_jour={"jours": 22, "avec_une": 0},
                      fenetres=dict(mesure["fenetres"], ouverture=dict(mesure["fenetres"]["ouverture"], hausses=[0, 0])))
            ok("texte : aucune hausse assez grande à l'ouverture",
               textes(m4)["conclusion"].endswith("Aucune n'a monté assez dès le premier quart d'heure, sur 22 séances."))

            # Les mots : aucun conseil, et ceux du plan.
            tous = [t["pastille"], t["intro"], t["legende"], t["conclusion"], t["legende_valeurs"], t["detail"],
                    *t["colonnes"], *notes_, *textes(m3)["notes"], *[x["nom"] + x["precision"] for x in lignes_]]
            interdits = re.compile(r"\b(achète|achetez|acheter|vends|vendez|vendre|prévoi\w*|prédi\w*|conseill\w*|"
                                   r"recommand\w*|devrais|devriez|opportunit\w*)\b", re.IGNORECASE)
            mal = [x for x in tous if interdits.search(x)]
            ok("aucun conseil dans les textes" + (f" — {mal[0]!r}" if mal else ""), not mal)
            typo = [x for x in tous if any(c in x for c in (" :", " ;", " %", " €", "« ", " »"))]
            ok("typographie : aucune espace ordinaire avant « : », « ; », « % », « € »" + (f" — {typo[0]!r}" if typo else ""),
               not typo)
            visibles = " ".join(tous)
            ok("les mots du plan : « prise au hasard », « hausses et baisses mêlées », « savoir d'avance », « un sondage »",
               all(x in visibles for x in ("prise au hasard", "hausses et baisses mêlées", "savoir d'avance", "un sondage")))

            # 12. La relecture refuse ce qui n'a pas la forme d'une mesure.
            with open(cm, encoding="utf-8") as f:
                base = json.load(f)

            def abime(nom, changer, attendu):
                d = json.loads(json.dumps(base))
                changer(d)
                chemin = os.path.join(dossier, "abime.json")
                with open(chemin, "w", encoding="utf-8") as f:
                    f.write(d if isinstance(d, str) else json.dumps(d))
                s = section(chemin)
                ok(f"refusé, {nom} (« {s.get('detail', '')} »)", s["etat"] == "illisible" and attendu in s.get("detail", ""))

            def poser(chemin_cles, valeur):
                def f(d):
                    x = d
                    for k in chemin_cles[:-1]:
                        x = x[k]
                    x[chemin_cles[-1]] = valeur
                return f

            def ajouter(n):
                def f(d):
                    d["fenetres"]["ouverture"]["comptes"] += n
                return f

            with open(os.path.join(dossier, "abime.json"), "w", encoding="utf-8") as f:
                f.write("{pas du json")
            s = section(os.path.join(dossier, "abime.json"))
            ok(f"refusé, pas du JSON (« {s.get('detail')} »)", s["etat"] == "illisible" and s["detail"] == "ce n'est pas du JSON valide")
            with open(os.path.join(dossier, "abime.json"), "wb") as f:
                f.write(b"\xff\xfe\x00")
            ok("refusé, pas du texte UTF-8", section(os.path.join(dossier, "abime.json"))["detail"] == "ce n'est pas du texte UTF-8")
            with open(os.path.join(dossier, "abime.json"), "w", encoding="utf-8") as f:
                f.write("[]")
            ok("refusé, un tableau JSON", section(os.path.join(dossier, "abime.json"))["detail"] == "ce n'est pas un objet JSON")
            for nom, changer, attendu in (
                    ("un objet vide", lambda d: d.clear(), "version"),
                    ("une autre version", poser(["version"], 2), "version"),
                    ("une heure sans fuseau", poser(["date_mesure"], "2026-10-02T17:00:00"), "fuseau"),
                    ("une heure sans « T »", poser(["date_mesure"], "2026-10-02 17:00:00+02:00"), "illisible"),
                    ("une date qui ne suit pas l'heure", poser(["aujourd_hui"], "2026-10-03"), "ne suit pas"),
                    ("19 séances", poser(["seances"], 19), "séances"),
                    ("des séances à l'envers", lambda d: d.update(debut="2026-10-01", fin="2026-09-30"), "ne se suivent pas"),
                    ("une dernière séance le jour même", poser(["fin"], "2026-10-02"), "ne se suivent pas"),
                    ("une date pas écrite en entier", poser(["debut"], "2026-9-1"), "en entier"),
                    ("44 valeurs", lambda d: d["valeurs"].pop(), "au moins 45"),
                    ("une valeur en double", lambda d: d["valeurs"].__setitem__(1, d["valeurs"][0]), "au moins 45"),
                    ("un nom de valeur bizarre", lambda d: d["valeurs"].__setitem__(0, "<img>"), "bonne forme"),
                    ("une valeur refusée et mesurée", poser(["refusees", "VAA"], "boum"), "à la fois"),
                    ("d'autres capitaux", poser(["capitaux"], [200.0, 60.0]), "200"),
                    ("des seuils à l'envers", poser(["seuils"], [0.05, 0.01]), "ordre"),
                    ("un seuil infini", lambda d: d["seuils"].__setitem__(0, float("inf")), "fini"),
                    ("des comptes en texte", poser(["fenetres", "ouverture", "comptes"], "990"), "entier"),
                    ("des comptes à virgule", poser(["fenetres", "ouverture", "comptes"], 990.0), "entier"),
                    ("plus de hausses à 50 € qu'à 200 €", poser(["fenetres", "seance", "hausses"], [10, 11]), "ne suivent pas"),
                    ("plus de hausses que de comptes", poser(["fenetres", "quart", "hausses"], [30000, 0]), "ne suivent pas"),
                    ("une médiane négative", poser(["fenetres", "quart", "mediane"], -0.01), "trop petit"),
                    ("des comptes qui ne font pas le total", ajouter(1), "total"),
                    ("une valeur sans médiane", lambda d: d["fenetres"]["tranche"]["par_valeur"].pop("VAA"), "par valeur"),
                    ("des retirées jour par jour fausses", poser(["retirees_par_jour"], {"2026-09-04": {"quart": 3}}), "total"),
                    ("plus de jours avec une hausse que de jours", poser(["ouverture_par_jour", "avec_une"], 23), "ne suivent pas"),
                    ("aucun jour avec une hausse malgré 495 hausses", poser(["ouverture_par_jour", "avec_une"], 0), "ne suivent pas"),
                    ("une variation à l'envers", poser(["variation"], {"debut": "2016-09-06", "fin": "2026-09-03", "periode": 60,
                                                                       "valeurs": 49, "min": [9, 10, "2017-01-03"],
                                                                       "max": [1, 10, "2018-01-03"], "mediane": 0.2}), "envers"),
                    ("une empreinte bizarre", poser(["md5_barres"], "pas un md5"), "empreinte"),
                    ("un écart du courtier de 15 %", poser(["ecart"], 0.15), "trop grand")):
                abime(nom, changer, attendu)
            os.mkdir(os.path.join(dossier, "dossier.json"))
            s = section(os.path.join(dossier, "dossier.json"))
            ok("refusé, un dossier à la place du fichier", s["etat"] == "illisible" and s["detail"] == "ce n'est pas un fichier")
            s = section(os.path.join(dossier, "absent.json"))
            ok("absent : « pas encore mesuré », sans erreur", s["etat"] == "absent" and "pas encore mesuré" in s["message"])

            # 13. Presque toutes les valeurs refusées : rien n'est écrit.
            FauxTicker.refus = set(valeurs[:5])
            cm2, cb2 = os.path.join(dossier, "rien.json"), os.path.join(dossier, "rien.csv.gz")
            try:
                faire_la_mesure(valeurs, maintenant, cm2, cb2, dossier_journalier=os.path.join(dossier, "rien"),
                                dormir=lambda s: None)
                ok("Yahoo : 40 valeurs sur 45, refusé", False)
            except ValueError as ex:
                ok("Yahoo : 40 valeurs seulement, rien n'est écrit (« pas assez de barres »)",
                   "pas donné assez de barres" in str(ex) and not os.path.exists(cm2) and not os.path.exists(cb2))

            # 14. Une écriture qui échoue en route : l'ancien fichier reste entier, aucun fichier temporaire ne traîne.
            avant = open(cm, "rb").read()
            with mock.patch("os.replace", side_effect=OSError("disque plein")):
                try:
                    ecrire(dict(mesure, seances=mesure["seances"]), synth, cm, cb)
                    ok("une écriture qui échoue lève une erreur", False)
                except OSError:
                    ok("une écriture qui échoue lève une erreur", True)
            ok("l'ancienne mesure reste entière, et aucun fichier temporaire ne traîne",
               open(cm, "rb").read() == avant and not [f for f in os.listdir(dossier) if f.startswith(".seance-")])
    finally:
        if ancien_yf is None:
            sys.modules.pop("yfinance", None)
        else:
            sys.modules["yfinance"] = ancien_yf

    print(f"\n{sum(cas)}/{len(cas)} vérifications réussies")
    return all(cas)


# ══════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--mesurer", action="store_true", help="lire Yahoo (environ 2 min), puis écrire data/seance.json")
    p.add_argument("--afficher", action="store_true", help="le rapport de la mesure écrite (sans réseau)")
    p.add_argument("--verifier", action="store_true", help="les cas calculés à la main (sans réseau)")
    args = p.parse_args()
    if args.verifier:
        sys.exit(0 if verifier() else 1)
    if args.mesurer:
        import stock_engine
        print(f"Lecture des barres de 5 minutes chez Yahoo, une valeur à la fois "
              f"({len(stock_engine.UNIVERSE_TICKERS)} valeurs, environ 2 min)…", flush=True)
        try:
            mesure = faire_la_mesure(stock_engine.UNIVERSE_TICKERS)
        except ValueError as e:
            sys.exit(f"Rien n'est écrit : {e}.")
        print(rapport(mesure))
        print(f"\nÉcrit : {os.path.relpath(MESURE, ROOT)} et {os.path.relpath(BARRES, ROOT)}.")
        return
    if args.afficher:
        try:
            print(rapport(lire()))
        except FileNotFoundError:
            sys.exit("Pas encore de mesure : python3 seance.py --mesurer.")
        except MesureIllisible as e:
            sys.exit(f"La mesure écrite est illisible : {e}.")
        return
    p.print_help()


if __name__ == "__main__":
    main()
