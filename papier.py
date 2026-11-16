"""
Le mode papier — un défi de 4 semaines avec de l'argent FICTIF.

CE QUE ÇA FAIT
    Tient la liste des ordres fictifs (data/papier.json, où l'on ne fait
    qu'ajouter), en recalcule l'état à chaque lecture (liquidités,
    positions au prix moyen pondéré, plus-values réalisées, frais payés)
    et passe un ordre fictif au prix de la dernière minute de séance
    normale de New York.

    Le défi commence au PREMIER ORDRE : au même instant, les 200 € de
    l'adversaire vont dans le S&P 500 (SPY), au même prix de séance et
    avec le même 1 € de frais. Il dure 28 jours : le jour 1 est celui du
    premier ordre, le jour 28 le dernier.

CE QUE ÇA NE FAIT PAS
    Aucun ordre réel, aucune clé d'API, aucun conseil, aucune prédiction.
    Un ordre fictif ne touche jamais data/journal.json ni le carnet du
    bot (data/stock_positions.json).

LA RÈGLE DU PRIX
    Le prix d'un ordre est celui de la dernière minute de séance normale
    (ni pré- ni après-séance), relu au moment de l'ordre, et vieux de
    moins de 3 minutes. Cette seule règle refuse les week-ends, les jours
    fériés américains, la fin des séances raccourcies et l'après-séance,
    sans calendrier à tenir. Les heures sont toujours écrites avec leur
    fuseau : le Mac peut changer d'heure (en voyage), pas le défi.

LANCER
    python3 papier.py              # l'état du défi (lit les cours)
    python3 papier.py --verifier   # les cas calculés à la main, sans réseau
"""

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import ma_position   # noqa: E402  (les cours Yahoo, l'écriture d'un coup, les 49 valeurs)

PARIS = ma_position.PARIS
NEW_YORK = ZoneInfo("America/New_York")
INDICE = ma_position.INDICE     # SPY : le S&P 500, tel qu'un ETF le suit
CHANGE = ma_position.CHANGE     # EURUSD=X : combien de dollars vaut 1 €

CAPITAL_EUR = 200.0
FRAIS_EUR = 1.0             # par ordre, comme chez le courtier
JOURS = 28
ACHAT_MIN_EUR = 1.0
AGE_MAX_S = 180             # le prix d'un ordre : une minute de séance de moins de 3 minutes
AGE_MAX_CHANGE_S = 600      # le cours du dollar : moins de 10 minutes
AVANCE_TOLEREE_S = 120      # une minute « dans le futur » : les horloges ne sont jamais parfaitement d'accord
MEMO_S = 30                 # l'affichage relit les cours au plus toutes les 30 s ; un ordre, toujours
AVERTISSEMENT = ("Défi FICTIF : aucun ordre n'est envoyé, aucun euro réel n'est en jeu. "
                 "Des faits sur ton défi, aucun conseil ni prédiction.")
_verrou = threading.Lock()


# ══════════════════════════════════════════════════════════════════
#  LE FICHIER DU DÉFI
# ══════════════════════════════════════════════════════════════════

def chemin():
    """
    Le fichier du défi. Un serveur d'essai, qui lit une COPIE du journal
    (variable MA_POSITION_JOURNAL), doit avoir son propre fichier papier
    (MA_POSITION_PAPIER) : sinon ses ordres de test iraient dans le vrai
    défi.
    """
    propre = os.environ.get("MA_POSITION_PAPIER")
    if propre:
        return propre
    if os.environ.get("MA_POSITION_JOURNAL"):
        raise ValueError("Serveur d'essai sans fichier papier à lui (variable MA_POSITION_PAPIER) : "
                         "le défi papier est coupé pour protéger le vrai.")
    return os.path.join(ROOT, "data", "papier.json")


def nouveau_defi():
    return {"version": 1, "capital_eur": CAPITAL_EUR, "frais_ordre_eur": FRAIS_EUR,
            "jours": JOURS, "debut": None, "ordres": []}


def lire(fichier):
    """Le défi enregistré, ou un défi neuf. Un fichier abîmé n'est JAMAIS remplacé."""
    try:
        with open(fichier) as f:
            defi = json.load(f)
    except FileNotFoundError:
        return nouveau_defi()
    except ValueError as e:
        print(f"[papier] {fichier} illisible : {e}", file=sys.stderr)
        raise ValueError(f"{os.path.basename(fichier)} est illisible : le défi n'est pas touché.")
    if not isinstance(defi, dict) or not isinstance(defi.get("ordres"), list):
        raise ValueError(f"{os.path.basename(fichier)} n'a pas la forme d'un défi : il n'est pas touché.")
    return defi


# ══════════════════════════════════════════════════════════════════
#  LES CALCULS (purs : aucun réseau, vérifiés à la main par --verifier)
# ══════════════════════════════════════════════════════════════════

