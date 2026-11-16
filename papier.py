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
    (ni pré- ni après-séance), relu au moment de l'ordre, vieux de moins
    de 3 minutes, et l'ordre doit tomber entre l'ouverture et la clôture
    de la séance que donne Yahoo (qui connaît les jours fériés et les
    séances raccourcies ; à défaut, 9 h 30 – 16 h à New York). Aucun
    calendrier à tenir. Les heures sont toujours écrites avec leur
    fuseau : le Mac peut changer d'heure (en voyage), pas le défi.

LANCER
    python3 papier.py              # l'état du défi (lit les cours)
    python3 papier.py --verifier   # les cas calculés à la main, sans réseau
"""

import argparse
import contextlib
import fcntl
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

import pandas as pd
import yfinance as yf

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import ma_position   # noqa: E402  (les 49 valeurs, le prix moyen pondéré du journal réel)

PARIS = ma_position.PARIS
NEW_YORK = ZoneInfo("America/New_York")
UTC = timezone.utc
INDICE = ma_position.INDICE     # SPY : le S&P 500, tel qu'un ETF le suit
CHANGE = ma_position.CHANGE     # EURUSD=X : combien de dollars vaut 1 €
NOMS = {INDICE: "le S&P 500", CHANGE: "le dollar"}

CAPITAL_EUR = 200.0
FRAIS_EUR = 1.0             # par ordre, comme chez le courtier
JOURS = 28
ACHAT_MIN_EUR = 1.0         # et le plus petit reste qu'une vente partielle peut laisser
AGE_MAX_S = 180             # le prix d'un ordre : une minute de séance de moins de 3 minutes
AGE_MAX_CHANGE_S = 600      # le cours du dollar : moins de 10 minutes
AVANCE_TOLEREE_S = 120      # une minute « dans le futur » : les horloges ne sont jamais parfaitement d'accord
MEMO_S = 30                 # l'affichage relit les cours au plus toutes les 30 s ; un ordre, toujours
VERROU_ATTENTE_S = 10.0     # un ordre attend au plus 10 s qu'un autre ordre finisse
PORT_APPLI = "8770"         # le serveur de la vraie appli
AVERTISSEMENT = ("Défi FICTIF : aucun ordre n'est envoyé, aucun euro réel n'est en jeu. "
                 "Des faits sur ton défi, aucun conseil ni prédiction.")
_verrou = threading.Lock()


def nom(symbole):
    """« le S&P 500 » plutôt que « SPY », « le dollar » plutôt que « EURUSD=X »."""
    return NOMS.get(symbole, symbole)


# ══════════════════════════════════════════════════════════════════
#  LE FICHIER DU DÉFI
# ══════════════════════════════════════════════════════════════════

def chemin():
    """
    Le fichier du défi. Un serveur d'essai — journal en copie (variable
    MA_POSITION_JOURNAL) ou port autre que celui de l'appli — doit avoir
    son propre fichier papier (MA_POSITION_PAPIER) : sinon ses ordres de
    test iraient dans le vrai défi, et deux serveurs écriraient le même
    fichier.
    """
    propre = os.environ.get("MA_POSITION_PAPIER")
    if propre:
        return propre
    port = os.environ.get("DASHBOARD_PORT")
    if os.environ.get("MA_POSITION_JOURNAL") or (port and port != PORT_APPLI):
        raise ValueError("Serveur d'essai sans fichier papier à lui (variable MA_POSITION_PAPIER) : "
                         "le défi papier est coupé pour protéger le vrai.")
    return os.path.join(ROOT, "data", "papier.json")


def nouveau_defi():
    return {"version": 1, "capital_eur": CAPITAL_EUR, "frais_ordre_eur": FRAIS_EUR,
            "jours": JOURS, "debut": None, "ordres": []}


def _nombre_fini(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _valider(defi, nom_fichier):
    """La forme d'un défi, vérifiée : un fichier d'une autre forme n'est ni lu ni remplacé."""
    def mauvais():
        return ValueError(f"{nom_fichier} n'a pas la forme d'un défi : il n'est pas touché.")
    if not isinstance(defi, dict) or not isinstance(defi.get("ordres"), list):
        raise mauvais()
    for cle in ("capital_eur", "frais_ordre_eur", "jours"):
        if cle in defi and not _nombre_fini(defi[cle]):
            raise mauvais()
    for o in defi["ordres"]:
        if (not isinstance(o, dict) or o.get("sens") not in ("achat", "vente")
                or not isinstance(o.get("ticker"), str) or not isinstance(o.get("date"), str)
                or not all(_nombre_fini(o.get(c)) for c in ("quantite", "montant_eur", "frais_eur", "prix_eur"))
                or o["quantite"] <= 0):
            raise mauvais()
    d = defi.get("debut")
    if d is not None:
        if (not isinstance(d, dict) or not isinstance(d.get("date"), str)
                or not isinstance(d.get("indice"), dict) or not _nombre_fini(d["indice"].get("quantite"))):
            raise mauvais()
        try:
            datetime.fromisoformat(d["date"])
        except ValueError:
            raise mauvais()
    return defi


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
    return _valider(defi, os.path.basename(fichier))


