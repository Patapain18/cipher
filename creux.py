"""
Le creux du marché — de combien le S&P 500 et le Nasdaq sont sous leur
record, et ce qu'ont été les baisses de 20 % ou plus du passé.

CE QUE ÇA FAIT
    Pour le S&P 500 (^GSPC) et le Nasdaq Composite (^IXIC), sur les
    clôtures journalières de Yahoo, en prix seul (comme le thermomètre) :
      - la baisse en cours depuis le dernier record ;
      - les baisses FINIES de 20 % ou plus : leur record, leur creux, leur
        profondeur, le temps de la descente, et le temps pour revenir au
        record, compté depuis le record ET depuis le creux ;
      - la médiane de ces retours, le plus court et le plus long.

LES RÈGLES (le plan du 22/11/2026, validé par Mathis le 23/11, fin de BRIEF.md)
    - Tout se décide sur les clôtures. Pendant la séance de New York, la
      ligne du jour (le cours du moment) est ignorée.
    - Le record : la plus haute clôture vue jusque-là ; à égalité, le jour
      le plus récent (la baisse commence après le dernier jour au record).
    - Une baisse commence au record et finit au premier jour où une clôture
      l'égale ou le dépasse. Elle entre dans le tableau si sa plus basse
      clôture (à égalité, la première) est à 20 % ou plus sous le record,
      −20 % exactement compris : `creux <= record * 0.8`.
    - Les pourcentages : au dixième, tronqués vers zéro, calculés exactement.
    - Les durées : mois = jours ÷ 30,44, arrondi ; sous 1 mois « moins d'un
      mois », sous 24 mois « N mois », au-delà les années (jours ÷ 365,25,
      au dixième).

CE QUE ÇA NE FAIT PAS
    Aucune date de fin, aucune prévision, aucun conseil. Le tableau ne garde
    que les baisses finies, donc revenues : celle en cours n'y est pas.

LANCER
    python3 creux.py --afficher    # les deux tableaux dans le terminal (2 lectures Yahoo)
    python3 creux.py --verifier    # les cas calculés à la main (sans réseau)
"""

import argparse
import json
import math
import os
import sys
from datetime import date, datetime, timezone
from fractions import Fraction
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import thermometre       # noqa: E402  ses cours : même nettoyage, mêmes 3 essais (thermometre.py ne change pas)

INDICES = (("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq Composite"))
DEBUT_YAHOO = "1927-01-01"   # un début demandé à Yahoo avant son premier cours (^GSPC : 30/12/1927)
# Le premier cours de chaque indice chez Yahoo (vu le 23/11/2026). Une série qui commence plus
# d'un mois après a été coupée (par Yahoo ou par yfinance) : ses baisses seraient fausses sans le
# dire, elle est donc refusée comme un refus de Yahoo.
PREMIERS_COURS = {"^GSPC": date(1927, 12, 30), "^IXIC": date(1971, 2, 5)}
MARGE_DEBUT = 31         # jours
SEUIL = 0.20             # une baisse de 20 % ou plus
SEUIL_PROCHE = 0.19      # « la barre compte » : ce qu'on aurait compté avec −19 %
JOURS_PAR_MOIS = Fraction("30.44")
JOURS_PAR_AN = Fraction("365.25")
NY = ZoneInfo("America/New_York")
PARIS = ZoneInfo("Europe/Paris")

NBSP = " "          # l'espace insécable de PP.pct (avant « % », « : », « ; »)
MILLIERS = " "      # l'espace fine des milliers, comme toLocaleString('fr-FR')
MOINS = "−"         # le vrai signe moins, comme PP.pct

MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre")
JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def _insecables(texte):
    """La typographie française : une espace insécable avant « : », « ; », « % » et « » », après « « »."""
    for avant, apres in ((" :", NBSP + ":"), (" ;", NBSP + ";"), (" %", NBSP + "%"),
                         ("« ", "«" + NBSP), (" »", NBSP + "»")):
        texte = texte.replace(avant, apres)
    return texte


# ══════════════════════════════════════════════════════════════════
#  LES BAISSES (pures : aucun réseau, vérifiées à la main par --verifier)
# ══════════════════════════════════════════════════════════════════

def sous_le_seuil(creux, record, seuil=SEUIL):
    """
    La baisse a-t-elle atteint le seuil ? UNE seule fonction, écrite par une
    multiplication : 100 → 80 est bien à −20 %. Écrite par une division
    (creux / record − 1 <= −0,20), elle refuserait ce cas : pour
    l'ordinateur, 80 / 100 − 1 vaut −0,19999999999999996.
    (1 − 0,20 vaut exactement 0,8, et 1 − 0,19 exactement 0,81 : vérifié.)
    """
    return creux <= record * (1 - seuil)


def _parcours(valeurs):
    """
    Un seul passage sur les clôtures. Renvoie les épisodes finis, chacun
    (record, creux, fin) en positions, de TOUTES les profondeurs, puis
    l'épisode en cours (record, creux ou None).
      - le record : la plus haute clôture vue jusque-là ; une clôture qui
        l'égale finit la baisse, et la date du record avance ;
      - le creux : la plus basse clôture depuis le record ; à égalité, la
        première (seule une clôture strictement plus basse le remplace).
    """
    finis = []
    rec, creux = 0, None
    for i in range(1, len(valeurs)):
        if valeurs[i] >= valeurs[rec]:
            if creux is not None:
                finis.append((rec, creux, i))
            rec, creux = i, None
        elif creux is None or valeurs[i] < valeurs[creux]:
            creux = i
    return finis, (rec, creux)


def _jour(horodatage):
    return horodatage.date() if hasattr(horodatage, "date") else horodatage


def _episode(cours, rec, creux, fin=None):
    """Les chiffres d'un épisode : dates, niveaux, et durées en jours de calendrier."""
    dates, v = cours.index, cours.to_numpy(dtype=float)
    d_rec, d_creux = _jour(dates[rec]), _jour(dates[creux])
    e = {"record_date": d_rec, "record": float(v[rec]),
         "creux_date": d_creux, "creux": float(v[creux]),
         "descente": (d_creux - d_rec).days}
    if fin is not None:
        d_fin = _jour(dates[fin])
        e.update(fin_date=d_fin, retour_record=(d_fin - d_rec).days, retour_creux=(d_fin - d_creux).days)
    return e


def baisses(cours, seuil=SEUIL):
    """
    Les baisses FINIES dont le creux est à `seuil` ou plus sous le record
    (−20 % par défaut), de la plus ancienne à la plus récente.
    `cours` : les clôtures, une série pandas indexée par date (sans fuseau,
    triée, sans doublon : le nettoyage de thermometre._cours).
    Chaque baisse : record_date, record, creux_date, creux, descente,
    fin_date, retour_record, retour_creux (durées en jours de calendrier).
    """
    v = cours.to_numpy(dtype=float)
    finis, _ = _parcours(v)
    return [_episode(cours, r, c, f) for r, c, f in finis if sous_le_seuil(v[c], v[r], seuil)]


def la_plus_proche(cours, seuil=SEUIL):
    """
    La baisse finie la plus profonde parmi celles qui n'ont PAS atteint le
    seuil (celle qui est passée le plus près de la barre), ou None.
    """
    v = cours.to_numpy(dtype=float)
    finis, _ = _parcours(v)
    restees = [(v[c] / v[r], r, c, f) for r, c, f in finis if not sous_le_seuil(v[c], v[r], seuil)]
    if not restees:
        return None
    _, r, c, f = min(restees, key=lambda x: x[0])
    return _episode(cours, r, c, f)