def rejouer(defi):
    """
    L'état du défi, recalculé ordre par ordre (le fichier ne garde que
    les ordres). Même règle que le journal réel (ma_position.agreger) :
    le prix moyen pondéré, frais d'achat compris ; une vente retire sa
    part du coût, au prorata des actions vendues. La plus-value d'une
    vente = ce qu'elle rapporte, son 1 € de frais déduit, moins cette part
    du coût.
    """
    liquidites = float(defi.get("capital_eur", CAPITAL_EUR))
    positions, realisees, frais = {}, [], 0.0
    for o in defi["ordres"]:
        frais += o["frais_eur"]
        p = positions.setdefault(o["ticker"], {"ticker": o["ticker"], "quantite": 0.0,
                                               "cout_eur": 0.0, "montant_eur": 0.0})
        if o["sens"] == "achat":
            liquidites -= o["montant_eur"] + o["frais_eur"]
            p["quantite"] += o["quantite"]
            p["cout_eur"] += o["montant_eur"] + o["frais_eur"]
            p["montant_eur"] += o["montant_eur"]
        else:
            part = o["quantite"] / p["quantite"] if p["quantite"] else 0.0
            cout_vendu = p["cout_eur"] * part
            recu = o["montant_eur"] - o["frais_eur"]
            liquidites += recu
            realisees.append({"date": o["date"], "ticker": o["ticker"],
                              "plus_value_eur": recu - cout_vendu})
            p["cout_eur"] -= cout_vendu
            p["montant_eur"] -= p["montant_eur"] * part
            p["quantite"] -= o["quantite"]
            if p["quantite"] <= 1e-9:
                del positions[o["ticker"]]
    return {"liquidites_eur": liquidites, "positions": list(positions.values()),
            "realisees": realisees, "frais_payes_eur": frais}


def jour_paris(instant):
    return instant.astimezone(PARIS).date()


def calendrier(defi, maintenant):
    """Le jour du défi (1 le jour du premier ordre, 28 le dernier), sa date de fin, s'il est fini."""
    if not defi.get("debut"):
        return {"jour": None, "debut_jour": None, "fin": None, "fini": False}
    debut = jour_paris(datetime.fromisoformat(defi["debut"]["date"]))
    fin = debut + timedelta(days=int(defi.get("jours", JOURS)) - 1)
    aujourd_hui = jour_paris(maintenant)
    return {"jour": (aujourd_hui - debut).days + 1, "debut_jour": debut.isoformat(),
            "fin": fin.isoformat(), "fini": aujourd_hui > fin}


def seance_du_jour(jour_ny):
    """L'ouverture et la clôture normales d'un jour de New York (9 h 30 – 16 h), à l'heure de Paris."""
    ouverture = datetime(jour_ny.year, jour_ny.month, jour_ny.day, 9, 30, tzinfo=NEW_YORK)
    cloture = datetime(jour_ny.year, jour_ny.month, jour_ny.day, 16, 0, tzinfo=NEW_YORK)
    return ouverture.astimezone(PARIS), cloture.astimezone(PARIS)


def fraiche(minute, maintenant, age_max=AGE_MAX_S):
    """Une minute de cours assez récente pour servir de prix ?"""
    age = (maintenant - minute).total_seconds()
    return -AVANCE_TOLEREE_S <= age <= age_max


# ── Les mots ──────────────────────────────────────────────────────

JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
           "septembre", "octobre", "novembre", "décembre"]


def date_fr(jour):
    """« jeudi 17 décembre » (une date, ou « AAAA-MM-JJ »)."""
    if isinstance(jour, str):
        jour = date.fromisoformat(jour)
    return f"{JOURS_FR[jour.weekday()]} {jour.day} {MOIS_FR[jour.month - 1]}"


def heure_fr(instant):
    """« 15 h 30 » ou « 22 h », à l'heure de Paris."""
    d = instant.astimezone(PARIS)
    return f"{d.hour} h {d.minute:02d}" if d.minute else f"{d.hour} h"


def quand_fr(instant, maintenant):
    """« 15:42 » le jour même, « mercredi 25 novembre à 21:59 » sinon (heure de Paris)."""
    d = instant.astimezone(PARIS)
    hhmm = f"{d.hour:02d}:{d.minute:02d}"
    return hhmm if d.date() == jour_paris(maintenant) else f"{date_fr(d.date())} à {hhmm}"


def euros(v):
    """1234.5 → « 1 234,50 € » (vrai signe moins, espaces insécables)."""
    s = f"{abs(v):,.2f}".replace(",", " ").replace(".", ",")
    return ("−" if round(v, 2) < 0 else "") + s + " €"


def qte(q):
    """0.5 → « 0,5 » ; 0.341234 → « 0,341234 »."""
    return f"{q:.6f}".rstrip("0").rstrip(".").replace(".", ",")


def de(ticker):
    """« de NVDA », mais « d'AAPL »."""
    return f"d'{ticker}" if ticker[:1] in "AEIOUY" else f"de {ticker}"