def _ecrire_defi(fichier, defi):
    """Remplacé d'un coup, par un fichier temporaire au nom unique : jamais un fichier à moitié écrit."""
    dossier = os.path.dirname(os.path.abspath(fichier))
    os.makedirs(dossier, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dossier, prefix=".papier-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(defi, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, fichier)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def _verrou_fichier(fichier):
    """
    Un ordre à la fois, même entre deux serveurs (deux processus) : un
    verrou posé sur « papier.json.verrou » (le verrou de threading, lui,
    ne vaut que dans un processus).
    """
    os.makedirs(os.path.dirname(os.path.abspath(fichier)), exist_ok=True)
    with open(fichier + ".verrou", "a") as f:
        limite = time.monotonic() + VERROU_ATTENTE_S
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > limite:
                    raise ValueError("Un autre ordre est en cours : réessaie dans un instant.")
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


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
    """
    Le jour du défi (1 le jour du premier ordre, 28 le dernier), sa date
    de fin, la dernière séance prévue d'ici là (le dernier jour de semaine,
    sauf jour férié à New York), et s'il est fini.
    """
    if not defi.get("debut"):
        return {"jour": None, "debut_jour": None, "fin": None, "derniere_seance": None, "fini": False}
    debut = jour_paris(datetime.fromisoformat(defi["debut"]["date"]))
    fin = debut + timedelta(days=int(defi.get("jours", JOURS)) - 1)
    derniere = fin - timedelta(days=max(0, fin.weekday() - 4))
    aujourd_hui = jour_paris(maintenant)
    return {"jour": (aujourd_hui - debut).days + 1, "debut_jour": debut.isoformat(),
            "fin": fin.isoformat(), "derniere_seance": derniere.isoformat(), "fini": aujourd_hui > fin}


def seance_du_jour(jour_ny):
    """L'ouverture et la clôture NORMALES d'un jour de New York (9 h 30 – 16 h), à l'heure de Paris."""
    ouverture = datetime(jour_ny.year, jour_ny.month, jour_ny.day, 9, 30, tzinfo=NEW_YORK)
    cloture = datetime(jour_ny.year, jour_ny.month, jour_ny.day, 16, 0, tzinfo=NEW_YORK)
    return ouverture.astimezone(PARIS), cloture.astimezone(PARIS)


def prochaine_seance(maintenant, des_demain=False):
    """
    La prochaine séance normale qui n'est pas finie : aujourd'hui avant
    16 h à New York, sinon le jour de semaine suivant. `des_demain` : pas
    aujourd'hui (la séance du jour a déjà fermé plus tôt).
    """
    jour = maintenant.astimezone(NEW_YORK).date() + timedelta(days=1 if des_demain else 0)
    while True:
        if jour.weekday() < 5:
            ouverture, cloture = seance_du_jour(jour)
            if maintenant < cloture:
                return ouverture, cloture
        jour += timedelta(days=1)


def fraiche(minute, maintenant, age_max=AGE_MAX_S):
    """Une minute de cours assez récente pour servir de prix ?"""
    age = (maintenant - minute).total_seconds()
    return -AVANCE_TOLEREE_S <= age <= age_max


def en_seance(minute, maintenant, periode=None):
    """
    Un ordre est-il possible maintenant ? La minute est fraîche ET on est
    dans la séance : celle que donne Yahoo (jours fériés et séances
    raccourcies compris), sinon 9 h 30 – 16 h à New York, en semaine.
    """
    if not fraiche(minute, maintenant):
        return False
    if periode:
        return periode[0] <= maintenant < periode[1]
    ny = maintenant.astimezone(NEW_YORK)
    ouverture, cloture = seance_du_jour(ny.date())
    return ny.weekday() < 5 and ouverture <= maintenant < cloture


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


def jour_relatif(instant, maintenant):
    """« aujourd'hui », « demain » ou « lundi 23 novembre » (à Paris)."""
    ecart = (jour_paris(instant) - jour_paris(maintenant)).days
    return "aujourd'hui" if ecart == 0 else "demain" if ecart == 1 else date_fr(jour_paris(instant))


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


def message_fermee(maintenant, symbole=None, minute=None, periode=None):
    """Pourquoi un ordre est impossible maintenant, et quand ce sera la prochaine séance (heure de Paris)."""
    def annonce(ouverture, cloture, sur=False):
        quand = f"{jour_relatif(ouverture, maintenant)}, de {heure_fr(ouverture)} à {heure_fr(cloture)}, heure de Paris"
        return f"Prochaine séance : {quand}." if sur else \
            f"Prochaine séance : {quand} (sauf jour férié à New York)."

    ny = maintenant.astimezone(NEW_YORK)
    ouverture, cloture = seance_du_jour(ny.date())
    jour_de_semaine = ny.weekday() < 5
    if periode and periode[0] > maintenant:
        # Yahoo annonce déjà la prochaine séance : jour férié et séance raccourcie compris.
        if jour_de_semaine and ouverture <= maintenant < cloture and jour_paris(periode[0]) != jour_paris(maintenant):
            return f"Pas de séance à New York aujourd'hui. {annonce(*periode, sur=True)}"
        if ny.weekday() >= 5:
            return f"La Bourse de New York est fermée le week-end. {annonce(*periode, sur=True)}"
        debut = "La séance de New York n'a pas commencé" if maintenant < ouverture or \
            jour_paris(periode[0]) == jour_paris(maintenant) else "La séance de New York est finie"
        return f"{debut}. {annonce(*periode, sur=True)}"
    if periode and periode[0] <= maintenant and periode[1] <= maintenant and periode[1] < cloture \
            and jour_paris(periode[1]) == jour_paris(maintenant):
        return (f"La séance de New York a fermé plus tôt aujourd'hui, à {heure_fr(periode[1])} "
                f"(heure de Paris). {annonce(*prochaine_seance(maintenant, des_demain=True))}")
    if not jour_de_semaine:
        return f"La Bourse de New York est fermée le week-end. {annonce(*prochaine_seance(maintenant))}"
    if maintenant < ouverture:
        return f"La séance de New York n'a pas commencé. {annonce(ouverture, cloture)}"
    if maintenant >= cloture:
        return f"La séance de New York est finie. {annonce(*prochaine_seance(maintenant))}"
    pour = f" pour {nom(symbole)}" if symbole else ""
    depuis = f" (le dernier date de {quand_fr(minute, maintenant)})" if minute else ""
    return (f"Pas de cours de séance récent{pour}{depuis} : jour férié à New York, fin d'une "
            "séance raccourcie, ou Yahoo en retard. Ordres impossibles pour l'instant.")


# ══════════════════════════════════════════════════════════════════
#  LES COURS (réseau)
# ══════════════════════════════════════════════════════════════════

_memo = {}   # symbole → (instant de lecture, cours, minute en UTC, séance de Yahoo ou None)


def _seance_de_yahoo(meta):
    """La séance régulière que Yahoo annonce (jours fériés et séances raccourcies compris), ou None."""
    try:
        reg = (meta or {})["currentTradingPeriod"]["regular"]
        debut = datetime.fromtimestamp(int(reg["start"]), UTC)
        fin = datetime.fromtimestamp(int(reg["end"]), UTC)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return (debut, fin) if debut < fin else None


def minute_de_seance(symbole, relire=False):
    """
    La dernière minute de séance NORMALE (ni pré- ni après-séance) :
    (cours en dollars, heure de la minute en UTC, séance de Yahoo). Pour
    EURUSD=X, la dernière minute tout court : le change s'échange jour et
    nuit.
    """
    deja = _memo.get(symbole)
    if not relire and deja and time.monotonic() - deja[0] < MEMO_S:
        return deja[1:]
    t = yf.Ticker(symbole)
    h = t.history(period="5d", interval="1m", prepost=False, auto_adjust=True)
    if h is None or h.empty:
        raise ValueError(f"aucun cours reçu pour {symbole}")
    c = h["Close"].dropna()
    if c.empty:
        raise ValueError(f"aucun cours reçu pour {symbole}")
    cours = float(c.iloc[-1])
    minute = pd.Timestamp(c.index[-1]).tz_convert("UTC").to_pydatetime()
    if not math.isfinite(cours) or cours <= 0:
        raise ValueError(f"cours illisible pour {symbole}")
    periode = _seance_de_yahoo(getattr(t, "history_metadata", None))
    _memo[symbole] = (time.monotonic(), cours, minute, periode)
    return cours, minute, periode


def _trois(r):
    """Un lecteur de cours rend (cours, minute) ou (cours, minute, séance)."""
    return (r[0], r[1], r[2]) if len(r) > 2 else (r[0], r[1], None)


def _cours_d_ordre(lire_cours, symbole, maintenant):
    """Un cours relu pour un ordre : frais et en séance, sinon une phrase qui dit pourquoi."""
    try:
        cours, minute, periode = _trois(lire_cours(symbole, relire=True))
    except Exception as e:     # yfinance lève des erreurs de toutes sortes
        print(f"[papier] cours de {symbole} : {e}", file=sys.stderr)
        raise ValueError(f"Yahoo n'a pas donné le cours de {nom(symbole)} : réessaie dans une minute.")
    if symbole == CHANGE:
        if fraiche(minute, maintenant, AGE_MAX_CHANGE_S):
            return cours, minute
        raise ValueError(f"Le cours du dollar n'est pas à jour (le dernier date de "
                         f"{quand_fr(minute, maintenant)}) : réessaie dans une minute.")
    if en_seance(minute, maintenant, periode):
        return cours, minute
    raise ValueError(message_fermee(maintenant, symbole, minute, periode))


# ══════════════════════════════════════════════════════════════════
#  PASSER UN ORDRE FICTIF
# ══════════════════════════════════════════════════════════════════

def _nombre(v, nom_champ, exemple):
    try:
        x = float(str(v).strip().replace(" ", "").replace(" ", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError(f"{nom_champ} : un nombre, par exemple {exemple}.")
    if not math.isfinite(x):
        raise ValueError(f"{nom_champ} : un nombre, par exemple {exemple}.")
    return x


def lire_demande(payload):
    """Ce que demande le formulaire, vérifié AVANT tout appel réseau."""
    if not isinstance(payload, dict):
        raise ValueError("Demande illisible.")
    sens = str(payload.get("sens", "")).strip()
    if sens not in ("achat", "vente"):
        raise ValueError("Le sens est « achat » ou « vente ».")
    ticker = str(payload.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError("Choisis une valeur.")
    if ticker not in ma_position.engine.UNIVERSE:
        raise ValueError(f"{ticker[:12]} ne fait pas partie des 49 valeurs suivies.")
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
    with _verrou, _verrou_fichier(fichier):
        maintenant = maintenant or datetime.now(UTC)
        defi = lire(fichier)
        cal = calendrier(defi, maintenant)
        if cal["fini"]:
            raise ValueError(f"Le défi est fini depuis le {date_fr(cal['fin'])} : plus d'ordres.")
        etat = rejouer(defi)
        frais = float(defi.get("frais_ordre_eur", FRAIS_EUR))
        ticker = demande["ticker"]

        # Ce qui se vérifie sans les cours.
        tout = False
        if demande["sens"] == "achat":
            if demande["montant_eur"] + frais > etat["liquidites_eur"] + 1e-9:
                reste = etat["liquidites_eur"]
                if reste - frais < ACHAT_MIN_EUR:
                    raise ValueError(f"Il te reste {euros(reste)} : pas assez pour un achat (au moins "
                                     f"{euros(ACHAT_MIN_EUR)}, plus {euros(frais)} de frais).")
                raise ValueError(f"Il te reste {euros(reste)} : avec le {euros(frais)} de frais, "
                                 f"tu peux acheter pour {euros(reste - frais)} au plus.")
        else:
            detenue = next((p for p in etat["positions"] if p["ticker"] == ticker), None)
            if not detenue:
                raise ValueError(f"Tu ne détiens pas {de(ticker)} dans ce défi.")
            tout = bool(demande.get("tout")) or abs(demande["quantite"] - detenue["quantite"]) <= 1e-9
            quantite = detenue["quantite"] if tout else demande["quantite"]
            if quantite > detenue["quantite"] + 1e-9:
                raise ValueError(f"Tu ne détiens que {qte(detenue['quantite'])} {ticker} dans ce défi.")

        # Les cours, relus maintenant : jamais ceux de l'affichage.
        prix_usd, minute = _cours_d_ordre(lire_cours, ticker, maintenant)
        fx, _ = _cours_d_ordre(lire_cours, CHANGE, maintenant)
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
            if tout:
                # Fermer une ligne reste toujours possible, même quand le frais
                # dépasse ce qu'elle rapporte, tant que les liquidités le paient.
                if etat["liquidites_eur"] + montant - frais < -1e-9:
                    raise ValueError(f"Il te reste {euros(etat['liquidites_eur'])} : pas assez pour payer le "
                                     f"{euros(frais)} de frais de cette vente, qui rapporterait {euros(montant)}.")
            else:
                if montant <= frais:
                    raise ValueError(f"Cette vente rapporterait {euros(montant)}, pas plus que son "
                                     f"{euros(frais)} de frais. Pour fermer la ligne, coche « Tout vendre ».")
                reste = (detenue["quantite"] - quantite) * prix_eur
                if reste < ACHAT_MIN_EUR:
                    raise ValueError(f"Il ne resterait que {euros(reste)} {de(ticker)}, moins que le "
                                     f"{euros(frais)} de frais d'une prochaine vente : vends tout, "
                                     "ou une plus petite part.")

        ordre = {"date": maintenant.isoformat(timespec="seconds"), "sens": demande["sens"],
                 "ticker": ticker, "quantite": quantite, "prix_usd": round(prix_usd, 4),
                 "change": round(fx, 6), "prix_eur": prix_eur, "minute": minute.isoformat(),
                 "montant_eur": montant, "frais_eur": frais, "raison": demande["raison"]}
        if debut:
            defi["debut"] = debut
        defi["ordres"].append(ordre)
        _ecrire_defi(fichier, defi)
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
    maintenant = maintenant or datetime.now(UTC)
    defi = lire(fichier)
    r = rejouer(defi)
    cal = calendrier(defi, maintenant)
    capital = float(defi.get("capital_eur", CAPITAL_EUR))
    frais = float(defi.get("frais_ordre_eur", FRAIS_EUR))

    manquants = []

    def cours(symbole):
        try:
            return _trois(lire_cours(symbole))
        except Exception as e:
            print(f"[papier] cours de {symbole} : {e}", file=sys.stderr)
            manquants.append(nom(symbole))
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

    # La séance : la même règle que les ordres, lue sur le S&P 500 ; et un
    # dollar à jour, sans quoi chaque ordre serait refusé.
    if not (spy and fx):
        ouverte = False
        message = ("Cours indisponibles pour l'instant (Yahoo) : ordres impossibles. "
                   "Nouvel essai dans une minute.")
    elif not en_seance(spy[1], maintenant, spy[2]):
        ouverte, message = False, message_fermee(maintenant, INDICE, spy[1], spy[2])
    elif not fraiche(fx[1], maintenant, AGE_MAX_CHANGE_S):
        ouverte, message = False, "Le cours du dollar n'est pas à jour : ordres impossibles pour l'instant."
    else:
        ouverte, message = True, None
    if cal["fini"]:
        ouverts, pourquoi = False, f"Le défi est fini depuis le {date_fr(cal['fin'])} : plus d'ordres."
    else:
        ouverts, pourquoi = ouverte, message

    # L'adversaire (le duel s'affichera avec la partie 2 du plan).
    adversaire = None
    if defi.get("debut") and spy and fx:
        ind = defi["debut"]["indice"]
        valeur = ind["quantite"] * spy[0] / fx[0]
        adversaire = {"ticker": ind.get("ticker", INDICE), "valeur_eur": valeur, "gain_eur": valeur - capital,
                      "prix_depart_usd": ind.get("prix_usd"), "change_depart": ind.get("change")}

    return {
        "maj": maintenant.isoformat(timespec="seconds"),
        "capital_eur": capital, "frais_ordre_eur": frais, "achat_min_eur": ACHAT_MIN_EUR,
        "jours": int(defi.get("jours", JOURS)),
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
    global VERROU_ATTENTE_S, yf
    proche = lambda a, b, tol=1e-6: a is not None and abs(a - b) < tol
    cas = []

    def ok(nom_cas, condition):
        cas.append(bool(condition))
        print(("✓ " if condition else "✗ ") + nom_cas)

    def faux(table, appels=None):
        """Un lecteur de cours inventé : {symbole: (cours en $, minute en UTC[, séance])}."""
        def lire_cours(symbole, relire=False):
            if appels is not None:
                appels.append((symbole, relire))
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
    m = lambda t, s=60: t - timedelta(seconds=s)        # une minute de cours, s secondes avant t
    # 1 € = 1,10 $ ; NVDA à 110 $ = 100 € ; SPY à 660 $ = 600 € ; AAPL à 220 $ = 200 €.
    cours = lambda t, nvda=110.0, **autres: faux({"NVDA": (nvda, m(t)), "EURUSD=X": (1.10, m(t)),
                                                  "SPY": (660.0, m(t)), "AAPL": (220.0, m(t)), **autres})

    with tempfile.TemporaryDirectory() as dossier:
        # Le journal et le carnet du bot pointent vers des copies jetables pendant
        # tout le test : un ordre fictif qui les toucherait se verrait, sans risque.
        reel = (ma_position.JOURNAL, ma_position.CARNET)
        ma_position.JOURNAL = os.path.join(dossier, "journal.json")
        ma_position.CARNET = os.path.join(dossier, "stock_positions.json")
        for chemin_reel, contenu in ((ma_position.JOURNAL, "[]"), (ma_position.CARNET, "{}")):
            with open(chemin_reel, "w") as g:
                g.write(contenu)
        temoins = (empreinte(ma_position.JOURNAL), empreinte(ma_position.CARNET))
        try:
            _verifier_defi(dossier, ok, faux, refus, empreinte, cours, T0, m, proche)
        finally:
            ok("le journal et le carnet du bot n'ont pas bougé",
               (empreinte(ma_position.JOURNAL), empreinte(ma_position.CARNET)) == temoins)
            ma_position.JOURNAL, ma_position.CARNET = reel

    # ── La lecture des cours, avec un faux yfinance : séance normale seulement, une minute, mémoire de 30 s.
    class FauxTicker:
        appels = []

        def __init__(self, symbole):
            self.symbole = symbole
            debut = int(datetime(2026, 11, 27, 14, 30, tzinfo=UTC).timestamp())
            self.history_metadata = {"currentTradingPeriod": {"regular": {"start": debut, "end": debut + 3.5 * 3600}}}

        def history(self, **kw):
            FauxTicker.appels.append(kw)
            index = pd.DatetimeIndex([pd.Timestamp("2026-11-27 12:58", tz="America/New_York"),
                                      pd.Timestamp("2026-11-27 12:59", tz="America/New_York")])
            return pd.DataFrame({"Close": [101.0, 102.5]}, index=index)

    class FauxYf:
        Ticker = FauxTicker

    vrai_yf = yf
    _memo.clear()
    try:
        yf = FauxYf
        c1 = minute_de_seance("NVDA")
        c2 = minute_de_seance("NVDA")
        c3 = minute_de_seance("NVDA", relire=True)
        kw = FauxTicker.appels[0]
        ok("lecture : séance normale seulement (prepost=False), en minutes",
           kw.get("prepost") is False and kw.get("interval") == "1m")
        ok("lecture : la dernière minute (102,5 $ à 17:59 UTC) et la séance raccourcie de Yahoo (fin 18:00 UTC)",
           c1[0] == 102.5 and c1[1] == datetime(2026, 11, 27, 17, 59, tzinfo=UTC)
           and c1[2][1] == datetime(2026, 11, 27, 18, 0, tzinfo=UTC))
        ok("lecture : gardée 30 s pour l'affichage, relue pour un ordre",
           len(FauxTicker.appels) == 2 and c2 == c1 and c3 == c1)
    finally:
        yf = vrai_yf
        _memo.clear()

    # ── Le serveur d'essai ne touche jamais le vrai défi.
    garde = {k: os.environ.get(k) for k in ("MA_POSITION_PAPIER", "MA_POSITION_JOURNAL", "DASHBOARD_PORT")}
    try:
        for k in garde:
            os.environ.pop(k, None)
        ok("par défaut : data/papier.json", chemin() == os.path.join(ROOT, "data", "papier.json"))
        os.environ["DASHBOARD_PORT"] = PORT_APPLI
        ok("le port de l'appli (8770) : data/papier.json", chemin() == os.path.join(ROOT, "data", "papier.json"))
        os.environ["DASHBOARD_PORT"] = "8779"
        r_ok, _ = refus(chemin, "MA_POSITION_PAPIER")
        ok("un autre port SANS fichier papier à lui : refusé", r_ok)
        os.environ.pop("DASHBOARD_PORT")
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


def _verifier_defi(dossier, ok, faux, refus, empreinte, cours, T0, m, proche):
    global VERROU_ATTENTE_S
    f = os.path.join(dossier, "papier.json")
    ordre = lambda payload, t=T0, lc=None, fichier=f: passer_ordre(payload, maintenant=t,
                                                                  lire_cours=lc or cours(t), fichier=fichier)
    achat = lambda montant, ticker="NVDA", raison="Essai": {"sens": "achat", "ticker": ticker,
                                                            "montant_eur": montant, "raison": raison}
    vente = lambda q, ticker="NVDA": {"sens": "vente", "ticker": ticker, "quantite": q, "raison": "Essai"}
    tout = lambda ticker="NVDA": {"sens": "vente", "ticker": ticker, "tout": True, "raison": "Fin"}

    # ── Avant le premier ordre : rien n'est écrit, le défi n'a pas commencé.
    e0 = etat(maintenant=T0, lire_cours=cours(T0), fichier=f)
    ok("défi neuf : 200 € de liquidités, pas commencé, aucun fichier écrit",
       proche(e0["liquidites_eur"], 200) and e0["jour"] is None and empreinte(f) is None
       and proche(e0["total_eur"], 200) and e0["ordres_ouverts"])

    # ── Les refus qui ne demandent aucun cours.
    for nom_cas, payload, mots in [
        ("raison vide", {**achat(50), "raison": "   "}, ("raison est obligatoire",)),
        ("valeur hors des 49", achat(50, "ZZZZ"), ("ZZZZ", "49 valeurs")),
        ("aucune valeur choisie", achat(50, ""), ("Choisis une valeur",)),
        ("montant de 0,50 €", achat(0.5), ("au moins",)),
        ("montant illisible", achat("abc"), ("Le montant", "un nombre")),
        ("sens inconnu", {**achat(50), "sens": "short"}, ("achat", "vente")),
        ("vente d'une valeur pas détenue", vente(0.1, "AAPL"), ("ne détiens pas d'AAPL",)),
        ("le défi commence par un achat", vente(0.1), ("ne détiens pas de NVDA",)),
        ("quantité nulle", vente(0), ("plus grande que 0",)),
    ]:
        r_ok, texte = refus(lambda: ordre(payload), *mots)
        ok(f"refusé : {nom_cas} → « {texte} »", r_ok and empreinte(f) is None)

    # ── Les refus de séance : week-end, avant, après, jour férié, séance raccourcie.
    UTC_ = UTC
    samedi = datetime(2026, 11, 21, 15, 0, tzinfo=UTC_)
    vendredi_cloture = datetime(2026, 11, 20, 20, 59, tzinfo=UTC_)          # 15 h 59 à New York
    ferme = lambda t, derniere, periode=None: faux({"NVDA": (110.0, derniere, periode), "EURUSD=X": (1.10, m(t)),
                                                    "SPY": (660.0, derniere, periode)})
    vendredi_court = (datetime(2026, 11, 27, 14, 30, tzinfo=UTC_), datetime(2026, 11, 27, 18, 0, tzinfo=UTC_))
    for nom_cas, t, derniere, periode, mots in [
        ("samedi", samedi, vendredi_cloture, None,
         ("week-end", "Prochaine séance : lundi 23 novembre, de 15 h 30 à 22 h", "sauf jour férié")),
        ("lundi 9 h à Paris, avant l'ouverture", datetime(2026, 11, 23, 8, 0, tzinfo=UTC_), vendredi_cloture, None,
         ("n'a pas commencé", "aujourd'hui, de 15 h 30 à 22 h")),
        ("lundi 22 h 01 min 30, la minute de 21 h 59 encore fraîche : après la clôture",
         datetime(2026, 11, 23, 21, 1, 30, tzinfo=UTC_), datetime(2026, 11, 23, 20, 59, tzinfo=UTC_), None,
         ("est finie", "demain, de 15 h 30 à 22 h")),
        ("Thanksgiving (jeudi 26/11, 17 h à Paris), sans la séance de Yahoo",
         datetime(2026, 11, 26, 16, 0, tzinfo=UTC_), datetime(2026, 11, 25, 20, 59, tzinfo=UTC_), None,
         ("Pas de cours de séance récent pour NVDA", "mercredi 25 novembre à 21:59", "jour férié")),
        ("Thanksgiving, avec la séance de Yahoo (vendredi 27/11, 15 h 30 – 19 h)",
         datetime(2026, 11, 26, 16, 0, tzinfo=UTC_), datetime(2026, 11, 25, 20, 59, tzinfo=UTC_), vendredi_court,
         ("Pas de séance à New York aujourd'hui", "demain, de 15 h 30 à 19 h, heure de Paris.")),
        ("vendredi 27/11 à 19 h 01 min 30 : la séance raccourcie de Yahoo a fermé à 19 h",
         datetime(2026, 11, 27, 18, 1, 30, tzinfo=UTC_), datetime(2026, 11, 27, 17, 59, tzinfo=UTC_), vendredi_court,
         ("a fermé plus tôt aujourd'hui, à 19 h", "lundi 30 novembre")),
    ]:
        r_ok, texte = refus(lambda: ordre(achat(50), t, ferme(t, derniere, periode)), *mots)
        ok(f"refusé : {nom_cas} → « {texte} »", r_ok and empreinte(f) is None)
    ok("samedi 31/10 : Paris est passé à l'heure d'hiver, pas New York ; lundi 2/11, de 15 h 30 à 22 h",
       "lundi 2 novembre, de 15 h 30 à 22 h" in message_fermee(datetime(2026, 10, 31, 12, 0, tzinfo=UTC_)))
    ok("samedi 13/03/2027 : New York passe à l'heure d'été avant Paris ; lundi 15/3, de 14 h 30 à 21 h",
       "lundi 15 mars, de 14 h 30 à 21 h" in message_fermee(datetime(2027, 3, 13, 12, 0, tzinfo=UTC_)))
    ok("samedi 0 h 30 à Paris, c'est encore vendredi soir à New York : « finie », pas « week-end »",
       "est finie" in message_fermee(datetime(2026, 11, 20, 23, 30, tzinfo=UTC_)))

    r_ok, texte = refus(lambda: ordre(achat(50), T0, faux({"NVDA": (110.0, m(T0)), "EURUSD=X": (1.10, m(T0, 1200)),
                                                          "SPY": (660.0, m(T0))})), "dollar")
    ok(f"refusé : cours du dollar vieux de 20 min → « {texte} »", r_ok and empreinte(f) is None)
    r_ok, texte = refus(lambda: ordre(achat(50), T0, faux({"NVDA": (110.0, m(T0)), "EURUSD=X": (1.10, m(T0)),
                                                          "SPY": (660.0, m(T0, 600))})), "équitablement")
    ok(f"refusé : premier ordre sans cours frais du S&P 500 → « {texte} »", r_ok and empreinte(f) is None)
    r_ok, texte = refus(lambda: ordre(achat(50), T0, faux({"EURUSD=X": (1.10, m(T0)), "SPY": (660.0, m(T0))})),
                        "Yahoo", "NVDA")
    ok(f"refusé : Yahoo ne répond pas → « {texte} »", r_ok and empreinte(f) is None)
    r_ok, _ = refus(lambda: ordre(achat(50), T0, cours(T0, NVDA=(110.0, m(T0, 181)))))
    ok("refusé : une minute de 3 min 1 s (acceptée à 2 min 59, plus bas)", r_ok and empreinte(f) is None)
    r_ok, _ = refus(lambda: ordre(achat(50), T0, cours(T0, NVDA=(110.0, T0 + timedelta(minutes=10)))))
    ok("refusé : une minute 10 min « dans le futur » (une horloge déréglée)", r_ok and empreinte(f) is None)

    # ── Le verrou entre processus : tenu ailleurs, l'ordre attend puis renonce, sans rien écrire.
    VERROU_ATTENTE_S, garde_attente = 0.3, VERROU_ATTENTE_S
    try:
        with open(f + ".verrou", "a") as tenu:
            fcntl.flock(tenu.fileno(), fcntl.LOCK_EX)
            r_ok, texte = refus(lambda: ordre(achat(50)), "autre ordre est en cours")
            fcntl.flock(tenu.fileno(), fcntl.LOCK_UN)
        ok(f"refusé : un autre processus tient le verrou → « {texte} »", r_ok and empreinte(f) is None)
    finally:
        VERROU_ATTENTE_S = garde_attente

    # ── 1. Le premier ordre : 50 € de NVDA à 100 € → 0,5 action, et le défi commence.
    appels = []
    o1 = passer_ordre(achat(50, raison="  Premier   essai  " + "x" * 300), maintenant=T0,
                      lire_cours=faux({"NVDA": (110.0, m(T0, 179)), "EURUSD=X": (1.10, m(T0, 300)),
                                       "SPY": (660.0, m(T0))}, appels), fichier=f)
    d = lire(f)
    ok("1er ordre : 0,5 NVDA à 100 € (110 $ au change de 1,10), 50 € + 1 € de frais ; dollar de 5 min accepté",
       proche(o1["quantite"], 0.5) and proche(o1["prix_eur"], 100) and o1["montant_eur"] == 50
       and o1["frais_eur"] == 1)
    ok("la raison : espaces resserrés, 200 caractères au plus",
       o1["raison"].startswith("Premier essai x") and len(o1["raison"]) == 200)
    ok("un ordre RELIT ses trois cours (NVDA, dollar, S&P 500), jamais la mémoire de l'affichage",
       sorted(appels) == [("EURUSD=X", True), ("NVDA", True), ("SPY", True)])
    ok("le défi commence au premier ordre ; l'adversaire met 199 € dans SPY à 600 € (0,331667 part)",
       d["debut"]["date"] == T0.isoformat() and proche(d["debut"]["indice"]["quantite"], 199 / 600)
       and d["debut"]["indice"]["montant_eur"] == 199)
    e1 = etat(maintenant=T0, lire_cours=cours(T0), fichier=f)
    l1 = e1["positions"][0]
    ok("état : 149 € de liquidités, 0,5 NVDA valant 50 €, total 199 €, gain −1 € (le frais)",
       proche(e1["liquidites_eur"], 149) and proche(l1["valeur_eur"], 50)
       and proche(e1["total_eur"], 199) and proche(e1["gain_eur"], -1) and e1["jour"] == 1)
    ok("la ligne : gain frais compris −1 € ; prix moyen sans les frais 100 €",
       proche(l1["gain_eur"], -1) and proche(l1["prix_moyen_eur"], 100))
    ok("frais payés : 1 €, soit 0,5 % du capital", proche(e1["frais_payes_eur"], 1)
       and proche(e1["frais_payes_pct"], 0.005))
    bouge = faux({"NVDA": (110.0, m(T0)), "EURUSD=X": (1.05, m(T0)), "SPY": (693.0, m(T0))})
    ok("l'adversaire suit le S&P 500 ET le dollar : SPY +5 %, 1 € = 1,05 $ → 199/600 × 660 = 218,90 €",
       proche(etat(maintenant=T0, lire_cours=bouge, fichier=f)["adversaire"]["valeur_eur"], 218.9, 1e-3))
    ok("le jour se compte à Paris : 0 h 30 le mardi à Paris (23 h 30 UTC lundi) → jour 2",
       etat(maintenant=datetime(2026, 11, 23, 23, 30, tzinfo=UTC), lire_cours=cours(T0), fichier=f)["jour"] == 2)

    avant = empreinte(f)
    r_ok, texte = refus(lambda: ordre(achat(149)), "149,00", "148,00")
    ok(f"refusé : 149 € quand il en reste 149 (le frais en plus) → « {texte} »", r_ok and empreinte(f) == avant)
    r_ok, texte = refus(lambda: ordre(vente(0.6)), "que 0,5 NVDA")
    ok(f"refusé : vendre 0,6 quand on a 0,5 → « {texte} »", r_ok and empreinte(f) == avant)

    # ── 2. Un second achat plus cher : 30 € de NVDA à 120 € (132 $) → 0,25 action ;
    #       sa minute a 1 min d'avance sur l'horloge (tolérée).
    t2 = T0 + timedelta(hours=1)
    passer_ordre(achat(30), maintenant=t2, lire_cours=cours(t2, NVDA=(132.0, t2 + timedelta(minutes=1))), fichier=f)
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
    avant = empreinte(f)
    r_ok, texte = refus(lambda: ordre(vente(0.745), t3, cours(t3, nvda=121.0)), "ne resterait que 0,55", "vends tout")
    ok(f"refusé : une vente qui laisserait une miette (0,005 action, 0,55 €) → « {texte} »",
       r_ok and empreinte(f) == avant)
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
    t4 = t3 + timedelta(minutes=5)
    passer_ordre(tout(), maintenant=t4, lire_cours=cours(t4, nvda=121.0), fichier=f)
    r4 = rejouer(lire(f))
    ok("tout vendre : plus de position, 198,50 € (+2,50 € de cours, −4 € de frais)",
       r4["positions"] == [] and proche(r4["liquidites_eur"], 198.5) and proche(r4["frais_payes_eur"], 4)
       and proche(sum(x["plus_value_eur"] for x in r4["realisees"]), -1.5))

    # ── 5. Une petite ligne : 1 € d'achat (0,01 action). La moitié ne couvre pas son
    #       frais (refusée) ; « tout » la ferme quand même (1 € reçu, 1 € de frais).
    t5 = T0 + timedelta(hours=3)
    passer_ordre(achat(1), maintenant=t5, lire_cours=cours(t5), fichier=f)
    avant = empreinte(f)
    r_ok, texte = refus(lambda: ordre(vente(0.005), t5, cours(t5)), "0,50", "pas plus que", "Tout vendre")
    ok(f"refusé : une vente partielle de 0,50 € → « {texte} »", r_ok and empreinte(f) == avant)
    passer_ordre(tout(), maintenant=t5, lire_cours=cours(t5), fichier=f)
    r5 = rejouer(lire(f))
    ok("« Tout vendre » ferme une ligne d'1 € : 1 € reçu, 1 € de frais, 196,50 € de liquidités",
       r5["positions"] == [] and proche(r5["liquidites_eur"], 196.5))
    # Une action à 2 000 € : 3 € en achètent 0,0015 ; en vendre 0,0007 (1,40 €) laisse
    # 0,0008 action (1,60 €), une ligne de moins de 0,001 action qui doit rester.
    passer_ordre(achat(3), maintenant=t5, lire_cours=cours(t5, nvda=2200.0), fichier=f)
    passer_ordre(vente(0.0007), maintenant=t5, lire_cours=cours(t5, nvda=2200.0), fichier=f)
    ok("une action à 2 000 € : après une vente, la ligne de 0,0008 action est bien gardée",
       proche(rejouer(lire(f))["positions"][0]["quantite"], 0.0008))
    passer_ordre(tout(), maintenant=t5, lire_cours=cours(t5, nvda=2200.0), fichier=f)

    # ── 6. Le calendrier : jour 1 le jour du premier ordre, jour 28 le dernier.
    jour28 = datetime(2026, 12, 20, 15, 0, tzinfo=UTC)                     # dimanche
    jour29 = datetime(2026, 12, 21, 15, 0, tzinfo=UTC)                     # lundi
    c28, c29 = calendrier(lire(f), jour28), calendrier(lire(f), jour29)
    ok("commencé le lundi 23/11 : le jour 28 est le dimanche 20/12 (dernière séance vendredi 18/12), fini le 21/12",
       c28["jour"] == 28 and not c28["fini"] and c28["fin"] == "2026-12-20"
       and c28["derniere_seance"] == "2026-12-18" and c29["fini"])
    dernier = datetime(2026, 12, 18, 15, 0, tzinfo=UTC)                    # vendredi, jour 26
    passer_ordre(achat(2), maintenant=dernier, lire_cours=cours(dernier), fichier=f)
    avant = empreinte(f)
    r_ok, texte = refus(lambda: ordre(achat(2), jour29, cours(jour29)), "fini", "dimanche 20 décembre")
    ok(f"refusé : le jour 29 → « {texte} »", r_ok and empreinte(f) == avant)
    ok("état d'un défi fini : ordres fermés, avec la raison",
       not etat(maintenant=jour29, lire_cours=cours(jour29), fichier=f)["ordres_ouverts"])

    # ── 7. L'affichage quand Yahoo manque un cours, hors séance, ou avec un dollar périmé.
    trou = faux({"EURUSD=X": (1.10, m(T0)), "SPY": (660.0, m(T0))})
    e7 = etat(maintenant=T0, lire_cours=trou, fichier=f)
    ok("un cours manquant : la ligne sans valeur, pas de total inventé, le reste affiché",
       e7["cours_manquants"] == ["NVDA"] and e7["total_eur"] is None
       and e7["positions"][0]["valeur_eur"] is None and proche(e7["liquidites_eur"], 190.5))
    e8 = etat(maintenant=samedi, lire_cours=ferme(samedi, vendredi_cloture), fichier=f)
    ok("hors séance : ordres fermés, la raison dite, le total compté à la clôture",
       not e8["ordres_ouverts"] and "week-end" in e8["ordres_message"] and e8["total_eur"] is not None)
    vieux_dollar = faux({"NVDA": (110.0, m(T0)), "EURUSD=X": (1.10, m(T0, 1200)), "SPY": (660.0, m(T0))})
    e9 = etat(maintenant=T0, lire_cours=vieux_dollar, fichier=f)
    ok("dollar vieux de 20 min : la page dit « fermé », comme les ordres",
       not e9["ordres_ouverts"] and "dollar" in e9["ordres_message"])
    sans_spy = etat(maintenant=T0, lire_cours=faux({"NVDA": (110.0, m(T0)), "EURUSD=X": (1.10, m(T0))}), fichier=f)
    ok("S&P 500 absent : son nom en clair, pas « SPY »", sans_spy["cours_manquants"] == ["le S&P 500"])

    # ── 8. Une ligne qui ne rapporte pas son frais, avec des liquidités à zéro : « tout » refusé.
    f2 = os.path.join(dossier, "papier-2.json")
    passer_ordre(achat(197), maintenant=T0, lire_cours=cours(T0), fichier=f2)
    passer_ordre(achat(1, "AAPL"), maintenant=T0, lire_cours=cours(T0), fichier=f2)
    avant = empreinte(f2)
    r_ok, texte = refus(lambda: ordre(tout("AAPL"), T0, cours(T0, AAPL=(66.0, m(T0))), f2), "pas assez pour payer")
    ok(f"refusé : fermer une ligne de 0,30 € sans liquidités pour le frais → « {texte} »",
       r_ok and empreinte(f2) == avant)

    # ── 9. Deux ordres en même temps (deux fils) : un seul passe, l'autre voit les liquidités dépensées.
    f3 = os.path.join(dossier, "papier-3.json")
    lent = cours(T0)

    def lire_lent(symbole, relire=False):
        if symbole == "NVDA":
            time.sleep(0.2)
        return lent(symbole, relire)
    resultats = []

    def un_ordre():
        try:
            passer_ordre(achat(150), maintenant=T0, lire_cours=lire_lent, fichier=f3)
            resultats.append("accepté")
        except ValueError:
            resultats.append("refusé")
    fils = [threading.Thread(target=un_ordre) for _ in range(2)]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join()
    ok("deux achats de 150 € en même temps sur 200 € : un accepté, un refusé, un seul écrit",
       sorted(resultats) == ["accepté", "refusé"] and len(lire(f3)["ordres"]) == 1)

    # ── 10. Une séance raccourcie : on achète à 18 h 30, pas à 19 h 01.
    f4 = os.path.join(dossier, "papier-4.json")
    court = (datetime(2026, 11, 27, 14, 30, tzinfo=UTC), datetime(2026, 11, 27, 18, 0, tzinfo=UTC))
    t_court = datetime(2026, 11, 27, 17, 30, tzinfo=UTC)
    passer_ordre(achat(10), maintenant=t_court,
                 lire_cours=faux({"NVDA": (110.0, m(t_court), court), "EURUSD=X": (1.10, m(t_court)),
                                  "SPY": (660.0, m(t_court), court)}), fichier=f4)
    ok("séance raccourcie : un ordre à 18 h 30 (heure de Paris) passe", len(lire(f4)["ordres"]) == 1)

    # ── 11. Rien d'autre n'est écrit que les fichiers du défi et leur verrou.
    restes = sorted(n for n in os.listdir(dossier) if n not in ("journal.json", "stock_positions.json"))
    ok("le dossier ne contient que les défis et leurs verrous (aucun fichier temporaire oublié)",
       restes == ["papier-2.json", "papier-2.json.verrou", "papier-3.json", "papier-3.json.verrou",
                  "papier-4.json", "papier-4.json.verrou", "papier.json", "papier.json.verrou"])

    # ── 12. Un fichier abîmé, ou d'une autre forme, n'est jamais remplacé.
    for contenu, mots in [("{abîmé", ("illisible",)), ('{"ordres": [{"sens": "achat"}]}', ("pas la forme",)),
                          ("[]", ("pas la forme",))]:
        with open(f, "w") as g:
            g.write(contenu)
        avant = empreinte(f)
        r_ok, texte = refus(lambda: ordre(achat(5)), *mots)
        r_ok2, _ = refus(lambda: etat(maintenant=T0, lire_cours=cours(T0), fichier=f), *mots)
        ok(f"refusé : fichier {contenu[:22]!r}, laissé tel quel → « {texte} »",
           r_ok and r_ok2 and empreinte(f) == avant)


def main():
    parser = argparse.ArgumentParser(description="Le défi papier : de l'argent fictif, les vrais cours.")
    parser.add_argument("--verifier", action="store_true", help="les cas calculés à la main, sans réseau")
    args = parser.parse_args()
    if args.verifier:
        sys.exit(0 if verifier() else 1)
    print(json.dumps(etat(), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