def en_cours(cours, seuil=SEUIL):
    """
    La baisse depuis le dernier record, sur les clôtures :
      - au_record : la dernière clôture est le record (ou l'égale) ;
      - sinon, le creux pour l'instant (la plus basse clôture depuis le
        record ; à égalité, la première), et seuil_date : la première
        clôture à `seuil` ou plus sous le record (None si aucune).
    Jamais de date de fin : elle n'est pas connue.
    """
    v = cours.to_numpy(dtype=float)
    if not len(v):
        raise ValueError("aucune clôture")
    _, (rec, creux) = _parcours(v)
    dates = cours.index
    e = {"record_date": _jour(dates[rec]), "record": float(v[rec]),
         "derniere_date": _jour(dates[-1]), "derniere": float(v[-1]),
         "au_record": creux is None, "creux_date": None, "creux": None, "seuil_date": None}
    if creux is not None:
        franchi = next((i for i in range(rec + 1, len(v)) if sous_le_seuil(v[i], v[rec], seuil)), None)
        e.update(creux_date=_jour(dates[creux]), creux=float(v[creux]),
                 seuil_date=_jour(dates[franchi]) if franchi is not None else None)
    return e


def clotures(cours, maintenant):
    """
    Les clôtures seules. Pendant la séance de New York (avant 16 h là-bas,
    22 h à Paris la plupart de l'année), la ligne du jour est le cours du
    moment, pas une clôture : elle est retirée. Un jour de séance courte
    (fermeture à 13 h), elle l'est aussi jusqu'à 16 h : un jour de retard,
    jamais un chiffre provisoire.
    """
    ny = maintenant.astimezone(NY)
    if len(cours) and _jour(cours.index[-1]) == ny.date() and (ny.hour, ny.minute) < (16, 0):
        return cours.iloc[:-1]
    return cours