def message_fermee(maintenant, symbole=None, minute=None):
    """Pourquoi un ordre est impossible maintenant, à l'heure de Paris."""
    ny = maintenant.astimezone(NEW_YORK)
    ouverture, cloture = seance_du_jour(ny.date())
    horaires = f"de {heure_fr(ouverture)} à {heure_fr(cloture)}, heure de Paris"
    if ny.weekday() >= 5:
        return f"La Bourse de New York est fermée le week-end. Ordres du lundi au vendredi, {horaires}."
    if maintenant < ouverture:
        return (f"La séance de New York n'a pas commencé : ordres à partir de "
                f"{heure_fr(ouverture)}, heure de Paris.")
    if maintenant >= cloture:
        return f"La séance de New York est finie. Ordres du lundi au vendredi, {horaires}."
    pour = f" pour {symbole}" if symbole else ""
    depuis = f" (le dernier date de {quand_fr(minute, maintenant)})" if minute else ""
    return (f"Pas de cours de séance récent{pour}{depuis} : jour férié à New York, fin d'une "
            "séance raccourcie, ou Yahoo en retard. Ordres impossibles pour l'instant.")


# ══════════════════════════════════════════════════════════════════
#  LES COURS (réseau)
# ══════════════════════════════════════════════════════════════════

_memo = {}   # symbole → (instant de lecture, cours, minute en UTC)


def minute_de_seance(symbole, relire=False):
    """
    La dernière minute de séance NORMALE (ni pré- ni après-séance) :
    (cours en dollars, heure de la minute en UTC). Pour EURUSD=X, la
    dernière minute tout court : le change s'échange jour et nuit.
    """
    deja = _memo.get(symbole)
    if not relire and deja and time.monotonic() - deja[0] < MEMO_S:
        return deja[1], deja[2]
    c = ma_position._historique(symbole, period="5d", interval="1m", prepost=False)
    cours, minute = float(c.iloc[-1]), c.index[-1].to_pydatetime()
    if not math.isfinite(cours) or cours <= 0:
        raise ValueError(f"cours illisible pour {symbole}")
    _memo[symbole] = (time.monotonic(), cours, minute)
    return cours, minute


def _cours_d_ordre(lire_cours, symbole, maintenant, age_max=AGE_MAX_S):
    """Un cours relu pour un ordre : frais, sinon une phrase qui dit pourquoi."""
    try:
        cours, minute = lire_cours(symbole, relire=True)
    except Exception as e:     # yfinance lève des erreurs de toutes sortes
        print(f"[papier] cours de {symbole} : {e}", file=sys.stderr)
        raise ValueError(f"Yahoo n'a pas donné le cours de {symbole} : réessaie dans une minute.")
    if fraiche(minute, maintenant, age_max):
        return cours, minute
    if symbole == CHANGE:
        raise ValueError(f"Le cours du dollar n'est pas à jour (le dernier date de "
                         f"{quand_fr(minute, maintenant)}) : réessaie dans une minute.")
    raise ValueError(message_fermee(maintenant, symbole, minute))


# ══════════════════════════════════════════════════════════════════
#  PASSER UN ORDRE FICTIF
# ══════════════════════════════════════════════════════════════════

def _nombre(v, nom, exemple):
    try:
        x = float(str(v).strip().replace(" ", "").replace(" ", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError(f"{nom} : un nombre, par exemple {exemple}.")
    if not math.isfinite(x):
        raise ValueError(f"{nom} : un nombre, par exemple {exemple}.")
    return x


def lire_demande(payload):
    """Ce que demande le formulaire, vérifié AVANT tout appel réseau."""
    if not isinstance(payload, dict):
        raise ValueError("Demande illisible.")
    sens = str(payload.get("sens", "")).strip()
    if sens not in ("achat", "vente"):
        raise ValueError("Le sens est « achat » ou « vente ».")
    ticker = str(payload.get("ticker", "")).strip().upper()
    if ticker not in ma_position.engine.UNIVERSE:
        raise ValueError(f"{ticker or 'Cette valeur'} ne fait pas partie des 49 valeurs suivies.")
    raison = " ".join(str(payload.get("raison") or "").split())[:200]
    if not raison:
        raise ValueError("La raison est obligatoire : une phrase, c'est le cœur du défi.")
    demande = {"sens": sens, "ticker": ticker, "raison": raison}
    if sens == "achat":
        montant = round(_nombre(payload.get("montant_eur"), "Le montant", "50 ou 12,50"), 2)
        if montant < ACHAT_MIN_EUR:
            raise ValueError(f"Un achat d'au moins {euros(ACHAT_MIN_EUR)}.")
        demande["montant_eur"] = montant
    elif payload.get("tout") in (True, 1, "1", "true", "on"):
        demande["tout"] = True
    else:
        quantite = round(_nombre(payload.get("quantite"), "La quantité", "0,25"), 6)
        if quantite <= 0:
            raise ValueError("La quantité doit être plus grande que 0.")
        demande["quantite"] = quantite
    return demande


def passer_ordre(payload, *, maintenant=None, lire_cours=minute_de_seance, fichier=None):
    """
    Passe un ordre FICTIF et l'ajoute au défi. Un ordre refusé lève
    ValueError avec une phrase lisible, et rien n'est écrit.
    `maintenant`, `lire_cours` et `fichier` ne servent qu'aux vérifications.
    """
    demande = lire_demande(payload)
    fichier = fichier or chemin()
    with _verrou:
        maintenant = maintenant or datetime.now(timezone.utc)
        defi = lire(fichier)
        cal = calendrier(defi, maintenant)
        if cal["fini"]:
            raise ValueError(f"Le défi est fini depuis le {date_fr(cal['fin'])} : plus d'ordres.")
        etat = rejouer(defi)
        frais = float(defi.get("frais_ordre_eur", FRAIS_EUR))
        ticker = demande["ticker"]

        # Ce qui se vérifie sans les cours.
        if demande["sens"] == "achat":
            if demande["montant_eur"] + frais > etat["liquidites_eur"] + 1e-9:
                reste = etat["liquidites_eur"]
                if reste - frais < ACHAT_MIN_EUR:
                    raise ValueError(f"Il te reste {euros(reste)} : pas assez pour un achat, "
                                     f"avec le {euros(frais)} de frais.")
                raise ValueError(f"Il te reste {euros(reste)} : avec le {euros(frais)} de frais, "
                                 f"tu peux acheter pour {euros(reste - frais)} au plus.")
        else:
            detenue = next((p for p in etat["positions"] if p["ticker"] == ticker), None)
            if not detenue:
                raise ValueError(f"Tu ne détiens pas {de(ticker)} dans ce défi.")
            quantite = detenue["quantite"] if demande.get("tout") else demande["quantite"]
            if quantite > detenue["quantite"] + 1e-9:
                raise ValueError(f"Tu ne détiens que {qte(detenue['quantite'])} {ticker} dans ce défi.")

        # Les cours, relus maintenant : jamais ceux de l'affichage.
        prix_usd, minute = _cours_d_ordre(lire_cours, ticker, maintenant)
        fx, minute_fx = _cours_d_ordre(lire_cours, CHANGE, maintenant, AGE_MAX_CHANGE_S)
        debut = None
        if not defi.get("debut"):
            # Le premier ordre lance le défi : l'adversaire achète le S&P 500
            # au même instant. Sans cours frais pour lui, pas de départ équitable.
            try:
                spy, minute_spy = _cours_d_ordre(lire_cours, INDICE, maintenant)
            except ValueError:
                raise ValueError("Le S&P 500 n'a pas de cours de séance récent : le défi ne peut pas "
                                 "commencer équitablement. Réessaie dans une minute.")
            capital = float(defi.get("capital_eur", CAPITAL_EUR))
            debut = {"date": maintenant.isoformat(timespec="seconds"),
                     "indice": {"ticker": INDICE, "prix_usd": round(spy, 4),
                                "minute": minute_spy.isoformat(), "change": round(fx, 6),
                                "montant_eur": capital - frais, "frais_eur": frais,
                                "quantite": (capital - frais) / (round(spy, 4) / round(fx, 6))}}

        prix_eur = round(prix_usd / fx, 4)
        if demande["sens"] == "achat":
            montant = demande["montant_eur"]
            quantite = round(montant / prix_eur, 6)
            if quantite <= 0:
                raise ValueError("Ce montant est trop petit pour une fraction d'action.")
        else:
            montant = round(quantite * prix_eur, 2)
            if montant <= frais:
                raise ValueError(f"Cette vente rapporterait {euros(montant)}, pas plus que son "
                                 f"{euros(frais)} de frais.")

        ordre = {"date": maintenant.isoformat(timespec="seconds"), "sens": demande["sens"],
                 "ticker": ticker, "quantite": quantite, "prix_usd": round(prix_usd, 4),
                 "change": round(fx, 6), "prix_eur": prix_eur, "minute": minute.isoformat(),
                 "montant_eur": montant, "frais_eur": frais, "raison": demande["raison"]}
        if debut:
            defi["debut"] = debut
        defi["ordres"].append(ordre)
        ma_position._ecrire(fichier, defi)
    return ordre


# ══════════════════════════════════════════════════════════════════
#  CE QUE LIT LE CARNET
# ══════════════════════════════════════════════════════════════════

def etat(*, maintenant=None, lire_cours=minute_de_seance, fichier=None):
    """
    Le défi tel qu'il est maintenant. Les positions sont comptées au prix
    de la dernière minute de séance normale, comme les ordres : hors
    séance, c'est celui de la clôture. Un cours manquant n'empêche pas
    d'afficher le reste.
    """
    fichier = fichier or chemin()
    maintenant = maintenant or datetime.now(timezone.utc)
    defi = lire(fichier)
    r = rejouer(defi)
    cal = calendrier(defi, maintenant)
    capital = float(defi.get("capital_eur", CAPITAL_EUR))
    frais = float(defi.get("frais_ordre_eur", FRAIS_EUR))

    manquants = []

    def cours(symbole):
        try:
            return lire_cours(symbole)
        except Exception as e:
            print(f"[papier] cours de {symbole} : {e}", file=sys.stderr)
            manquants.append(symbole)
            return None

    fx, spy = cours(CHANGE), cours(INDICE)
    positions = []
    for p in r["positions"]:
        c = cours(p["ticker"])
        ligne = {**p, "prix_moyen_eur": p["montant_eur"] / p["quantite"], "prix_usd": None,
                 "prix_eur": None, "minute": None, "valeur_eur": None, "gain_eur": None, "gain_pct": None}
        if c and fx:
            prix_eur = c[0] / fx[0]
            valeur = p["quantite"] * prix_eur
            ligne.update(prix_usd=c[0], prix_eur=prix_eur, minute=c[1].isoformat(), valeur_eur=valeur,
                         gain_eur=valeur - p["cout_eur"], gain_pct=valeur / p["cout_eur"] - 1)
        positions.append(ligne)
    complet = all(l["valeur_eur"] is not None for l in positions)
    total = r["liquidites_eur"] + sum(l["valeur_eur"] for l in positions) if complet else None

    # La séance : la même règle que les ordres, lue sur le S&P 500.
    if spy and fx:
        ouverte = fraiche(spy[1], maintenant)
        message = None if ouverte else message_fermee(maintenant, INDICE, spy[1])
    else:
        ouverte = False
        message = "Cours indisponibles pour l'instant (Yahoo) : ordres impossibles. Nouvel essai dans une minute."
    if cal["fini"]:
        ouverts, pourquoi = False, f"Le défi est fini depuis le {date_fr(cal['fin'])} : plus d'ordres."
    else:
        ouverts, pourquoi = ouverte, message

    # L'adversaire (le duel s'affichera avec la partie 2 du plan).
    adversaire = None
    if defi.get("debut") and spy and fx:
        ind = defi["debut"]["indice"]
        valeur = ind["quantite"] * spy[0] / fx[0]
        adversaire = {"ticker": ind["ticker"], "valeur_eur": valeur, "gain_eur": valeur - capital,
                      "prix_depart_usd": ind["prix_usd"], "change_depart": ind["change"]}

    return {
        "maj": maintenant.isoformat(timespec="seconds"),
        "capital_eur": capital, "frais_ordre_eur": frais, "jours": int(defi.get("jours", JOURS)),
        "debut": defi["debut"]["date"] if defi.get("debut") else None,
        **cal,
        "seance": {"ouverte": ouverte, "minute": spy[1].isoformat() if spy else None},
        "ordres_ouverts": ouverts, "ordres_message": pourquoi,
        "liquidites_eur": r["liquidites_eur"],
        "positions": positions,
        "total_eur": total,
        "gain_eur": total - capital if total is not None else None,
        "gain_pct": total / capital - 1 if total is not None else None,
        "frais_payes_eur": r["frais_payes_eur"],
        "frais_payes_pct": r["frais_payes_eur"] / capital,
        "plus_values_realisees_eur": sum(x["plus_value_eur"] for x in r["realisees"]),
        "ordres": defi["ordres"],
        "adversaire": adversaire,
        "cours_manquants": manquants,
        "valeurs": sorted(ma_position.engine.UNIVERSE),
        "avertissement": AVERTISSEMENT,
    }


# ══════════════════════════════════════════════════════════════════
#  LES VÉRIFICATIONS (aucun réseau : des cours inventés, un dossier jetable)
# ══════════════════════════════════════════════════════════════════

def verifier():
    proche = lambda a, b, tol=1e-6: a is not None and abs(a - b) < tol
    cas = []

    def ok(nom, condition):
        cas.append(bool(condition))
        print(("✓ " if condition else "✗ ") + nom)

    UTC = timezone.utc

    def faux(table):
        """Un lecteur de cours inventé : {symbole: (cours en $, minute en UTC)}."""
        def lire_cours(symbole, relire=False):
            if symbole not in table:
                raise RuntimeError("pas de cours")
            return table[symbole]
        return lire_cours

    def refus(fn, *mots):
        """L'ordre est refusé, et la phrase contient ces mots."""
        try:
            fn()
        except ValueError as e:
            return all(m in str(e) for m in mots), str(e)
        return False, "accepté"

    def empreinte(fichier):
        try:
            with open(fichier, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except FileNotFoundError:
            return None

    # Lundi 23/11/2026, 16 h à Paris = 15 h UTC = 10 h à New York (heure d'hiver des deux côtés).
    T0 = datetime(2026, 11, 23, 15, 0, tzinfo=UTC)
    m = lambda t, s=60: t - timedelta(seconds=s)        # une minute de cours, 60 s avant t
    # 1 € = 1,10 $ ; NVDA à 110 $ = 100 € ; SPY à 660 $ = 600 € ; AAPL à 220 $ = 200 €.
    cours = lambda t, nvda=110.0, **autres: faux({"NVDA": (nvda, m(t)), "EURUSD=X": (1.10, m(t)),
                                                  "SPY": (660.0, m(t)), "AAPL": (220.0, m(t)), **autres})

    with tempfile.TemporaryDirectory() as dossier:
        f = os.path.join(dossier, "papier.json")
        ordre = lambda payload, t=T0, lc=None: passer_ordre(payload, maintenant=t,
                                                           lire_cours=lc or cours(t), fichier=f)
        achat = lambda montant, ticker="NVDA", raison="Essai": {"sens": "achat", "ticker": ticker,
                                                                "montant_eur": montant, "raison": raison}
        vente = lambda q, ticker="NVDA": {"sens": "vente", "ticker": ticker, "quantite": q, "raison": "Essai"}

        # ── Avant le premier ordre : rien n'est écrit, le défi n'a pas commencé.
        e0 = etat(maintenant=T0, lire_cours=cours(T0), fichier=f)
        ok("défi neuf : 200 € de liquidités, pas commencé, aucun fichier écrit",
           proche(e0["liquidites_eur"], 200) and e0["jour"] is None and empreinte(f) is None
           and proche(e0["total_eur"], 200) and e0["ordres_ouverts"])

        # ── Les refus qui ne demandent aucun cours.
        for nom, payload, mots in [
            ("raison vide", {**achat(50), "raison": "   "}, ("raison est obligatoire",)),
            ("valeur hors des 49", achat(50, "ZZZZ"), ("ZZZZ", "49 valeurs")),
            ("montant de 0,50 €", achat(0.5), ("au moins",)),
            ("montant illisible", achat("abc"), ("Le montant", "un nombre")),
            ("sens inconnu", {**achat(50), "sens": "short"}, ("achat", "vente")),
            ("vente d'une valeur pas détenue", vente(0.1, "AAPL"), ("ne détiens pas d'AAPL",)),
            ("le défi commence par un achat", vente(0.1), ("ne détiens pas de NVDA",)),
            ("quantité nulle", vente(0), ("plus grande que 0",)),
        ]:
            r_ok, texte = refus(lambda: ordre(payload), *mots)
            ok(f"refusé : {nom} → « {texte} »", r_ok and empreinte(f) is None)

        # ── Les refus de séance : week-end, avant, après, jour férié, séance raccourcie.
        samedi = datetime(2026, 11, 21, 15, 0, tzinfo=UTC)
        vendredi_cloture = datetime(2026, 11, 20, 20, 59, tzinfo=UTC)          # 15 h 59 à New York
        ferme = lambda t, derniere: faux({"NVDA": (110.0, derniere), "EURUSD=X": (1.10, m(t)),
                                          "SPY": (660.0, derniere)})
        for nom, t, derniere, mots in [
            ("samedi", samedi, vendredi_cloture, ("week-end", "15 h 30 à 22 h")),
            ("lundi 9 h à Paris, avant l'ouverture", datetime(2026, 11, 23, 8, 0, tzinfo=UTC),
             vendredi_cloture, ("n'a pas commencé", "15 h 30")),
            ("lundi 22 h 30 à Paris, après la clôture", datetime(2026, 11, 23, 21, 30, tzinfo=UTC),
             datetime(2026, 11, 23, 20, 59, tzinfo=UTC), ("est finie",)),
            ("Thanksgiving (jeudi 26/11, 17 h à Paris)", datetime(2026, 11, 26, 16, 0, tzinfo=UTC),
             datetime(2026, 11, 25, 20, 59, tzinfo=UTC), ("Pas de cours de séance récent pour NVDA",
                                                           "mercredi 25 novembre à 21:59", "jour férié")),
            ("vendredi 27/11 à 19 h 30, après la clôture de 13 h à New York",
             datetime(2026, 11, 27, 18, 30, tzinfo=UTC), datetime(2026, 11, 27, 17, 59, tzinfo=UTC),
             ("Pas de cours de séance récent", "18:59")),
        ]:
            r_ok, texte = refus(lambda: ordre(achat(50), t, ferme(t, derniere)), *mots)
            ok(f"refusé : {nom} → « {texte} »", r_ok and empreinte(f) is None)
        ok("samedi 31/10 : Paris est passé à l'heure d'hiver, pas encore New York → de 14 h 30 à 21 h",
           "14 h 30 à 21 h" in message_fermee(datetime(2026, 10, 31, 12, 0, tzinfo=UTC)))
        ok("le 16/11, de 15 h 30 à 22 h",
           [heure_fr(x) for x in seance_du_jour(date(2026, 11, 16))] == ["15 h 30", "22 h"])

        r_ok, texte = refus(lambda: ordre(achat(50), T0, faux({"NVDA": (110.0, m(T0)),
                                                              "EURUSD=X": (1.10, m(T0, 1200)),
                                                              "SPY": (660.0, m(T0))})), "dollar")
        ok(f"refusé : cours du dollar vieux de 20 min → « {texte} »", r_ok and empreinte(f) is None)
        r_ok, texte = refus(lambda: ordre(achat(50), T0, faux({"NVDA": (110.0, m(T0)),
                                                              "EURUSD=X": (1.10, m(T0)),
                                                              "SPY": (660.0, m(T0, 600))})), "équitablement")
        ok(f"refusé : premier ordre sans cours frais du S&P 500 → « {texte} »", r_ok and empreinte(f) is None)
        r_ok, texte = refus(lambda: ordre(achat(50), T0, faux({"EURUSD=X": (1.10, m(T0)),
                                                              "SPY": (660.0, m(T0))})), "Yahoo", "NVDA")
        ok(f"refusé : Yahoo ne répond pas → « {texte} »", r_ok and empreinte(f) is None)
        r_ok, _ = refus(lambda: ordre(achat(50), T0, cours(T0, NVDA=(110.0, m(T0, 181)))))
        ok("refusé : une minute de 3 min 1 s ; acceptée à 2 min 59 (vérifié plus bas)", r_ok and empreinte(f) is None)

        # ── 1. Le premier ordre : 50 € de NVDA à 100 € → 0,5 action, et le défi commence.
        o1 = passer_ordre(achat(50, raison="  Premier   essai  "), maintenant=T0,
                          lire_cours=cours(T0, NVDA=(110.0, m(T0, 179))), fichier=f)
        d = lire(f)
        ok("1er ordre : 0,5 NVDA à 100 € (110 $ au change de 1,10), 50 € + 1 € de frais",
           proche(o1["quantite"], 0.5) and proche(o1["prix_eur"], 100) and o1["montant_eur"] == 50
           and o1["frais_eur"] == 1 and o1["raison"] == "Premier essai")
        ok("le défi commence au premier ordre ; l'adversaire met 199 € dans SPY à 600 € (0,331667 part)",
           d["debut"]["date"] == T0.isoformat() and proche(d["debut"]["indice"]["quantite"], 199 / 600)
           and d["debut"]["indice"]["montant_eur"] == 199)
        e1 = etat(maintenant=T0, lire_cours=cours(T0), fichier=f)
        ok("état : 149 € de liquidités, 0,5 NVDA valant 50 €, total 199 €, gain −1 € (le frais)",
           proche(e1["liquidites_eur"], 149) and proche(e1["positions"][0]["valeur_eur"], 50)
           and proche(e1["total_eur"], 199) and proche(e1["gain_eur"], -1) and e1["jour"] == 1)
        ok("frais payés : 1 €, soit 0,5 % du capital", proche(e1["frais_payes_eur"], 1)
           and proche(e1["frais_payes_pct"], 0.005))
        ok("l'adversaire vaut 199 € tant que SPY ne bouge pas", proche(e1["adversaire"]["valeur_eur"], 199))

        avant = empreinte(f)
        r_ok, texte = refus(lambda: ordre(achat(149)), "149,00", "148,00")
        ok(f"refusé : 149 € quand il en reste 149 (le frais en plus) → « {texte} »", r_ok and empreinte(f) == avant)
        r_ok, texte = refus(lambda: ordre(vente(0.6)), "que 0,5 NVDA")
        ok(f"refusé : vendre 0,6 quand on a 0,5 → « {texte} »", r_ok and empreinte(f) == avant)

        # ── 2. Un second achat plus cher : 30 € de NVDA à 120 € (132 $) → 0,25 action.
        t2 = T0 + timedelta(hours=1)
        passer_ordre(achat(30), maintenant=t2, lire_cours=cours(t2, nvda=132.0), fichier=f)
        r2 = rejouer(lire(f))
        p2 = r2["positions"][0]
        ok("prix moyen pondéré : 0,75 action, coût 82 € (frais compris), 80 € sans les frais",
           proche(p2["quantite"], 0.75) and proche(p2["cout_eur"], 82) and proche(p2["montant_eur"], 80)
           and proche(r2["liquidites_eur"], 118))
        agr = ma_position.agreger(lire(f)["ordres"])[0]
        ok("même règle que le journal réel (ma_position.agreger)",
           proche(agr["quantite"], p2["quantite"]) and proche(agr["cout_eur"], p2["cout_eur"])
           and proche(agr["montant_eur"], p2["montant_eur"]))

        # ── 3. Vendre la moitié à 110 € (121 $) : 41,25 € − 1 € = 40,25 € reçus,
        #       pour une part de coût de 41 € → plus-value de −0,75 €.
        t3 = T0 + timedelta(hours=2)
        o3 = passer_ordre(vente(0.375), maintenant=t3, lire_cours=cours(t3, nvda=121.0), fichier=f)
        r3 = rejouer(lire(f))
        ok("vente de la moitié : 41,25 € (dont 1 € de frais), plus-value −0,75 €, 158,25 € de liquidités",
           o3["montant_eur"] == 41.25 and proche(r3["realisees"][0]["plus_value_eur"], -0.75)
           and proche(r3["liquidites_eur"], 158.25) and proche(r3["positions"][0]["cout_eur"], 41))
        agr = ma_position.agreger(lire(f)["ordres"])[0]
        ok("après la vente, toujours la même règle que le journal réel",
           proche(agr["cout_eur"], r3["positions"][0]["cout_eur"])
           and proche(agr["quantite"], r3["positions"][0]["quantite"]))

        # ── 4. « Tout » vendre au même prix : la ligne disparaît, tout est réalisé.
        passer_ordre({"sens": "vente", "ticker": "NVDA", "tout": True, "raison": "Fin"},
                     maintenant=t3 + timedelta(minutes=5),
                     lire_cours=cours(t3 + timedelta(minutes=5), nvda=121.0), fichier=f)
        r4 = rejouer(lire(f))
        ok("tout vendre : plus de position, 198,50 € (+2,50 € de cours, −4 € de frais)",
           r4["positions"] == [] and proche(r4["liquidites_eur"], 198.5) and proche(r4["frais_payes_eur"], 4)
           and proche(sum(x["plus_value_eur"] for x in r4["realisees"]), -1.5))

        # ── 5. Une vente qui ne couvre pas son frais : 1 € d'achat (0,01 action), revendue à moitié.
        t5 = T0 + timedelta(hours=3)
        passer_ordre(achat(1), maintenant=t5, lire_cours=cours(t5), fichier=f)
        avant = empreinte(f)
        r_ok, texte = refus(lambda: ordre(vente(0.005), t5, cours(t5)), "0,50", "pas plus que")
        ok(f"refusé : une vente de 0,50 € → « {texte} »", r_ok and empreinte(f) == avant)

        # ── 6. Le calendrier : jour 1 le jour du premier ordre, jour 28 le dernier.
        jour28 = datetime(2026, 12, 20, 15, 0, tzinfo=UTC)                     # dimanche
        jour29 = datetime(2026, 12, 21, 15, 0, tzinfo=UTC)                     # lundi
        c28, c29 = calendrier(lire(f), jour28), calendrier(lire(f), jour29)
        ok("commencé le lundi 23/11 : le jour 28 est le dimanche 20/12, le défi est fini le lundi 21/12",
           c28["jour"] == 28 and not c28["fini"] and c28["fin"] == "2026-12-20" and c29["fini"])
        dernier = datetime(2026, 12, 18, 15, 0, tzinfo=UTC)                    # vendredi, jour 26
        passer_ordre(achat(2), maintenant=dernier, lire_cours=cours(dernier), fichier=f)
        avant = empreinte(f)
        r_ok, texte = refus(lambda: ordre(achat(2), jour29, cours(jour29)), "fini", "dimanche 20 décembre")
        ok(f"refusé : le jour 29 → « {texte} »", r_ok and empreinte(f) == avant)
        ok("état d'un défi fini : ordres fermés, avec la raison",
           not etat(maintenant=jour29, lire_cours=cours(jour29), fichier=f)["ordres_ouverts"])

        # ── 7. L'affichage quand Yahoo manque un cours, ou hors séance.
        trou = faux({"EURUSD=X": (1.10, m(T0)), "SPY": (660.0, m(T0))})
        e7 = etat(maintenant=T0, lire_cours=trou, fichier=f)
        ok("un cours manquant : la ligne sans valeur, pas de total inventé, le reste affiché",
           e7["cours_manquants"] == ["NVDA"] and e7["total_eur"] is None
           and e7["positions"][0]["valeur_eur"] is None and proche(e7["liquidites_eur"], 193.5))
        e8 = etat(maintenant=samedi, lire_cours=ferme(samedi, vendredi_cloture), fichier=f)
        ok("hors séance : ordres fermés, la raison dite, le total compté à la clôture",
           not e8["ordres_ouverts"] and "week-end" in e8["ordres_message"] and e8["total_eur"] is not None)

        # ── 8. Rien d'autre n'est écrit que le fichier du défi.
        ok("le dossier ne contient que papier.json (aucun fichier .tmp oublié)", os.listdir(dossier) == ["papier.json"])

        # ── 9. Un fichier abîmé n'est jamais remplacé.
        with open(f, "w") as g:
            g.write("{abîmé")
        avant = empreinte(f)
        r_ok, texte = refus(lambda: ordre(achat(5)), "illisible")
        ok(f"refusé : fichier abîmé, laissé tel quel → « {texte} »", r_ok and empreinte(f) == avant)

    # ── 10. Le serveur d'essai ne touche jamais le vrai défi.
    garde = {k: os.environ.get(k) for k in ("MA_POSITION_PAPIER", "MA_POSITION_JOURNAL")}
    try:
        for k in garde:
            os.environ.pop(k, None)
        ok("par défaut : data/papier.json", chemin() == os.path.join(ROOT, "data", "papier.json"))
        os.environ["MA_POSITION_JOURNAL"] = "/tmp/copie/journal.json"
        r_ok, _ = refus(chemin, "MA_POSITION_PAPIER")
        ok("journal en copie SANS fichier papier à lui : refusé", r_ok)
        os.environ["MA_POSITION_PAPIER"] = "/tmp/copie/papier.json"
        ok("journal en copie AVEC son fichier papier : celui-là", chemin() == "/tmp/copie/papier.json")
    finally:
        for k, v in garde.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    ok("les mots : 1234,5 € → « 1 234,50 € », −0,004 € → « 0,00 € », 0,341234 et « d'AAPL »",
       euros(1234.5) == "1 234,50 €" and euros(-0.004) == "0,00 €"
       and qte(0.341234) == "0,341234" and qte(0.5) == "0,5" and de("AAPL") == "d'AAPL")

    print(f"\n{sum(cas)}/{len(cas)} vérifications réussies")
    return all(cas)


def main():
    parser = argparse.ArgumentParser(description="Le défi papier : de l'argent fictif, les vrais cours.")
    parser.add_argument("--verifier", action="store_true", help="les cas calculés à la main, sans réseau")
    args = parser.parse_args()
    if args.verifier:
        sys.exit(0 if verifier() else 1)
    print(json.dumps(etat(), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