def mediane(valeurs):
    """La médiane exacte : celle du milieu, ou la moyenne des deux du milieu (nombre pair)."""
    v = sorted(valeurs)
    n = len(v)
    if not n:
        return None
    return Fraction(v[n // 2]) if n % 2 else Fraction(v[n // 2 - 1] + v[n // 2], 2)


# ══════════════════════════════════════════════════════════════════
#  LES TEXTES (une seule fonction par texte affiché ; la page ne fait
#  que les insérer, et --verifier les teste tous)
# ══════════════════════════════════════════════════════════════════

def dixiemes_de_baisse(creux, record):
    """
    La baisse en dixièmes de point, TRONQUÉE vers zéro et calculée
    exactement (Fraction lit la valeur exacte de chaque nombre) :
      - 80 / 100 donne −200 (« −20,0 % »), alors que le calcul ordinaire
        donne −0,19999999999999996, qui tronqué ferait « −19,9 % » ;
      - 80,04 / 100 donne −199 (« −19,9 % ») : arrondi, −19,96 % s'écrirait
        « −20,0 % » sans avoir atteint la barre.
    """
    return math.trunc((Fraction(creux) / Fraction(record) - 1) * 1000)


def texte_pct(dixiemes):
    """−116 → « −11,6 % » (vrai signe moins, espace insécable avant « % »)."""
    a = abs(dixiemes)
    return f"{MOINS if dixiemes < 0 else ''}{a // 10},{a % 10}{NBSP}%"


def texte_baisse(creux, record):
    """La baisse d'un creux sous son record : 6910,39 sous 7818,93 → « −11,6 % »."""
    return texte_pct(dixiemes_de_baisse(creux, record))


def _arrondi(x):
    """L'arrondi au plus proche, la moitié vers le haut (x positif, exact)."""
    return math.floor(x + Fraction(1, 2))


def texte_duree(jours):
    """
    La règle des durées, sans trou : mois = jours ÷ 30,44, arrondi ; sous
    1 mois « moins d'un mois », sous 24 mois « N mois », sinon les années
    (jours ÷ 365,25, au dixième) : « 2 ans », « 2,1 ans ». Calculée
    exactement : une médiane peut tomber sur un demi-jour.
    """
    j = Fraction(jours)
    mois = _arrondi(j / JOURS_PAR_MOIS)
    if mois < 1:
        return "moins d'un mois"
    if mois < 24:
        return f"{mois}{NBSP}mois"
    d = _arrondi(j / JOURS_PAR_AN * 10)
    return f"{d // 10}{NBSP}ans" if d % 10 == 0 else f"{d // 10},{d % 10}{NBSP}ans"


def texte_date(d, jour_semaine=False):
    """date(2026, 10, 6) → « 6 octobre 2026 » ; « mardi 6 octobre 2026 » ; le 1er : « 1ᵉʳ juin 1932 »."""
    texte = f"{'1ᵉʳ' if d.day == 1 else d.day} {MOIS[d.month - 1]} {d.year}"
    return f"{JOURS[d.weekday()]} {texte}" if jour_semaine else texte


def texte_date_courte(d):
    """date(1929, 9, 16) → « 16/09/1929 »."""
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def texte_mois(d):
    """date(1990, 10, 11) → « octobre 1990 »."""
    return f"{MOIS[d.month - 1]} {d.year}"


def texte_niveau(x):
    """7818.93 → « 7 818,93 » (l'espace fine des milliers, comme la page)."""
    return f"{x:,.2f}".replace(",", MILLIERS).replace(".", ",")


def texte_heure(moment):
    """Un instant, à l'heure de Paris : « lundi 23 novembre 2026 à 10 h 05 »."""
    p = moment.astimezone(PARIS)
    return f"{texte_date(p.date(), True)} à {p.hour} h {p.minute:02d}"


def phrase_en_cours(nom, ec):
    """
    La baisse en cours, sur les clôtures, avec ses dates. Jamais de date de
    fin, jamais « il reste ».
    """
    p = f"À la clôture du {texte_date(ec['derniere_date'])}, le {nom}"
    if ec["au_record"]:
        return _insecables(f"{p} est à son record.")
    d = dixiemes_de_baisse(ec["derniere"], ec["record"])
    p += f" est à {texte_pct(d) if d else 'moins de 0,1 %'} de son record de clôture du {texte_date(ec['record_date'])}"
    if ec["creux_date"] == ec["derniere_date"]:
        p += " ; c'est aussi son creux pour l'instant."
    else:
        dc = dixiemes_de_baisse(ec["creux"], ec["record"])
        p += (f". Son creux pour l'instant : {texte_pct(dc) if dc else 'à moins de 0,1 % du record'}, "
              f"le {texte_date(ec['creux_date'])}.")
    if ec["seuil_date"] is not None:
        p += (f" La baisse commencée le {texte_date(ec['record_date'])} a atteint {MOINS}20 % "
              f"le {texte_date(ec['seuil_date'])}.")
    return _insecables(p)


def phrase_histoire(liste, debut):
    """Le nombre de baisses finies, puis leur retour au record : depuis le creux ET depuis le record."""
    depuis = f"depuis {texte_mois(debut)}"
    n = len(liste)
    if n == 0:
        return _insecables(f"Sur les clôtures, aucune baisse de 20 % ou plus n'est finie {depuis}.")
    if n == 1:
        b = liste[0]
        return _insecables(f"Sur les clôtures, une seule baisse de 20 % ou plus {depuis}, revenue à son record : "
                           f"en {texte_duree(b['retour_creux'])} depuis le creux, "
                           f"en {texte_duree(b['retour_record'])} depuis le record.")

    def retour(cle, depuis_quoi):
        jours = [b[cle] for b in liste]
        court, long_ = texte_duree(min(jours)), texte_duree(max(jours))
        return f"{texte_duree(mediane(jours))} {depuis_quoi}" + (f" (de {court} à {long_})" if court != long_ else "")

    return _insecables(f"Sur les clôtures, {n} baisses de 20 % ou plus {depuis}, toutes revenues à leur record. "
                       f"Le retour a pris en médiane {retour('retour_creux', 'depuis le creux')}, "
                       f"et {retour('retour_record', 'depuis le record')}.")


def phrase_barre(n, n_proche, proche):
    """La barre compte : avec −19 % au lieu de −20 %, combien de baisses, et la plus proche restée au-dessus."""
    if n_proche > n:
        p = f"Avec {MOINS}19 % au lieu de {MOINS}20 %, il y en aurait {n_proche}."
    elif n:
        p = f"Avec {MOINS}19 % au lieu de {MOINS}20 %, il y en aurait autant."
    else:
        p = f"Avec {MOINS}19 % au lieu de {MOINS}20 %, il n'y en aurait toujours aucune."
    if proche is not None:
        p += (f" La plus proche de la barre s'est arrêtée à {texte_baisse(proche['creux'], proche['record'])}, "
              f"en {texte_mois(proche['creux_date'])}.")
    return _insecables(p)


def titre_detail(nom, n, debut):
    """Le titre du détail à ouvrir (None sans aucune baisse : rien à montrer)."""
    depuis = f"depuis {texte_mois(debut)}, sur les clôtures"
    if n == 0:
        return None
    if n == 1:
        return _insecables(f"La seule baisse de 20 % ou plus du {nom} {depuis}")
    return _insecables(f"Les {n} baisses de 20 % ou plus du {nom} {depuis}")


def textes_ligne(b):
    """Les cellules d'une ligne du tableau."""
    return {"record": texte_date_courte(b["record_date"]), "niveau": texte_niveau(b["record"]),
            "creux": texte_date_courte(b["creux_date"]), "baisse": texte_baisse(b["creux"], b["record"]),
            "descente": texte_duree(b["descente"]), "retour_record": texte_duree(b["retour_record"]),
            "retour_creux": texte_duree(b["retour_creux"])}


LEGENDE = _insecables(
    "Record : la plus haute clôture avant la baisse, et son niveau. Creux : la plus basse clôture "
    "de la baisse. Descente : du record au creux. Retour : jusqu'à la première clôture au niveau "
    "du record ou au-dessus, compté depuis le record et depuis le creux.")

# Le Nikkei : relu chez Yahoo le 23/11/2026 (record de clôture du 29/12/1989 à 38 915,87, première
# clôture au-dessus le 22/02/2024, soit 12 473 jours ; plus bas le 10/03/2009, −81,87 %).
NIKKEI = ("Ailleurs, un retour a pris bien plus longtemps : le Nikkei japonais, 34,1 ans (record du "
          f"29/12/1989, dépassé le 22/02/2024 ; {MOINS}81,8 % au plus bas).")
NOTES_FIXES = (
    "En prix seul et en dollars courants : avec les dividendes, les retours seraient plus courts ; "
    "corrigés de l'inflation, plus longs.",
    "Un indice remplace les entreprises qui tombent : une action seule peut ne jamais revenir à son record.",
    "Une baisse dure jusqu'au record suivant : un second krach avant ce record en fait partie "
    "(pour le Nasdaq, 2007-2009 est dans 2000-2015).",
    # Confirmé le 23/11/2026 chez S&P Global : S&P 500 lancé le 4 mars 1957 ; avant, un indice
    # composite de 90 valeurs, calculé chaque jour depuis 1926.
    "Avant le 4 mars 1957, date de naissance du S&P 500, l'indice de Yahoo suit 90 valeurs "
    "(l'indice composite de Standard & Poor's, calculé chaque jour depuis 1926).",
    "Le Nasdaq-100 (QQQ), souvent cité, n'est pas suivi ici.",
)


def notes(indices):
    """
    Les notes du bas, comme l'avertissement du thermomètre. Les deux premières
    ne parlent de « la baisse en cours » que s'il y en a une (un indice sous
    son record). La dernière donne les sources et la date de la dernière
    clôture de chaque indice.
    """
    en_baisse = any(not x["en_cours"]["au_record"] for x in indices)
    n1 = ("Des faits du passé, pas une prévision : la baisse en cours peut finir demain ou durer des années."
          if en_baisse else "Des faits du passé, pas une prévision.") + " Aucun conseil d'achat ou de vente."
    n2 = ("Le tableau ne garde que les baisses finies, et les baisses finies sont revenues à leur record"
          + (" : celle en cours n'y est pas comptée. " if en_baisse else ". ") + NIKKEI)
    dates = [date.fromisoformat(x["cloture"]) for x in indices]
    if len(set(dates)) == 1:
        derniere = f" ; dernière clôture : {texte_date(dates[0], True)}."
    else:
        derniere = " ; dernières clôtures : " + ", ".join(
            f"{x['nom']} le {texte_date(d)}" for x, d in zip(indices, dates)) + "."
    sources = ("Cours de clôture de Yahoo, en prix seul : "
               + ", ".join(x["textes"]["source"] for x in indices) + derniere)
    return [_insecables(n) for n in (n1, n2, *NOTES_FIXES, sources)]


# ══════════════════════════════════════════════════════════════════
#  LA LECTURE D'UN INDICE, PUIS DES DEUX
# ══════════════════════════════════════════════════════════════════

def lire_indice(nom, cours, maintenant):
    """
    Tout ce que la page montre d'un indice, en nombres et en textes, prêt
    pour le JSON. Des cours inutilisables (aucune clôture, un cours nul,
    négatif ou infini) lèvent ValueError, comme un refus de Yahoo.
    """
    c = clotures(cours, maintenant)
    v = c.to_numpy(dtype=float)
    if not len(v) or not np.isfinite(v).all() or (v <= 0).any():
        raise ValueError(f"cours du {nom} inutilisables")
    ec = en_cours(c)
    liste = baisses(c)
    n_proche = len(baisses(c, SEUIL_PROCHE))
    proche = la_plus_proche(c)
    debut = _jour(c.index[0])
    iso = lambda d: d.isoformat() if d is not None else None    # noqa: E731
    # L'en-tête : « −11,6 % de son record », « à moins de 0,1 % de son record » (jamais « 0,0 % »),
    # ou « à son record ». Seul un pourcentage s'écrit en chiffres (tete_num).
    d_tete = None if ec["au_record"] else dixiemes_de_baisse(ec["derniere"], ec["record"])
    tete = "à son record" if ec["au_record"] else (texte_pct(d_tete) if d_tete else "à moins de 0,1 %")
    return {
        "nom": nom,
        "debut": iso(debut),
        "cloture": iso(ec["derniere_date"]),
        "tete_num": bool(d_tete),
        "en_cours": {"record_date": iso(ec["record_date"]), "record": ec["record"],
                     "derniere": ec["derniere"], "au_record": ec["au_record"],
                     "creux_date": iso(ec["creux_date"]), "creux": ec["creux"],
                     "seuil_date": iso(ec["seuil_date"])},
        "nombre": len(liste),
        "nombre_proche": n_proche,
        "textes": {
            "tete": _insecables(tete),
            "tete_suite": None if ec["au_record"] else "de son record",
            "en_cours": phrase_en_cours(nom, ec),
            "histoire": phrase_histoire(liste, debut),
            "barre": phrase_barre(len(liste), n_proche, proche),
            "detail": titre_detail(nom, len(liste), debut),
            "source": f"{nom} depuis le {texte_date(debut)}",
        },
        "baisses": [{"record_date": iso(b["record_date"]), "creux_date": iso(b["creux_date"]),
                     "fin_date": iso(b["fin_date"]), "record": b["record"], "creux": b["creux"],
                     "descente": b["descente"], "retour_record": b["retour_record"],
                     "retour_creux": b["retour_creux"], "textes": textes_ligne(b)} for b in liste],
    }


def lire_cours(symbole, essais=3):
    """
    Les clôtures d'un indice depuis son premier jour, en prix seul.

    PAS `thermometre._cours(symbole, "max")` : dans yfinance 1.2.0, « max » en
    journalier ne remonte que 99 ans (`yfinance/scrapers/history.py`). Le
    30/12/1927 du S&P 500 sortirait de la série dès le 05/12/2026, et la
    baisse de 1929 deviendrait fausse en août 2028, sans aucun message
    (trouvé par la relecture du 23/11/2026). On demande donc un début
    explicite. Le reste est celui du thermomètre, qui ne change pas : son
    nettoyage (`thermometre._nettoyer`) et 3 essais (2 s puis 6 s de pause).
    Une série coupée au début (voir PREMIERS_COURS) lève ValueError.
    """
    import time
    import yfinance as yf
    erreur, cours = None, None
    for n in range(essais):
        try:
            h = yf.Ticker(symbole).history(start=DEBUT_YAHOO, interval="1d", auto_adjust=False)
            if h is not None and not h.empty:
                cours = thermometre._nettoyer(h["Close"])
                break
            erreur = "réponse vide"
        except Exception as e:          # yfinance lève des erreurs variées quand Yahoo refuse
            erreur = str(e)
        if n < essais - 1:
            time.sleep(2 + 4 * n)
    if cours is None or not len(cours):
        raise ValueError(f"aucun cours reçu pour {symbole} ({erreur})")
    premier = PREMIERS_COURS.get(symbole)
    debut = _jour(cours.index[0])
    if premier is not None and (debut - premier).days > MARGE_DEBUT:
        raise ValueError(f"la série {symbole} de Yahoo commence le {texte_date(debut)}, "
                         f"pas le {texte_date(premier)} : elle est coupée")
    return cours


# La dernière bonne lecture de chaque indice, avec son heure. Le serveur
# appelle creux_du_marche sous son propre verrou : une seule à la fois.
_dernieres = {}


def creux_du_marche(maintenant=None, lire=lire_cours):
    """
    Les deux indices. Un indice que Yahoo refuse garde sa dernière bonne
    lecture, dite « dernière lecture du … ». Un indice sans aucune lecture
    lève une erreur : le cache du serveur ne la garde pas (une réponse à
    moitié vide resterait une heure), et la page réessaie au rafraîchissement
    suivant.
    """
    maintenant = maintenant or datetime.now(timezone.utc)
    indices = []
    for symbole, nom in INDICES:
        try:
            lecture = lire_indice(nom, lire(symbole), maintenant)
            lecture.update(symbole=symbole, lu_le=maintenant.isoformat(), frais=True, etat=None)
            _dernieres[symbole] = lecture
        except ValueError as e:
            ancienne = _dernieres.get(symbole)
            if ancienne is None:
                raise ValueError(f"aucune lecture du {nom} pour l'instant ({e})") from e
            # Un refus OU des cours inutilisables : un seul message, vrai dans les deux cas.
            lecture = dict(ancienne, frais=False, etat=_insecables(
                f"Yahoo n'a pas donné de nouveaux cours utilisables : dernière lecture du "
                f"{texte_heure(datetime.fromisoformat(ancienne['lu_le']))}."))
        indices.append(lecture)
    derniere = max(date.fromisoformat(x["cloture"]) for x in indices)
    # Un indice dont la clôture est plus ancienne que celle de la pastille (une vieille lecture) le
    # dit à côté de son chiffre, en tête. Des copies : la lecture gardée dans _dernieres ne change pas.
    sortie = []
    for x in indices:
        d = date.fromisoformat(x["cloture"])
        sortie.append(dict(x, textes=dict(x["textes"], tete_date=(
            None if d == derniere else f"(clôture du {texte_date_courte(d)})"))))
    return {"indices": sortie, "cloture": f"clôture du {texte_date(derniere, True)}",
            "legende": LEGENDE, "notes": notes(sortie)}


# ══════════════════════════════════════════════════════════════════
#  LES VÉRIFICATIONS (des séries inventées, calculées à la main)
# ══════════════════════════════════════════════════════════════════

def verifier():
    cas = []

    def ok(nom, condition):
        cas.append(bool(condition))
        print(("✓ " if condition else "✗ ") + nom)

    def serie(valeurs, debut="2026-01-05"):
        return pd.Series([float(x) for x in valeurs], index=pd.bdate_range(debut, periods=len(valeurs)))

    def aux_dates(paires):
        return pd.Series([float(v) for _, v in paires], index=pd.DatetimeIndex([d for d, _ in paires]))

    D = date.fromisoformat
    ny = lambda *a: datetime(*a, tzinfo=NY)     # noqa: E731
    n = " "                                # l'espace insécable, dans les textes attendus
    m = "−"                                # le vrai signe moins

    # 1. Un record égalé : la baisse finit, et la date du record avance.
    s = aux_dates([("2026-01-05", 100), ("2026-01-06", 70), ("2026-01-07", 100), ("2026-01-08", 90),
                   ("2026-01-09", 100), ("2026-01-12", 100), ("2026-01-13", 95)])
    b = baisses(s)
    ok("record égalé : la baisse de −30 % finit le jour où une clôture égale le record (07/01)",
       len(b) == 1 and b[0]["fin_date"] == D("2026-01-07") and b[0]["creux_date"] == D("2026-01-06"))
    ok("record égalé : descente 1 jour, retour 2 jours depuis le record et 1 jour depuis le creux",
       (b[0]["descente"], b[0]["retour_record"], b[0]["retour_creux"]) == (1, 2, 1))
    e = en_cours(s)
    ok("record égalé deux fois de suite (09/01 puis 12/01) : la baisse en cours part du dernier jour au record",
       e["record_date"] == D("2026-01-12") and not e["au_record"] and e["creux_date"] == D("2026-01-13"))
    ok("record égalé : la baisse de −10 % (08/01) est finie, mais trop petite pour le tableau",
       len(baisses(s, 0.05)) == 2 and baisses(s, 0.05)[1]["fin_date"] == D("2026-01-09"))

    # 2. Le seuil, écrit par une multiplication.
    ok("seuil : 100 → 80 (−20 % exactement) est gardée", len(baisses(serie([100, 80, 100]))) == 1)
    ok("seuil : 5 000 → 4 000 est gardée", len(baisses(serie([5000, 4000, 5000]))) == 1)
    ok("seuil : 100 → 80,01 est refusée", len(baisses(serie([100, 80.01, 100]))) == 0)
    ok("seuil : la division, elle, refuserait 100 → 80 (−0,19999999999999996) : d'où la multiplication",
       not (80 / 100 - 1 <= -0.20) and sous_le_seuil(80.0, 100.0))
    ok("seuil : 1 − 0,20 vaut exactement 0,8, et 1 − 0,19 exactement 0,81", 1 - SEUIL == 0.8 and 1 - SEUIL_PROCHE == 0.81)
    ok("seuil : la baisse en cours qui touche −20 % pile le voit aussi",
       en_cours(serie([100, 90, 80, 85]))["seuil_date"] == serie([100, 90, 80, 85]).index[2].date())

    # 3. Les pourcentages : tronqués vers zéro, calculés exactement.
    ok(f"pourcentage : −19,96 % s'écrit « {m}19,9 % » (tronqué, jamais « {m}20,0 % »)",
       texte_baisse(80.04, 100.0) == f"{m}19,9{n}%")
    ok(f"pourcentage : 100 → 80 s'écrit « {m}20,0 % » (le calcul ordinaire donnerait {m}19,9 %)",
       texte_baisse(80.0, 100.0) == f"{m}20,0{n}%")
    ok(f"pourcentage : 884 sous 1 000 s'écrit « {m}11,6 % » (le calcul ordinaire donnerait {m}11,5 %)",
       texte_baisse(884.0, 1000.0) == f"{m}11,6{n}%")
    ok(f"pourcentage : 6 910,39 sous 7 818,93 s'écrit « {m}11,6 % » (−11,62 %), et −0,04 % « 0,0 % » sans signe",
       texte_baisse(6910.39, 7818.93) == f"{m}11,6{n}%" and texte_baisse(99.96, 100.0) == f"0,0{n}%")
    ok(f"pourcentage : −86,19 % s'écrit « {m}86,1 % », pas « {m}86,2 % »", texte_baisse(4.40, 31.86) == f"{m}86,1{n}%")

    # 4. Deux creux égaux (le premier compte) ; deux baisses de suite.
    b = baisses(serie([100, 70, 75, 70, 100]))
    ok("deux creux égaux à 70 : le premier compte (descente d'un jour ouvré)",
       len(b) == 1 and b[0]["creux_date"] == serie([1]).index[0].date() + pd.Timedelta(days=1))
    b = baisses(aux_dates([("2026-01-05", 100), ("2026-01-06", 75), ("2026-01-07", 100), ("2026-01-08", 110),
                           ("2026-01-12", 80), ("2026-02-11", 110)]))
    ok("deux baisses de suite : 100 → 75 puis 110 → 80, avec leurs propres records",
       len(b) == 2 and b[0]["record_date"] == D("2026-01-05") and b[1]["record_date"] == D("2026-01-08")
       and b[1]["creux_date"] == D("2026-01-12") and b[1]["fin_date"] == D("2026-02-11"))
    ok("deux baisses de suite : la seconde descend en 4 jours et revient en 34 jours depuis le record, 30 depuis le creux",
       (b[1]["descente"], b[1]["retour_record"], b[1]["retour_creux"]) == (4, 34, 30))

    # 5. Une baisse qui ne finit pas ; une baisse en cours qui a touché −20 % ; une clôture au record.
    s = aux_dates([("2026-01-05", 100), ("2026-01-06", 85), ("2026-01-07", 79), ("2026-01-08", 60),
                   ("2026-01-09", 70)])
    ok("une baisse qui ne finit pas n'est jamais dans le tableau, même à −40 %", baisses(s) == [])
    e = en_cours(s)
    ok("baisse en cours : son creux pour l'instant (08/01, −40 %) et sa première clôture à −20 % ou plus (07/01)",
       e["creux_date"] == D("2026-01-08") and e["seuil_date"] == D("2026-01-07") and e["record_date"] == D("2026-01-05"))
    p = phrase_en_cours("S&P 500", e)
    ok("baisse en cours : la phrase donne ses deux dates (le record et la première clôture à −20 %)",
       p == (f"À la clôture du 9 janvier 2026, le S&P 500 est à {m}30,0{n}% de son record de clôture du "
             f"5 janvier 2026. Son creux pour l'instant{n}: {m}40,0{n}%, le 8 janvier 2026. La baisse commencée "
             f"le 5 janvier 2026 a atteint {m}20{n}% le 7 janvier 2026."))
    e = en_cours(aux_dates([("2026-01-05", 100), ("2026-01-06", 90), ("2026-01-07", 100)]))
    ok("une dernière clôture qui égale le record : à son record, record daté du dernier jour",
       e["au_record"] and e["record_date"] == D("2026-01-07") and e["creux_date"] is None and e["seuil_date"] is None)
    ok("une dernière clôture au record : la phrase", phrase_en_cours("Nasdaq Composite", e)
       == "À la clôture du 7 janvier 2026, le Nasdaq Composite est à son record.")
    e = en_cours(aux_dates([("2026-01-05", 100), ("2026-01-06", 95), ("2026-01-07", 97)]))
    ok("baisse en cours plus petite que −20 % : pas de phrase sur le seuil, le creux daté",
       phrase_en_cours("S&P 500", e) == (f"À la clôture du 7 janvier 2026, le S&P 500 est à {m}3,0{n}% de son "
                                         f"record de clôture du 5 janvier 2026. Son creux pour l'instant{n}: "
                                         f"{m}5,0{n}%, le 6 janvier 2026."))
    e = en_cours(aux_dates([("2026-01-05", 100), ("2026-01-06", 99.96)]))
    ok("baisse en cours de −0,04 % : « à moins de 0,1 % », c'est aussi son creux",
       phrase_en_cours("S&P 500", e) == (f"À la clôture du 6 janvier 2026, le S&P 500 est à moins de 0,1{n}% de "
                                         f"son record de clôture du 5 janvier 2026{n}; c'est aussi son creux pour "
                                         f"l'instant."))
    e = en_cours(serie([100, 80.5, 79, 85]))
    ok("baisse en cours : une clôture à −19,5 % ne compte pas, la première à −20 % ou plus (−21 %) oui",
       e["seuil_date"] == serie([100, 80.5, 79, 85]).index[2].date())
    e = en_cours(serie([100, 80.5, 90]))
    ok("baisse en cours restée à −19,5 % : pas de date à −20 %, pas de phrase dessus",
       e["seuil_date"] is None and "atteint" not in phrase_en_cours("Nasdaq Composite", e))
    e = en_cours(serie([6000, 6010, 6004.5, 6006]))
    ok("un creux pour l'instant tronqué à 0 : « à moins de 0,1 % du record », jamais « 0,0 % »",
       phrase_en_cours("S&P 500", e).endswith(f"Son creux pour l'instant{n}: à moins de 0,1{n}% du record, le 7 janvier 2026.")
       and f"0,0{n}%" not in phrase_en_cours("S&P 500", e))
    ok("une seule clôture : à son record", en_cours(serie([42]))["au_record"] and baisses(serie([42])) == [])
    try:
        en_cours(serie([]))
        ok("aucune clôture : refusé", False)
    except ValueError:
        ok("aucune clôture : refusé (ValueError)", True)

    # 6. La ligne du jour pendant la séance, ignorée.
    jour = aux_dates([("2026-11-18", 100), ("2026-11-19", 90), ("2026-11-20", 120)])
    ok("ligne du jour en pleine séance (11 h à New York) : retirée, la dernière clôture est celle de la veille",
       len(clotures(jour, ny(2026, 11, 20, 11, 0))) == 2)
    ok("ligne du jour à 15 h 59 à New York : encore retirée", len(clotures(jour, ny(2026, 11, 20, 15, 59))) == 2)
    ok("à 16 h à New York, la séance est finie : la ligne est une clôture", len(clotures(jour, ny(2026, 11, 20, 16, 0))) == 3)
    ok("le lendemain matin, la ligne d'hier reste", len(clotures(jour, ny(2026, 11, 21, 9, 0))) == 3)
    l1 = lire_indice("S&P 500", jour, ny(2026, 11, 20, 11, 0))
    l2 = lire_indice("S&P 500", jour, ny(2026, 11, 20, 16, 30))
    ok("en séance, le cours du moment (un nouveau record) est ignoré : à −10,0 % à la clôture du 19/11",
       l1["textes"]["tete"] == f"{m}10,0{n}%" and l1["cloture"] == "2026-11-19")
    ok("après la clôture : à son record", l2["textes"]["tete"] == "à son record" and l2["cloture"] == "2026-11-20")
    ok("heure de Paris : 21 h 30 à Paris = 15 h 30 à New York, la ligne est retirée",
       len(clotures(jour, datetime(2026, 11, 20, 21, 30, tzinfo=PARIS))) == 2)

    # 7. Les durées : la règle, sans trou.
    attendu = {0: "moins d'un mois", 14: "moins d'un mois", 15: "moins d'un mois", 16: f"1{n}mois",
               715: f"23{n}mois", 716: f"2{n}ans", 730: f"2{n}ans", 731: f"2{n}ans", 764: f"2,1{n}ans",
               8148: f"22,3{n}ans", 9131: f"25{n}ans", 9137: f"25{n}ans", 83: f"3{n}mois", 181: f"6{n}mois",
               785: f"2,1{n}ans"}      # 785 ÷ 365,25 = 2,149 (avec 365 jours : 2,151, donc « 2,2 ans »)
    faux = {j: texte_duree(j) for j, t in attendu.items() if texte_duree(j) != t}
    ok("durées : 14, 15, 16, 715, 716, 730, 731 jours, 25 ans (9 131 jours) et d'autres, comme calculés à la main"
       + (f" — faux : {faux}" if faux else ""), not faux)
    ok("durées : une médiane d'un demi-jour (532,5 jours) s'écrit « 17 mois », 380,5 jours « 13 mois » (12,5 arrondi vers le haut)",
       texte_duree(Fraction(1065, 2)) == f"17{n}mois" and texte_duree(Fraction(761, 2)) == f"13{n}mois")

    # 8. Les médianes : un nombre impair, puis pair (la moyenne des deux du milieu, pas de toutes).
    ok("médiane impaire : 10, 60, 50 → 50 (la moyenne serait 40)", mediane([10, 60, 50]) == 50)
    ok("médiane paire : 10, 1000, 20, 40 → 30 (la moyenne serait 267,5)", mediane([10, 1000, 20, 40]) == 30)
    ok("médiane paire d'un demi-jour : 1, 2 → 1,5 exactement", mediane([1, 2]) == Fraction(3, 2))
    ok("médiane : rien → None", mediane([]) is None)

    # Les retours du S&P 500 (le recompte du 22/11/2026, en jours) et les phrases du plan, écrites à la main.
    sp = [(9137, 8148), (782, 337), (630, 434), (449, 209), (1193, 650), (2744, 2114), (705, 83), (701, 600),
          (2623, 1694), (1997, 1480), (181, 148), (746, 464)]
    fabriquees = [{"retour_record": r, "retour_creux": c} for r, c in sp]
    ok("phrase, plusieurs baisses : celle du plan pour le S&P 500 (17 mois de 3 mois à 22,3 ans ; 2,1 ans de 6 mois à 25 ans)",
       phrase_histoire(fabriquees, D("1927-12-30")) == (
           f"Sur les clôtures, 12 baisses de 20{n}% ou plus depuis décembre 1927, toutes revenues à leur record. "
           f"Le retour a pris en médiane 17{n}mois depuis le creux (de 3{n}mois à 22,3{n}ans), et 2,1{n}ans "
           f"depuis le record (de 6{n}mois à 25{n}ans)."))
    nq = [(2065, 1435), (316, 254), (157, 109), (524, 83), (928, 531), (707, 645), (540, 168), (130, 50),
          (5522, 4579), (237, 120), (110, 77), (832, 428), (193, 80)]
    ok("phrase, nombre impair : celle du plan pour le Nasdaq (6 mois de 2 mois à 12,5 ans ; 17 mois de 4 mois à 15,1 ans)",
       phrase_histoire([{"retour_record": r, "retour_creux": c} for r, c in nq], D("1971-02-05")) == (
           f"Sur les clôtures, 13 baisses de 20{n}% ou plus depuis février 1971, toutes revenues à leur record. "
           f"Le retour a pris en médiane 6{n}mois depuis le creux (de 2{n}mois à 12,5{n}ans), et 17{n}mois "
           f"depuis le record (de 4{n}mois à 15,1{n}ans)."))
    ok("phrase, une seule baisse : sans médiane ni fourchette",
       phrase_histoire([{"retour_record": 181, "retour_creux": 148}], D("2020-01-02")) == (
           f"Sur les clôtures, une seule baisse de 20{n}% ou plus depuis janvier 2020, revenue à son record{n}: "
           f"en 5{n}mois depuis le creux, en 6{n}mois depuis le record."))
    ok("phrase, aucune baisse", phrase_histoire([], D("1971-02-05"))
       == f"Sur les clôtures, aucune baisse de 20{n}% ou plus n'est finie depuis février 1971.")
    ok("phrase, deux baisses de même durée : pas de fourchette « de 3 mois à 3 mois »",
       phrase_histoire([{"retour_record": 90, "retour_creux": 80}, {"retour_record": 92, "retour_creux": 85}],
                       D("2000-01-03")).endswith(f"en médiane 3{n}mois depuis le creux, et 3{n}mois depuis le record."))

    # La barre compte.
    proche = {"record": 100.0, "creux": 80.04, "creux_date": D("1990-10-11")}
    ok("barre : plus de baisses avec −19 %, et la plus proche (tronquée)", phrase_barre(12, 15, proche)
       == f"Avec {m}19{n}% au lieu de {m}20{n}%, il y en aurait 15. La plus proche de la barre s'est arrêtée à "
          f"{m}19,9{n}%, en octobre 1990.")
    ok("barre : autant avec −19 %", phrase_barre(13, 13, None)
       == f"Avec {m}19{n}% au lieu de {m}20{n}%, il y en aurait autant.")
    ok("barre : aucune, même avec −19 %", phrase_barre(0, 0, None)
       == f"Avec {m}19{n}% au lieu de {m}20{n}%, il n'y en aurait toujours aucune.")
    s = aux_dates([("2026-01-05", 100), ("2026-01-06", 80.5), ("2026-01-07", 101), ("2026-01-08", 88),
                   ("2026-01-09", 102), ("2026-01-12", 70), ("2026-01-13", 103), ("2026-01-14", 70)])
    pp = la_plus_proche(s)
    ok("la plus proche de la barre : la plus profonde des baisses FINIES restées au-dessus (−19,5 %), "
       "ni celle de −31 % (gardée), ni celle en cours", pp is not None and pp["creux_date"] == D("2026-01-06"))
    ok("barre : −19,5 % compte avec −19 % (2 baisses au lieu de 1)", len(baisses(s)) == 1 and len(baisses(s, SEUIL_PROCHE)) == 2)
    ok("la plus proche : aucune baisse finie restée au-dessus → None", la_plus_proche(serie([100, 70, 100])) is None)
    pp = la_plus_proche(serie([100, 80.5, 101, 1000, 830, 1001]))
    ok("la plus proche se mesure en %, pas en points : −19,5 % (19,5 points) avant −17 % (170 points)",
       pp is not None and pp["creux"] == 80.5)

    # Les autres textes.
    ok("dates : « 6 octobre 2026 », « 1ᵉʳ juin 1932 », « vendredi 20 novembre 2026 », « 16/09/1929 », « octobre 1990 »",
       texte_date(D("2026-10-06")) == "6 octobre 2026" and texte_date(D("1932-06-01")) == "1ᵉʳ juin 1932"
       and texte_date(D("2026-11-20"), True) == "vendredi 20 novembre 2026"
       and texte_date_courte(D("1929-09-16")) == "16/09/1929" and texte_mois(D("1990-10-11")) == "octobre 1990")
    ok("niveaux : 7 818,93 (espace fine des milliers) et 31,86",
       texte_niveau(7818.93) == "7 818,93" and texte_niveau(31.86) == "31,86" and texte_niveau(27599.79) == "27 599,79")
    ok("heure de Paris : « lundi 23 novembre 2026 à 10 h 05 » (depuis 9 h 05 UTC)",
       texte_heure(datetime(2026, 11, 23, 9, 5, tzinfo=timezone.utc)) == "lundi 23 novembre 2026 à 10 h 05")
    ok("titres du détail : plusieurs, une seule, aucune",
       titre_detail("S&P 500", 12, D("1927-12-30")) == f"Les 12 baisses de 20{n}% ou plus du S&P 500 depuis décembre 1927, sur les clôtures"
       and titre_detail("Nasdaq Composite", 1, D("1971-02-05")) == f"La seule baisse de 20{n}% ou plus du Nasdaq Composite depuis février 1971, sur les clôtures"
       and titre_detail("S&P 500", 0, D("1927-12-30")) is None)
    t = textes_ligne({"record_date": D("1929-09-16"), "record": 31.86, "creux_date": D("1932-06-01"), "creux": 4.40,
                      "descente": 989, "retour_record": 9137, "retour_creux": 8148})
    ok("une ligne du tableau (1929, écrite à la main)",
       t == {"record": "16/09/1929", "niveau": "31,86", "creux": "01/06/1932", "baisse": f"{m}86,1{n}%",
             "descente": f"2,7{n}ans", "retour_record": f"25{n}ans", "retour_creux": f"22,3{n}ans"})

    # 9. Les cours avec un FAUX Yahoo : un fuseau, une date en double ; puis la panne.
    import types

    class FauxTicker:
        def __init__(self, symbole):
            self.symbole = symbole

        def history(self, period=None, interval=None, auto_adjust=None, start=None):
            demandes.append((self.symbole, period, start, auto_adjust))
            if self.symbole in refuses:
                raise RuntimeError("Yahoo refuse")
            if start is not None:
                jours = pd.bdate_range(debuts.get(self.symbole, "2026-11-16"), periods=4)
                idx = jours.insert(2, jours[1]).tz_localize(NY)
                return pd.DataFrame({"Close": [10.0, 11.0, 9.0, 12.0, 13.0], "Adj Close": [9.0] * 5}, index=idx)
            idx = pd.DatetimeIndex(["2026-11-16", "2026-11-17", "2026-11-17", "2026-11-18", "2026-11-19"]).tz_localize(NY)
            brut = [100.0, 90.0, 70.0, 75.0, 80.0] if self.symbole == "^GSPC" else [50.0, 51.0, 52.0, 52.0, 53.0]
            return pd.DataFrame({"Close": brut, "Adj Close": [x * 1.1 for x in brut]}, index=idx)

    refuses = set()
    demandes = []
    debuts = {}
    ancien_yf = sys.modules.get("yfinance")
    anciennes = dict(_dernieres)
    sys.modules["yfinance"] = types.SimpleNamespace(Ticker=FauxTicker)
    _dernieres.clear()
    try:
        c = thermometre._cours("^GSPC", "max", essais=1)
        ok("Yahoo : un fuseau (New York) retiré, la date en double gardée une fois (la dernière), en prix seul",
           list(c.index.strftime("%Y-%m-%d")) == ["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19"]
           and list(c.values) == [100.0, 70.0, 75.0, 80.0] and c.index.tz is None)
        lire_vite = lambda s: thermometre._cours(s, "max", essais=1)    # noqa: E731
        apres = ny(2026, 11, 23, 4, 0)
        r = creux_du_marche(maintenant=apres, lire=lire_vite)
        sp, nq = r["indices"]
        ok("deux indices lus, dans l'ordre, frais", [x["symbole"] for x in r["indices"]] == ["^GSPC", "^IXIC"]
           and sp["frais"] and nq["frais"] and sp["etat"] is None)
        ok("S&P 500 inventé : à −20,0 % (80 sous 100), creux du 17/11 à −30,0 %, −20 % touché le 17/11",
           sp["textes"]["tete"] == f"{m}20,0{n}%" and sp["en_cours"]["creux_date"] == "2026-11-17"
           and sp["en_cours"]["seuil_date"] == "2026-11-17")
        ok("Nasdaq inventé : à son record (53), après une égalité à 52", nq["textes"]["tete"] == "à son record"
           and nq["en_cours"]["record_date"] == "2026-11-19")
        ok("la date de clôture de l'en-tête, et la ligne des sources avec la dernière clôture",
           r["cloture"] == "clôture du jeudi 19 novembre 2026"
           and r["notes"][-1] == (f"Cours de clôture de Yahoo, en prix seul{n}: S&P 500 depuis le 16 novembre 2026, "
                                  f"Nasdaq Composite depuis le 16 novembre 2026{n}; dernière clôture{n}: "
                                  f"jeudi 19 novembre 2026."))
        cles = ["pas une prévision", "Nikkei", "prix seul", "action seule", "second krach", "90 valeurs",
                "Nasdaq-100", "Cours de clôture de Yahoo"]
        ok("la réponse porte la légende et les 8 notes, chacune à sa place",
           r["legende"] == LEGENDE and len(r["notes"]) == 8 and all(k in x for k, x in zip(cles, r["notes"])))
        ok("une baisse en cours (le S&P 500) : les notes en parlent",
           "la baisse en cours peut finir demain" in r["notes"][0] and "celle en cours n'y est pas comptée" in r["notes"][1])
        ok("en tête, un pourcentage s'écrit en chiffres, « à son record » en mots, avec « de son record » après le chiffre",
           sp["tete_num"] and not nq["tete_num"] and sp["textes"]["tete_suite"] == "de son record"
           and nq["textes"]["tete_suite"] is None and sp["textes"]["tete_date"] is None)
        ok("la réponse s'écrit en JSON valide (aucun NaN, aucune date brute)",
           bool(json.dumps(r, allow_nan=False, ensure_ascii=False)))

        # 10. La panne : un indice refusé, avec puis sans lecture d'avant.
        refuses.add("^IXIC")
        r2 = creux_du_marche(maintenant=ny(2026, 11, 23, 5, 0), lire=lire_vite)
        ok("panne avec une lecture d'avant : le Nasdaq garde sa dernière lecture, dite avec son heure (Paris)",
           not r2["indices"][1]["frais"] and r2["indices"][0]["frais"]
           and r2["indices"][1]["etat"] == (f"Yahoo n'a pas donné de nouveaux cours utilisables{n}: dernière lecture "
                                            f"du lundi 23 novembre 2026 à 10 h 00.")
           and r2["indices"][1]["textes"] == nq["textes"])
        ok("panne : la dernière lecture n'est pas remplacée par la copie « ancienne »", _dernieres["^IXIC"]["frais"])
        _dernieres["^IXIC"] = dict(nq, cloture="2026-11-18")
        r4 = creux_du_marche(maintenant=ny(2026, 11, 23, 5, 0), lire=lire_vite)
        ok("panne : la pastille garde la clôture la plus récente des deux (19/11), pas celle de la vieille lecture",
           r4["cloture"] == "clôture du jeudi 19 novembre 2026")
        ok("panne : le chiffre de la vieille lecture dit sa clôture en tête, l'autre non",
           r4["indices"][1]["textes"]["tete_date"] == "(clôture du 18/11/2026)" and r4["indices"][0]["textes"]["tete_date"] is None)
        ok("panne : la ligne des sources donne les deux dernières clôtures",
           r4["notes"][-1].endswith(f"dernières clôtures{n}: S&P 500 le 19 novembre 2026, Nasdaq Composite le 18 novembre 2026."))
        ok("panne : la lecture gardée n'est pas changée par la copie envoyée", "tete_date" not in _dernieres["^GSPC"]["textes"])

        # lire_cours : un début explicite (pas « max », qui ne remonte que 99 ans dans yfinance 1.2.0),
        # le nettoyage du thermomètre, et une série coupée au début refusée.
        del demandes[:]
        debuts.update({"^GSPC": "1927-12-30"})
        c = lire_cours("^GSPC", essais=1)
        ok("lire_cours demande à Yahoo un début explicite (1927), en prix seul, jamais « max »",
           demandes == [("^GSPC", None, DEBUT_YAHOO, False)] and DEBUT_YAHOO == "1927-01-01")
        ok("lire_cours : fuseau retiré, date en double gardée une fois (la dernière), premier jour le 30/12/1927",
           list(c.index.strftime("%Y-%m-%d")) == ["1927-12-30", "1928-01-02", "1928-01-03", "1928-01-04"]
           and list(c.values) == [10.0, 9.0, 12.0, 13.0] and c.index.tz is None)
        debuts.update({"^GSPC": "1928-01-20"})
        ok("lire_cours : une série qui commence 21 jours après le premier cours connu passe",
           len(lire_cours("^GSPC", essais=1)) == 4)
        debuts.update({"^GSPC": "1928-03-01"})
        try:
            lire_cours("^GSPC", essais=1)
            ok("lire_cours : une série coupée au début (le S&P 500 le 01/03/1928) est refusée", False)
        except ValueError as err:
            ok("lire_cours : une série coupée au début (le S&P 500 le 01/03/1928) est refusée, et c'est dit",
               str(err) == "la série ^GSPC de Yahoo commence le 1ᵉʳ mars 1928, pas le 30 décembre 1927 : elle est coupée")
        ok("lire_cours : un symbole sans premier cours connu n'est pas contrôlé", len(lire_cours("^AUTRE", essais=1)) == 4)
        _dernieres.clear()
        try:
            creux_du_marche(maintenant=apres, lire=lire_vite)
            ok("panne sans aucune lecture : une erreur (le cache du serveur ne la garde pas)", False)
        except ValueError as err:
            ok("panne sans aucune lecture : une erreur, qui nomme l'indice", "Nasdaq Composite" in str(err))
        refuses.clear()
        cassee = lambda s: pd.Series([100.0, 0.0], index=pd.bdate_range("2026-01-05", periods=2))    # noqa: E731
        _dernieres["^GSPC"] = dict(sp)
        _dernieres["^IXIC"] = dict(nq)
        r3 = creux_du_marche(maintenant=apres, lire=cassee)
        ok("un cours nul chez Yahoo : traité comme un refus (la dernière lecture reste)",
           not r3["indices"][0]["frais"] and not r3["indices"][1]["frais"])
        for mauvais in ([100.0, float("nan")], [100.0, -5.0], [100.0, float("inf")]):
            try:
                lire_indice("S&P 500", pd.Series(mauvais, index=pd.bdate_range("2026-01-05", periods=2)), apres)
                ok(f"cours {mauvais[1]} : refusé", False)
            except ValueError:
                ok(f"cours {mauvais[1]} : refusé", True)
    finally:
        _dernieres.clear()
        _dernieres.update(anciennes)
        if ancien_yf is None:
            sys.modules.pop("yfinance", None)
        else:
            sys.modules["yfinance"] = ancien_yf

    # 11. Tous les textes : la typographie (espaces insécables, vrai signe moins), jamais un conseil.
    l3 = lire_indice("S&P 500", s, ny(2026, 1, 20, 9, 0))
    ok("lecture : la barre de −19 % comptée sur les cours (1 baisse, 2 avec −19 %, la plus proche à −19,5 %)",
       l3["nombre"] == 1 and l3["nombre_proche"] == 2 and l3["textes"]["barre"]
       == f"Avec {m}19{n}% au lieu de {m}20{n}%, il y en aurait 2. La plus proche de la barre s'est arrêtée à "
          f"{m}19,5{n}%, en janvier 2026.")
    ok("lecture : la phrase et le titre comptent les baisses de 20 %, pas celles de 19 % (une seule, pas 2)",
       l3["textes"]["histoire"].startswith("Sur les clôtures, une seule baisse")
       and l3["textes"]["detail"] == f"La seule baisse de 20{n}% ou plus du S&P 500 depuis janvier 2026, sur les clôtures")
    l4 = lire_indice("S&P 500", serie([100, 99.96]), ny(2026, 1, 20, 9, 0))
    ok("en tête, une baisse tronquée à 0 s'écrit « à moins de 0,1 % » (en mots), jamais « 0,0 % » ni « à son record »",
       l4["textes"]["tete"] == f"à moins de 0,1{n}%" and not l4["tete_num"] and l4["textes"]["tete_suite"] == "de son record")
    au_record = lire_indice("S&P 500", serie([100, 90, 101]), ny(2026, 1, 20, 9, 0))
    n_baisse, n_record = notes([l3]), notes([au_record, au_record])
    ok("les notes, deux indices à leur record : rien sur « la baisse en cours »",
       n_record[0] == "Des faits du passé, pas une prévision. Aucun conseil d'achat ou de vente."
       and n_record[1].startswith("Le tableau ne garde que les baisses finies, et les baisses finies sont revenues à leur "
                                  "record. Ailleurs") and not any("en cours" in x for x in n_record))
    ok("les notes, une baisse en cours : elles en parlent",
       n_baisse[0] == (f"Des faits du passé, pas une prévision{n}: la baisse en cours peut finir demain ou durer des "
                       f"années. Aucun conseil d'achat ou de vente.")
       and f"record{n}: celle en cours n'y est pas comptée. Ailleurs" in n_baisse[1])
    textes = [LEGENDE, *n_baisse, *n_record, *l3["textes"].values(), *l4["textes"].values(),
              *[v for b in l3["baisses"] for v in b["textes"].values()],
              phrase_en_cours("S&P 500", en_cours(serie([100, 90, 80, 85]))), phrase_barre(1, 2, proche)]
    import re
    mal = [t for t in textes if t and (any(x in t for x in (" :", " ;", " %", "« ", " »"))
                                       or re.search(r"(?<![\d])-\d[\d,]*\s?%", t))]
    ok("typographie : aucune espace ordinaire avant « : », « ; », « % », et le vrai signe moins"
       + (f" — {mal[0]!r}" if mal else ""), not mal)
    # Des mots entiers (« vendredi » n'est pas « vendre »). « garde » n'y est pas : « le tableau ne
    # garde que… » est un fait, pas un conseil.
    interdits = re.compile(r"\b(achète|achetez|acheter|vends|vendez|vendre|prévoi\w*|prédi\w*|reste à|il reste|"
                           r"remontera|rebondira|finira le|reviendra)\b", re.IGNORECASE)
    conseils = [t for t in textes if t and interdits.search(t)]
    ok("aucun conseil ni date de fin dans les textes" + (f" — {conseils[0]!r}" if conseils else ""), not conseils)
    visibles = " ".join(n_baisse)
    ok("les mots du plan : « prix seul », « les baisses finies sont revenues », « une action seule peut ne jamais revenir »",
       all(x in visibles for x in ("prix seul", "les baisses finies sont revenues", "une action seule peut ne jamais revenir")))
    ok("« sur les clôtures » dans les phrases et le titre du détail",
       "Sur les clôtures" in l3["textes"]["histoire"] and "sur les clôtures" in l3["textes"]["detail"])

    print(f"\n{sum(cas)}/{len(cas)} vérifications réussies")
    return all(cas)


# ══════════════════════════════════════════════════════════════════

def afficher(r):
    """Les deux tableaux dans le terminal, avec les jours (pour un recompte)."""
    for x in r["indices"]:
        t = x["textes"]
        print(f"\n{x['nom']} ({x['symbole']}) — {'lecture fraîche' if x['frais'] else x['etat']}")
        print(f"  {t['en_cours']}\n  {t['histoire']}\n  {t['barre']}")
        if t["detail"]:
            print(f"  {t['detail']} :")
        print(f"  {'Record':<11}{'Niveau':>11}  {'Creux':<11}{'Baisse':>9}  {'Descente':<17}"
              f"{'Retour (record)':<20}{'Retour (creux)':<20}Fin")
        for b in x["baisses"]:
            c = b["textes"]
            print(f"  {c['record']:<11}{c['niveau']:>11}  {c['creux']:<11}{c['baisse']:>9}  "
                  f"{c['descente'] + ' (' + str(b['descente']) + ' j)':<17}"
                  f"{c['retour_record'] + ' (' + str(b['retour_record']) + ' j)':<20}"
                  f"{c['retour_creux'] + ' (' + str(b['retour_creux']) + ' j)':<20}{b['fin_date']}")
    print()
    for note in r["notes"]:
        print(f"  · {note}")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--afficher", action="store_true", help="les deux tableaux dans le terminal (lit Yahoo)")
    p.add_argument("--verifier", action="store_true", help="les cas calculés à la main (sans réseau)")
    args = p.parse_args()
    if args.verifier:
        sys.exit(0 if verifier() else 1)
    if args.afficher:
        afficher(creux_du_marche())
        return
    p.print_help()


if __name__ == "__main__":
    main()
