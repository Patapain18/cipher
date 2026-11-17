"""
Ma position — les chiffres de mon argent, en direct.

CE QUE ÇA CALCULE
    Pour chaque valeur détenue : sa valeur en dollars et en euros, le gain
    ou la perte frais compris, et les prix qui remboursent les frais.
    Le journal des décisions : chaque achat ou vente avec sa raison, et ce
    que la même somme serait devenue dans le S&P 500 (SPY) au même moment.
    Le graphique du cours, et les indicateurs techniques, marqués « ne
    prédit rien » : le projet l'a mesuré sur 10 ans.

CE QUE ÇA NE FAIT PAS
    Aucun ordre, aucune clé d'API, aucun conseil, aucune prédiction.

D'OÙ VIENNENT LES CHIFFRES
    Le journal (data/journal.json) est la vérité sur mon argent : les
    montants en euros que le courtier a vraiment débités. Les cours
    viennent de Yahoo Finance (yfinance), le dollar d'EURUSD=X. Le carnet
    du bot (data/stock_positions.json) suit le journal, en dollars.

LANCER
    python3 ma_position.py               # la position et le journal
    python3 ma_position.py --verifier    # les cas calculés à la main
"""

import argparse
import json
import math
import os
import sys
import threading
import time
import warnings
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")

import stock_engine as engine   # noqa: E402

# Un serveur d'essai peut pointer vers des copies (data/essai-*/) :
# les pistes s'y testent sans jamais toucher le vrai journal.
JOURNAL = os.environ.get("MA_POSITION_JOURNAL") or os.path.join(ROOT, "data", "journal.json")
CARNET = os.environ.get("MA_POSITION_CARNET") or os.path.join(ROOT, "data", "stock_positions.json")
PARIS = ZoneInfo("Europe/Paris")
CHANGE = "EURUSD=X"
INDICE = "SPY"            # le S&P 500, tel qu'un ETF le suit
FRAIS_VENTE = 1.0         # € : ce que coûterait une vente chez le courtier (1 € par ordre)
# Les valeurs détenues HORS des 49 du bot (ajoutées le 16/11/2026). Le
# journal les note et le carnet de bord les suit comme les autres (cours,
# gain, duel face au S&P 500). Le bot ne
# les classe pas, et son carnet en dollars (data/stock_positions.json)
# ne les reçoit pas : le bot, le point de la semaine et leurs mesures
# restent sur les 49.
HORS_DU_BOT = ("TSLA",)
AVERTISSEMENT = ("Des faits sur ton argent. Aucune prédiction, "
                 "aucun conseil d'achat ou de vente.")
AVERTISSEMENT_INDICATEURS = ("Le score technique du bot, qui combine ces indicateurs, a été "
                             "mesuré sur 10 ans et 49 valeurs : il ne prédit rien.")
_verrou = threading.Lock()


# ══════════════════════════════════════════════════════════════════
#  LES CALCULS (purs : aucun réseau, vérifiés à la main par --verifier)
# ══════════════════════════════════════════════════════════════════

def en_utc(date_iso):
    """Une date écrite à l'heure de Paris (« 2026-03-02T15:45:00 ») → en UTC."""
    d = datetime.fromisoformat(date_iso)
    if d.tzinfo is None:
        d = d.replace(tzinfo=PARIS)
    return d.astimezone(timezone.utc)


def maintenant_paris():
    """« 2026-11-17T16:45:00 » : l'heure de Paris, sans fuseau, comme le journal (le Mac peut être au Japon)."""
    return datetime.now(PARIS).replace(tzinfo=None).isoformat(timespec="seconds")


def ordre(d):
    """
    L'ordre du journal : l'heure réelle (en UTC, quel que soit le format de
    la date), puis, à la même heure, les ventes avant les achats. L'argent
    d'une vente peut payer un achat de la même minute, jamais l'inverse.
    Avec un simple tri sur le texte de la date, l'ordre de saisie changeait
    le bilan (vu par la vérification indépendante du 17/11/2026).
    """
    return (en_utc(d["date"]), 0 if d["sens"] == "vente" else 1)


def seance_du_cours(heure_utc):
    """D'où vient un cours, d'après l'heure de New York : « avant » (avant 9 h 30), « ouverte », « apres » (16 h et plus).
    Une séance raccourcie (13 h) n'est pas connue ici : son après-séance s'écrit « ouverte » jusqu'à 16 h."""
    ny = heure_utc.astimezone(ZoneInfo("America/New_York"))
    minutes = ny.hour * 60 + ny.minute
    return "avant" if minutes < 9 * 60 + 30 else "apres" if minutes >= 16 * 60 else "ouverte"


def agreger(journal):
    """
    Les lignes détenues, à partir du journal.

    Le prix de revient suit la règle française du prix moyen pondéré : une
    vente retire sa part du coût, au prorata des actions vendues. Le coût
    compte les frais d'achat, puisque c'est l'argent vraiment sorti.
    """
    lignes = {}
    for d in sorted(journal, key=ordre):
        l = lignes.setdefault(d["ticker"], {"ticker": d["ticker"], "quantite": 0.0,
                                            "cout_eur": 0.0, "montant_eur": 0.0})
        if d["sens"] == "achat":
            l["quantite"] += d["quantite"]
            l["cout_eur"] += d["montant_eur"] + d["frais_eur"]
            l["montant_eur"] += d["montant_eur"]
        else:
            part = d["quantite"] / l["quantite"] if l["quantite"] else 0.0
            l["cout_eur"] -= l["cout_eur"] * part
            l["montant_eur"] -= l["montant_eur"] * part
            l["quantite"] -= d["quantite"]
    return [l for l in lignes.values() if l["quantite"] > 1e-9]


def _ventes_impossibles(journal):
    """Les ventes qui vendent plus que ce qui était détenu À LEUR DATE, dans l'ordre du journal."""
    detenu, fautives = {}, []
    for d in sorted(journal, key=ordre):
        q = detenu.get(d["ticker"], 0.0)
        if d["sens"] == "achat":
            detenu[d["ticker"]] = q + d["quantite"]
        elif d["quantite"] > q + 1e-9:
            fautives.append(d)
        else:
            detenu[d["ticker"]] = q - d["quantite"]
    return fautives


def flux(journal):
    """
    L'argent qui a vraiment bougé, dans l'ordre du journal (choix de Mathis
    du 17/11/2026 : le chiffre du haut de « Ta position » garde le résultat
    des ventes, et une action offerte compte à 0 €).

    - versé : ce qui est sorti de ta poche. L'argent d'une vente paie
      d'abord les achats suivants ; seul ce qui manque est versé. Un achat
      noté « offert » ne coûte rien ;
    - en caisse : l'argent des ventes pas encore réinvesti ;
    - ventes : le résultat des ventes, l'argent reçu moins la part du coût
      des actions vendues (prix moyen pondéré, comme `agreger`) ;
    - offert : la valeur des actions offertes, au jour du cadeau.

    Chaque montant existe frais compris et sans les frais. Le gain total
    vaut alors : la valeur des actions + l'argent en caisse − le versé,
    c'est-à-dire le gain des actions détenues + les ventes + l'offert.
    """
    verse = verse_sf = caisse = caisse_sf = offert = ventes = ventes_sf = 0.0
    lignes, nombre_ventes = {}, 0
    for d in sorted(journal, key=ordre):
        l = lignes.setdefault(d["ticker"], {"quantite": 0.0, "cout": 0.0, "montant": 0.0})
        if d["sens"] == "achat":
            l["quantite"] += d["quantite"]
            l["cout"] += d["montant_eur"] + d["frais_eur"]
            l["montant"] += d["montant_eur"]
            if d.get("offert"):
                offert += d["montant_eur"]
                continue
            prix, prix_sf = d["montant_eur"] + d["frais_eur"], d["montant_eur"]
            verse += max(0.0, prix - caisse)
            caisse = max(0.0, caisse - prix)
            verse_sf += max(0.0, prix_sf - caisse_sf)
            caisse_sf = max(0.0, caisse_sf - prix_sf)
        else:
            nombre_ventes += 1
            part = min(1.0, d["quantite"] / l["quantite"]) if l["quantite"] else 0.0
            ventes += d["montant_eur"] - d["frais_eur"] - l["cout"] * part
            ventes_sf += d["montant_eur"] - l["montant"] * part
            l["cout"] -= l["cout"] * part
            l["montant"] -= l["montant"] * part
            l["quantite"] -= d["quantite"]
            caisse += d["montant_eur"] - d["frais_eur"]
            caisse_sf += d["montant_eur"]
            if caisse < 0:      # une vente plus petite que ses frais : le reste est sorti de ta poche
                verse, caisse = verse - caisse, 0.0
    return {"verse_eur": verse, "verse_sans_frais_eur": verse_sf,
            "encaisse_eur": caisse, "encaisse_sans_frais_eur": caisse_sf,
            "ventes_eur": ventes, "ventes_sans_frais_eur": ventes_sf,
            "offert_eur": offert, "nombre_ventes": nombre_ventes}


def calcul_ligne(quantite, cout_eur, montant_eur, prix_usd, eurusd):
    """Ce que vaut une ligne, et ce qu'il faut pour rembourser les frais."""
    prix_eur = prix_usd / eurusd
    valeur_eur = quantite * prix_eur
    prix_achat_eur = montant_eur / quantite
    return {
        "prix_usd": prix_usd,
        "prix_eur": prix_eur,
        "valeur_usd": quantite * prix_usd,
        "valeur_eur": valeur_eur,
        "gain_eur": valeur_eur - cout_eur,
        "gain_pct": valeur_eur / cout_eur - 1,
        "prix_achat_eur": prix_achat_eur,
        "variation_prix_pct": prix_eur / prix_achat_eur - 1,
        "seuil_achat_eur": cout_eur / quantite,
        "seuil_vente_eur": (cout_eur + FRAIS_VENTE) / quantite,
    }


def calcul_comparaison(montant_eur, indice_avant, indice_maintenant, fx_avant, fx_maintenant):
    """
    Ce qu'aurait donné la même somme dans le S&P 500 au même moment.

    Même montant, mêmes frais : la comparaison ne mesure que le choix de
    l'action. Le dollar compte des deux côtés, puisque l'indice est en
    dollars aussi.
    """
    return montant_eur * (indice_maintenant / indice_avant) * (fx_avant / fx_maintenant)


def moyenne_mobile(cours, jours):
    return cours.rolling(jours).mean()


def rsi(cours, jours=14):
    """
    Le RSI de Wilder : la part des hausses dans les mouvements récents,
    de 0 à 100. Au-dessus de 70 on dit « suracheté », sous 30 « survendu ».
    """
    delta = cours.diff()
    hausses = delta.clip(lower=0).ewm(alpha=1 / jours, adjust=False).mean()
    baisses = (-delta.clip(upper=0)).ewm(alpha=1 / jours, adjust=False).mean()
    rs = hausses / baisses
    return (100 - 100 / (1 + rs)).where(baisses > 0, 100.0)


def macd(cours, court=12, long=26, signal=9):
    """L'écart entre deux moyennes exponentielles, et sa propre moyenne (le signal)."""
    ligne = cours.ewm(span=court, adjust=False).mean() - cours.ewm(span=long, adjust=False).mean()
    return ligne, ligne.ewm(span=signal, adjust=False).mean()


# ══════════════════════════════════════════════════════════════════
#  LES COURS (réseau)
# ══════════════════════════════════════════════════════════════════

def _historique(symbole, **kwargs):
    h = yf.Ticker(symbole).history(auto_adjust=True, **kwargs)
    if h is None or h.empty:
        raise ValueError(f"aucun cours reçu pour {symbole}")
    h.index = pd.DatetimeIndex(h.index).tz_convert("UTC")
    return h["Close"].dropna()


# Les cours déjà lus, pour que la position et le journal, demandés au
# même moment par la page, comptent avec LE MÊME cours (sinon l'écran
# montre 12,62 € en haut et 12,60 € dans le journal).
_memo_actuel = {}     # symbole → (instant de lecture, cours, heure du cours)
_memo_passe = {}      # (symbole, moment) → cours : un cours passé ne change plus
_memo_repli = {}      # (symbole, moment) → (instant de lecture, clôture du jour) : un repli, redemandé
MEMO_ACTUEL_S = 30
REPLI_S = 600         # toutes les 10 minutes, pour reprendre la vraie barre dès que Yahoo la rend


def cours_actuel(symbole):
    """Le dernier cours connu et son heure : le marché peut être fermé."""
    deja = _memo_actuel.get(symbole)
    if deja and time.monotonic() - deja[0] < MEMO_ACTUEL_S:
        return deja[1], deja[2]
    c = _historique(symbole, period="5d", interval="1m", prepost=True)
    cours, heure = float(c.iloc[-1]), c.index[-1]
    _memo_actuel[symbole] = (time.monotonic(), cours, heure)
    return cours, heure


# Les cours passés lus en barres de 5 minutes sont aussi gardés sur disque,
# à côté du journal (data/cours_passes.json ; data/essai/… pour l'essai) :
# Yahoo n'a plus ces barres après 60 jours, et sa clôture journalière de
# secours est ajustée des dividendes. Sans eux, le duel d'un achat
# bougerait tout seul à chaque lancement (vu par la relecture du 17/11).
_disque_passe = {"chemin": None, "valeurs": {}, "lisible": True}
_verrou_passe = threading.Lock()


def _chemin_cours_passes():
    return os.path.join(os.path.dirname(os.path.abspath(JOURNAL)), "cours_passes.json")


def _cours_gardes():
    """Les cours passés gardés sur disque, lus une fois par fichier. Un fichier abîmé n'est jamais remplacé."""
    chemin = _chemin_cours_passes()
    if _disque_passe["chemin"] != chemin:
        valeurs, lisible = {}, True
        try:
            with open(chemin) as f:
                brut = json.load(f)
            valeurs = {k: float(v) for k, v in brut.items()
                       if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0}
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError) as e:
            print(f"[ma_position] {chemin} illisible, gardé tel quel : {e}", file=sys.stderr)
            lisible = False
        _disque_passe.update(chemin=chemin, valeurs=valeurs, lisible=lisible)
    return _disque_passe


def _garder_cours(cle, v):
    """Ajoute un cours au fichier : sous un verrou entre processus (l'appli et la ligne de commande), en
    relisant d'abord le fichier pour ne rien perdre de ce qu'un autre a écrit, par un fichier temporaire unique."""
    import fcntl
    import tempfile
    with _verrou_passe:
        d = _cours_gardes()
        if not d["lisible"] or d["valeurs"].get(cle) == v:
            return
        d["valeurs"][cle] = v
        try:
            os.makedirs(os.path.dirname(d["chemin"]), exist_ok=True)
            with open(d["chemin"] + ".verrou", "a") as verrou:
                fcntl.flock(verrou.fileno(), fcntl.LOCK_EX)
                try:
                    with open(d["chemin"]) as f:
                        sur_disque = json.load(f)
                    for k, x in sur_disque.items():
                        if k not in d["valeurs"] and isinstance(x, (int, float)) and math.isfinite(x) and x > 0:
                            d["valeurs"][k] = float(x)
                except FileNotFoundError:
                    pass
                except (ValueError, AttributeError) as e:
                    print(f"[ma_position] {d['chemin']} abîmé entre-temps, gardé tel quel : {e}", file=sys.stderr)
                    d["lisible"] = False
                    return
                fd, tmp = tempfile.mkstemp(dir=os.path.dirname(d["chemin"]), prefix=".cours-", suffix=".tmp")
                with os.fdopen(fd, "w") as f:
                    json.dump(d["valeurs"], f, indent=1)
                os.replace(tmp, d["chemin"])
        except OSError as e:
            print(f"[ma_position] cours passés pas gardés : {e}", file=sys.stderr)


def cours_a(symbole, quand_utc):
    """
    Le cours d'un symbole à un moment passé : celui gardé sur disque s'il
    y est, sinon les barres de 5 minutes du jour (Yahoo les garde 60
    jours), sinon la clôture du jour (jamais gardée sur disque).
    """
    cle = (symbole, quand_utc.isoformat())
    if cle in _memo_passe:
        return _memo_passe[cle]
    garde = _cours_gardes()["valeurs"].get(f"{symbole}|{quand_utc.isoformat()}")
    if garde:
        _memo_passe[cle] = garde
        return garde
    repli = _memo_repli.get(cle)
    if repli and time.monotonic() - repli[0] < REPLI_S:
        return repli[1]
    jour = quand_utc.date()
    v, en_5_minutes = float("nan"), False
    try:
        c = _historique(symbole, start=jour - timedelta(days=4), end=jour + timedelta(days=1),
                        interval="5m", prepost=True)
        v = float(c.asof(quand_utc))
        en_5_minutes = v == v
    except ValueError:
        pass
    if v != v:
        c = _historique(symbole, start=jour - timedelta(days=10), end=jour + timedelta(days=1),
                        interval="1d")
        v = float(c.asof(quand_utc))
    # Gardé seulement s'il date de plus de 10 minutes : la barre de
    # 5 minutes en cours peut encore bouger.
    if datetime.now(timezone.utc) - quand_utc > timedelta(minutes=10):
        if en_5_minutes:
            _memo_passe[cle] = v
            if math.isfinite(v) and v > 0:
                _garder_cours(f"{symbole}|{quand_utc.isoformat()}", v)
        else:
            # La clôture du jour (ajustée des dividendes) n'est qu'un repli : gardée en mémoire jusqu'au
            # redémarrage, elle figeait un mauvais cours après une réponse vide de Yahoo (vérification du 17/11).
            _memo_repli[cle] = (time.monotonic(), v)
    return v


# ══════════════════════════════════════════════════════════════════
#  LE JOURNAL
# ══════════════════════════════════════════════════════════════════

def valeurs_du_journal():
    """Ce que le journal accepte : les 49 valeurs du bot, plus celles détenues hors du bot."""
    return sorted(set(engine.UNIVERSE) | set(HORS_DU_BOT))


def lire_journal():
    try:
        with open(JOURNAL) as f:
            journal = json.load(f)
        return journal if isinstance(journal, list) else []
    except (OSError, ValueError):
        return []


def _ecrire(chemin, donnees):
    """Remplacement d'un coup : le bot et le serveur ne lisent jamais un fichier à moitié écrit."""
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    tmp = chemin + ".tmp"
    with open(tmp, "w") as f:
        json.dump(donnees, f, indent=2, ensure_ascii=False)
    os.replace(tmp, chemin)


def ajouter_decision(payload):
    """
    Note un achat ou une vente déjà passés chez le courtier, et met le
    carnet du bot d'accord (en dollars, prix moyen pondéré).
    """
    sens = str(payload.get("sens", "")).strip()
    ticker = str(payload.get("ticker", "")).strip().upper()
    if sens not in ("achat", "vente"):
        raise ValueError("le sens est « achat » ou « vente »")
    if ticker not in valeurs_du_journal():
        raise ValueError(f"{ticker[:12]} ne fait pas partie des valeurs suivies "
                         f"(les 49 du bot, plus {', '.join(HORS_DU_BOT)})")
    try:
        quantite = float(payload.get("quantite"))
        prix_eur = float(payload.get("prix_eur"))
        montant_eur = float(payload.get("montant_eur", quantite * prix_eur))
        frais_eur = float(payload.get("frais_eur", 1.0))
    except (TypeError, ValueError):
        raise ValueError("quantité, prix, montant et frais doivent être des nombres")
    nombres = (quantite, prix_eur, montant_eur, frais_eur)
    if not all(math.isfinite(x) for x in nombres) or min(quantite, prix_eur, montant_eur) <= 0 \
            or frais_eur < 0:
        raise ValueError("quantité, prix et montant positifs, frais positifs ou nuls")
    # Au centime, comme le courtier : une vente notée sans montant gardait 9,86227947 € (vérification du 17/11).
    montant_eur = round(montant_eur, 2)
    if montant_eur <= 0:
        raise ValueError("quantité, prix et montant positifs, frais positifs ou nuls")
    # Une action offerte par le courtier (choix de Mathis du 17/11/2026) : rien payé, donc pas de frais.
    offert = sens == "achat" and payload.get("offert") is True
    if offert:
        frais_eur = 0.0
    raison = " ".join(str(payload.get("raison", "")).split())[:200]
    date = str(payload.get("date") or maintenant_paris())
    try:
        quand = en_utc(date)
    except (TypeError, ValueError):
        raise ValueError("date illisible (attendu : 2026-11-17T15:45, à l'heure de Paris)")
    if quand > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("cette date est dans le futur : une décision se note une fois passée chez le courtier")
    prix_usd = prix_eur * cours_a(CHANGE, quand)

    with _verrou:
        journal = lire_journal()
        decision = {"date": date, "sens": sens, "ticker": ticker, "quantite": quantite,
                    "prix_eur": prix_eur, "prix_usd": round(prix_usd, 4),
                    "montant_eur": montant_eur, "frais_eur": frais_eur, "raison": raison}
        if offert:
            decision["offert"] = True
        if sens == "vente":
            # Ce qui était détenu À LA DATE de la vente, pas aujourd'hui : une vente antidatée passait
            # et faussait tout le bilan (vérification du 17/11).
            fautives = _ventes_impossibles(journal + [decision])
            if any(f is decision for f in fautives):
                raise ValueError(f"à cette date, tu ne détenais pas autant de {ticker}")
            if len(fautives) > len(_ventes_impossibles(journal)):
                raise ValueError(f"cette vente rendrait impossible une vente de {ticker} notée plus tard")
        journal.append(decision)
        _ecrire(JOURNAL, journal)

        # Le carnet du bot ne reçoit que ses 49 valeurs : il ne suit pas les autres.
        if ticker not in engine.UNIVERSE:
            return decision
        try:
            with open(CARNET) as f:
                carnet = json.load(f)
        except (OSError, ValueError):
            carnet = {}
        ligne = carnet.get(ticker)
        if sens == "achat":
            if ligne:
                q = ligne["quantity"] + quantite
                ligne["entry_price"] = (ligne["quantity"] * ligne["entry_price"] + quantite * prix_usd) / q
                ligne["quantity"] = q
            else:
                carnet[ticker] = {"quantity": quantite, "entry_price": round(prix_usd, 4),
                                  "entry_date": date}
        elif ligne:
            ligne["quantity"] -= quantite
            if ligne["quantity"] <= 1e-9:
                del carnet[ticker]
        _ecrire(CARNET, carnet)
    return decision


# ══════════════════════════════════════════════════════════════════
#  CE QUE LIT LE TABLEAU DE BORD
# ══════════════════════════════════════════════════════════════════

def position(rang_de=None):
    """
    La position en direct. `rang_de(ticker)` donne (rang, classées) depuis
    le dernier scan du serveur, s'il en a un.

    Chaque ligne compte comme le courtier : depuis son prix d'achat, une
    action offerte depuis son prix du jour du cadeau. Le TOTAL répond à
    « est-ce que je gagne ? » (choix de Mathis du 17/11/2026) : la valeur
    des actions + l'argent des ventes pas réinvesti − ce qui est sorti de
    ta poche (`flux`). Il garde donc le résultat des ventes, et l'action
    offerte y compte comme un gain.
    """
    eurusd, _ = cours_actuel(CHANGE)
    journal = lire_journal()
    lignes, valeur, cout = [], 0.0, 0.0
    for l in agreger(journal):
        prix_usd, heure = cours_actuel(l["ticker"])
        c = calcul_ligne(l["quantite"], l["cout_eur"], l["montant_eur"], prix_usd, eurusd)
        rang, classees = rang_de(l["ticker"]) if rang_de else (None, None)
        lignes.append({**l, **c, "cours_a": heure.isoformat(), "seance_du_cours": seance_du_cours(heure),
                       "rang": rang, "classees": classees,
                       "suivie_par_le_bot": l["ticker"] in engine.UNIVERSE,
                       "resultats": engine.prochaine_publication(l["ticker"])})
        valeur += c["valeur_eur"]
        cout += l["cout_eur"]
    f = flux(journal)
    gain = valeur + f["encaisse_eur"] - f["verse_eur"]
    gain_sf = valeur + f["encaisse_sans_frais_eur"] - f["verse_sans_frais_eur"]
    return {
        "maj": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "eurusd": eurusd,
        "lignes": lignes,
        "total": {"valeur_eur": valeur, "cout_eur": cout, **f,
                  "gain_eur": gain, "gain_pct": gain / f["verse_eur"] if f["verse_eur"] else None,
                  "gain_sans_frais_eur": gain_sf,
                  "gain_sans_frais_pct": (gain_sf / f["verse_sans_frais_eur"]
                                          if f["verse_sans_frais_eur"] else None)},
        "frais_vente_eur": FRAIS_VENTE,
        "avertissement": AVERTISSEMENT,
    }


def journal_compare(prix_actuels=None, fx_actuel=None):
    """
    Chaque décision, et pour chaque achat le même argent passé par le
    S&P 500 (SPY) au même moment : son « jumeau ». `prix_actuels`
    ({ticker: cours en $}) et `fx_actuel` sont les cours de la position
    déjà affichée : le journal compte avec les mêmes.

    Une vente retire sa part de CHAQUE achat de la valeur (la règle du
    prix moyen pondéré, comme `agreger`) et lui rend sa part de l'argent
    encaissé ; le jumeau de chaque achat vend la même part de ses parts de
    SPY, le même jour, au cours de ce moment. Un achat vaut donc ce qu'il
    en reste plus ce qu'il a rapporté, ventes comprises, et le bilan
    compare tout le portefeuille (positions + ventes encaissées) au même
    argent passé par le S&P 500 aux mêmes dates. Frais exclus des deux
    côtés. Tout est compté au centime : les écarts sont ceux des montants
    affichés.
    """
    journal = sorted(lire_journal(), key=ordre)
    valeurs = valeurs_du_journal()    # pour la liste du formulaire
    if not journal:
        return {"decisions": [], "valeurs": valeurs, "avertissement": AVERTISSEMENT, "bilan": None}
    indice_maintenant, _ = cours_actuel(INDICE)
    fx_maintenant = fx_actuel or cours_actuel(CHANGE)[0]
    prix_maintenant = dict(prix_actuels or {})
    sortie, achats = [], {}
    # Le bilan du portefeuille : l'argent des ventes reste en caisse et paie d'abord les achats suivants ;
    # seul ce qui manque est de l'argent versé, que le jumeau du portefeuille place dans SPY et garde.
    caisse = verse = parts_versees = 0.0
    for d in journal:
        ligne = dict(d)
        quand = en_utc(d["date"])
        ligne["date_utc"] = quand.isoformat()
        if d["sens"] == "achat":
            depot = max(0.0, d["montant_eur"] - caisse)
            caisse = max(0.0, caisse - d["montant_eur"])
            if depot > 0:
                verse += depot
                parts_versees += depot * cours_a(CHANGE, quand) / cours_a(INDICE, quand)
        else:
            caisse += d["montant_eur"]
        if d["sens"] == "achat":
            achats.setdefault(d["ticker"], []).append({
                "ligne": ligne, "reste": 1.0, "encaisse_eur": 0.0, "vendu": False,
                "parts_spy": d["montant_eur"] * cours_a(CHANGE, quand) / cours_a(INDICE, quand),
                "jumeau_encaisse_eur": 0.0})
        else:
            tenus = [a for a in achats.get(d["ticker"], []) if a["reste"] > 1e-12]
            detenu = sum(a["ligne"]["quantite"] * a["reste"] for a in tenus)
            part = min(1.0, d["quantite"] / detenu) if detenu else 0.0
            if tenus:
                spy_v, fx_v = cours_a(INDICE, quand), cours_a(CHANGE, quand)
            for a in tenus:
                a["encaisse_eur"] += d["montant_eur"] * a["ligne"]["quantite"] * a["reste"] / detenu
                vendues = a["parts_spy"] * part
                a["jumeau_encaisse_eur"] += vendues * spy_v / fx_v
                a["parts_spy"] -= vendues
                a["reste"] *= 1 - part
                a["vendu"] = True
        sortie.append(ligne)
    positions = 0.0
    nombre = devant = 0
    for ticker, lots in achats.items():
        # Une valeur vendue en entier n'a plus besoin de son cours : le demander rendait tout le carnet
        # « indisponible » le jour où Yahoo ne le donnait plus (vérification du 17/11).
        restant = sum(a["ligne"]["quantite"] * a["reste"] for a in lots)
        if restant > 1e-9 and ticker not in prix_maintenant:
            prix_maintenant[ticker] = cours_actuel(ticker)[0]
        for a in lots:
            # Les positions s'additionnent en valeurs exactes et ne s'arrondissent qu'une fois : le bilan
            # tombe sur « Ta position » au centime près (vu le 17/11 : 198,34 contre 198,33).
            reste_brut = (a["ligne"]["quantite"] * a["reste"] * prix_maintenant[ticker] / fx_maintenant
                          if restant > 1e-9 else 0.0)
            choix_brut = reste_brut + a["encaisse_eur"]
            jumeau_brut = a["parts_spy"] * indice_maintenant / fx_maintenant + a["jumeau_encaisse_eur"]
            choix, jumeau = round(choix_brut, 2), round(jumeau_brut, 2)
            if math.isfinite(choix) and math.isfinite(jumeau):
                a["ligne"].update({"valeur_choix_eur": choix, "valeur_indice_eur": jumeau,
                                   "ecart_eur": round(choix - jumeau, 2), "ventes_comprises": a["vendu"]})
                positions, nombre = positions + reste_brut, nombre + 1
                devant += round(choix - jumeau, 2) > 0
    # Le bilan : ce que tu as (positions + ventes pas réinvesties) contre l'argent versé, placé dans le
    # S&P 500 aux mêmes dates et gardé. Une vente rachetée n'est donc jamais comptée deux fois.
    # Arrondis d'abord, additionnés ensuite : la phrase « X de positions et Y de ventes » tombe juste sur
    # « Ton portefeuille vaut Z » (2 relectures sur 11 avaient un centime d'écart, vérification du 17/11).
    positions_c, caisse_c = round(positions, 2), round(caisse, 2)
    toi = round(positions_c + caisse_c, 2)
    indice = round(parts_versees * indice_maintenant / fx_maintenant, 2)
    offert = sum(d["montant_eur"] for d in journal if d["sens"] == "achat" and d.get("offert"))
    bilan = ({"toi_eur": toi, "indice_eur": indice, "ecart_eur": round(toi - indice, 2),
              "positions_eur": positions_c, "encaisse_eur": caisse_c, "verse_eur": round(verse, 2),
              "offert_eur": round(offert, 2), "achats": nombre, "devant": devant,
              "ventes": any(a["vendu"] for lots in achats.values() for a in lots)}
             if nombre and math.isfinite(toi) and math.isfinite(indice) else None)
    return {"decisions": sortie, "indice": "S&P 500 (SPY)", "valeurs": valeurs, "bilan": bilan,
            "avertissement": AVERTISSEMENT}


PERIODES = {"1j": dict(period="5d", interval="5m"),
            "1m": dict(period="1mo", interval="60m"),
            "1a": dict(period="2y", interval="1d")}


def graphique(ticker, periode):
    """Le cours en dollars et en euros, la ligne d'achat, et pour 1a les indicateurs."""
    if periode not in PERIODES:
        raise ValueError("période : 1j, 1m ou 1a")
    if ticker != INDICE and ticker not in valeurs_du_journal():
        raise ValueError(f"{ticker[:12]} ne fait pas partie des valeurs suivies")
    cours = _historique(ticker, **PERIODES[periode])
    fx = _historique(CHANGE, **PERIODES[periode]).reindex(cours.index, method="ffill").bfill()
    if periode == "1j":
        # La dernière séance seulement, à l'heure de New York.
        jours = cours.index.tz_convert("America/New_York").date
        garde = jours == jours[-1]
        cours, fx = cours[garde], fx[garde]
    sortie = {"ticker": ticker, "periode": periode, "indicateurs": None,
              "points": [{"t": t.isoformat(), "usd": round(float(u), 4), "eur": round(float(u / x), 4)}
                         for t, u, x in zip(cours.index, cours.values, fx.values)]}
    ligne = next((l for l in agreger(lire_journal()) if l["ticker"] == ticker), None)
    if ligne:
        sortie["achat"] = {"eur": ligne["montant_eur"] / ligne["quantite"],
                           "seuil_eur": ligne["cout_eur"] / ligne["quantite"]}
    if periode == "1a":
        sma50, sma200 = moyenne_mobile(cours, 50), moyenne_mobile(cours, 200)
        r = rsi(cours)
        m, s = macd(cours)
        dernier_an = cours.index >= cours.index[-1] - pd.Timedelta(days=365)
        sortie["points"] = [p for p, garde in zip(sortie["points"], dernier_an) if garde]
        rond = lambda v: None if v != v else round(float(v), 4)
        sortie["indicateurs"] = {
            "avertissement": AVERTISSEMENT_INDICATEURS,
            "derniers": {"sma50": rond(sma50.iloc[-1]), "sma200": rond(sma200.iloc[-1]),
                         "rsi": rond(r.iloc[-1]), "macd": rond(m.iloc[-1]), "signal": rond(s.iloc[-1])},
            "series": [{"t": t.isoformat(), "sma50": rond(a), "sma200": rond(b), "rsi": rond(c),
                        "macd": rond(d), "signal": rond(e)}
                       for t, a, b, c, d, e in zip(cours.index[dernier_an], sma50[dernier_an],
                                                   sma200[dernier_an], r[dernier_an],
                                                   m[dernier_an], s[dernier_an])],
        }
    return sortie


# ══════════════════════════════════════════════════════════════════
#  LES VÉRIFICATIONS
# ══════════════════════════════════════════════════════════════════

def verifier():
    # Remplacés le temps des cas « hors des 49 » (fichiers jetables, cours
    # inventés), puis remis : aucun réseau, rien de réel n'est touché.
    global JOURNAL, CARNET, cours_a, cours_actuel, _historique
    proche = lambda a, b, tol=1e-9: abs(a - b) < tol
    cas = []

    def ok(nom, condition):
        cas.append(condition)
        print(("✓ " if condition else "✗ ") + nom)

    # Des chiffres inventés, calculés à la main : 0,5 action à 120 €,
    # 60 € investis + 1 € de frais, soit 61 € dépensés.
    ok("15 h 45 à Paris le 02/03 = 14 h 45 UTC (heure d'hiver)",
       en_utc("2026-03-02T15:45:00") == datetime(2026, 3, 2, 14, 45, 0, tzinfo=timezone.utc))
    ok("19 h 30 à Paris le 13/07 = 17 h 30 UTC (heure d'été)",
       en_utc("2026-07-13T19:30:00").hour == 17)

    achat = {"date": "2026-03-02T15:45:00", "sens": "achat", "ticker": "MSFT",
             "quantite": 0.5, "prix_eur": 120.0, "montant_eur": 60.0, "frais_eur": 1.0}
    l = agreger([achat])[0]
    ok("un achat de 60 € + 1 € de frais coûte 61 €", proche(l["cout_eur"], 61.0))

    c = calcul_ligne(0.5, 61.0, 60.0, 132.0, 1.10)
    ok(f"à 132 $ et 1 € = 1,10 $ : {c['valeur_eur']:.2f} € (0,5 × 132 / 1,10 = 60,00 €)",
       abs(c["valeur_eur"] - 60.0) < 0.01)
    ok(f"perte frais compris : {c['gain_pct']:+.2%} (60 / 61 − 1 = −1,64 %)",
       abs(c["gain_pct"] + 0.0164) < 0.0001)
    ok(f"prix qui rembourse l'achat : {c['seuil_achat_eur']:.2f} € (61 / 0,5)",
       abs(c["seuil_achat_eur"] - 122.0) < 0.01)
    ok(f"avec le 1 € d'une vente : {c['seuil_vente_eur']:.2f} € (62 / 0,5)",
       abs(c["seuil_vente_eur"] - 124.0) < 0.01)
    ok(f"prix d'achat moyen : {c['prix_achat_eur']:.2f} € (60 / 0,5 = 120)",
       abs(c["prix_achat_eur"] - 120.0) < 0.01)

    vente = {**achat, "date": "2026-03-09T16:00:00", "sens": "vente", "quantite": 0.25,
             "montant_eur": 31.0, "frais_eur": 1.0}
    l2 = agreger([achat, vente])[0]
    ok("vendre la moitié retire la moitié du coût (prix moyen pondéré)",
       proche(l2["cout_eur"], 30.5) and proche(l2["quantite"], 0.25))
    ok("tout vendre vide la ligne", agreger([achat, {**vente, "quantite": 0.5}]) == [])

    ok("60 € dans un indice qui monte de 1 %, dollar stable : 60,60 €",
       proche(calcul_comparaison(60.0, 600.0, 606.0, 1.10, 1.10), 60.6))
    ok("même indice, mais le dollar perd 10 % face à l'euro : 55,09 €",
       abs(calcul_comparaison(60.0, 600.0, 606.0, 1.10, 1.21) - 55.0909) < 0.001)

    montee = pd.Series([float(i) for i in range(1, 40)])
    ok("RSI d'une hausse sans aucune baisse : 100", proche(rsi(montee).iloc[-1], 100.0))
    plat = pd.Series([10.0, 11.0] * 30)
    ok(f"RSI d'un aller-retour régulier : {rsi(plat).iloc[-1]:.0f} (autour de 50)",
       45 < rsi(plat).iloc[-1] < 55)
    ok("moyenne mobile de 1, 2, 3 sur 3 jours : 2", proche(moyenne_mobile(pd.Series([1.0, 2.0, 3.0]), 3).iloc[-1], 2.0))
    m, s = macd(pd.Series([100.0] * 60))
    ok("MACD d'un cours immobile : 0", proche(m.iloc[-1], 0.0) and proche(s.iloc[-1], 0.0))

    # ── Une valeur détenue hors des 49 (TSLA) : notée dans le journal et
    #    suivie par le carnet de bord, jamais écrite dans le carnet du bot.
    import tempfile

    def refus(fn):
        try:
            fn()
        except ValueError:
            return True
        return False

    def sans_reseau(*_a, **_k):
        raise RuntimeError("pas de réseau dans les vérifications")

    reel = (JOURNAL, CARNET, cours_a, cours_actuel, _historique)
    with tempfile.TemporaryDirectory() as dossier:
        JOURNAL = os.path.join(dossier, "journal.json")
        CARNET = os.path.join(dossier, "stock_positions.json")
        _ecrire(CARNET, {})
        instant = datetime.now(timezone.utc)
        cours_a = lambda _symbole, _quand: 1.10
        cours_actuel = lambda symbole: (1.10 if symbole == CHANGE else 330.0, instant)
        _historique = sans_reseau
        try:
            prime = {"date": "2026-03-02T15:45:00", "sens": "achat", "ticker": "tsla",
                     "quantite": 0.03, "prix_eur": 300.0, "montant_eur": 9.0, "frais_eur": 0}
            note = not refus(lambda: ajouter_decision(prime))
            j = lire_journal()
            ok("TSLA, hors des 49, se note dans le journal",
               note and len(j) == 1 and j[0]["ticker"] == "TSLA")
            with open(CARNET) as f:
                ok("… mais pas dans le carnet du bot, qui ne la suit pas", json.load(f) == {})
            ok("une ligne notée à 0 € de frais coûte son seul montant : 9 €",
               note and proche(agreger(j)[0]["cout_eur"], 9.0))
            ok("une valeur ni dans les 49 ni détenue hors du bot est refusée, et rien n'est écrit",
               refus(lambda: ajouter_decision({**prime, "ticker": "ZZZZ"})) and len(lire_journal()) == len(j))
            ajouter_decision({**achat, "date": "2026-03-03T15:45:00"})
            with open(CARNET) as f:
                ok("une valeur des 49 met toujours le carnet du bot à jour", "MSFT" in json.load(f))
            lignes = {l["ticker"]: l for l in position()["lignes"]}
            ok("la position dit TSLA hors du bot, MSFT dedans",
               lignes.get("TSLA", {}).get("suivie_par_le_bot") is False
               and lignes.get("MSFT", {}).get("suivie_par_le_bot") is True)
            v = lignes.get("TSLA", {}).get("valeur_eur", 0.0)
            ok(f"TSLA à 330 $ et 1 € = 1,10 $ : {v:.2f} € (0,03 × 330 / 1,10 = 9,00 €)", abs(v - 9.0) < 0.01)
            ok("le formulaire propose les 49 et TSLA, soit 50 valeurs",
               len(valeurs_du_journal()) == 50 and set(engine.UNIVERSE) | {"TSLA"} == set(valeurs_du_journal()))
            ok("le graphique refuse une valeur inconnue, avant tout appel réseau",
               refus(lambda: graphique("ZZZZ", "1j")))
            try:
                graphique("TSLA", "1j")
                passe = False
            except RuntimeError:
                passe = True      # la garde l'a laissée passer : il ne manquait que le réseau
            except ValueError:
                passe = False
            ok("le graphique accepte TSLA (il irait chercher ses cours)", passe)
        finally:
            JOURNAL, CARNET, cours_a, cours_actuel, _historique = reel

    _verifier_lot3(ok, proche)
    _verifier_lot4(ok, proche)
    print(f"\n{sum(cas)}/{len(cas)} vérifications réussies")
    return all(cas)


def _verifier_lot3(ok, proche):
    """Le carnet réel, lot 3 (17/11/2026) : le duel avec les ventes, les cours passés gardés, l'heure de Paris."""
    global JOURNAL, CARNET, cours_a, cours_actuel, _historique
    import tempfile
    import time as horloge
    reel = (JOURNAL, CARNET, cours_a, cours_actuel, _historique)
    utc = lambda texte: en_utc(texte)
    with tempfile.TemporaryDirectory() as dossier:
        JOURNAL = os.path.join(dossier, "journal.json")
        CARNET = os.path.join(dossier, "stock_positions.json")
        _ecrire(CARNET, {})
        _memo_passe.clear()
        _disque_passe["chemin"] = None
        try:
            # ── Le duel avec une vente. Deux achats de MSFT, puis la moitié vendue (chiffres inventés) :
            #    lundi 0,5 à 120 € (60 €, SPY 600 $, 1 € = 1,20 $ → 0,12 part de SPY) ; mardi 0,25 à 160 € (40 €,
            #    SPY 640 $ → 0,075 part) ; mercredi, vente de 0,375 à 200 € (75 €), SPY 660 $ et 1 € = 1,10 $.
            #    Aujourd'hui : MSFT 220 $ (200 €), SPY 702,9 $ (639 €).
            passes = {("SPY", "2026-03-02"): 600.0, ("SPY", "2026-03-03"): 640.0, ("SPY", "2026-03-04"): 660.0,
                      ("EURUSD=X", "2026-03-02"): 1.20, ("EURUSD=X", "2026-03-03"): 1.20, ("EURUSD=X", "2026-03-04"): 1.10}
            cours_a = lambda symbole, quand: passes[(symbole, quand.date().isoformat())]
            cours_actuel = lambda symbole: ({"SPY": 702.9, "EURUSD=X": 1.10, "MSFT": 220.0, "AAPL": 352.0}[symbole],
                                            datetime.now(timezone.utc))
            achat1 = {"date": "2026-03-02T15:45:00", "sens": "achat", "ticker": "MSFT", "quantite": 0.5,
                      "prix_eur": 120.0, "montant_eur": 60.0, "frais_eur": 1.0, "raison": "Un"}
            achat2 = {**achat1, "date": "2026-03-03T15:45:00", "quantite": 0.25, "prix_eur": 160.0, "montant_eur": 40.0,
                      "raison": "Deux"}
            vente = {**achat1, "date": "2026-03-04T15:45:00", "sens": "vente", "quantite": 0.375, "prix_eur": 200.0,
                     "montant_eur": 75.0, "raison": "Moitié"}
            _ecrire(JOURNAL, [achat1, achat2])
            sans = journal_compare()
            a1 = sans["decisions"][0]
            ok("sans vente, rien ne change : 0,5 × 200 = 100 € contre 60 × (702,9/600) × (1,20/1,10) = 76,68 €",
               a1["valeur_choix_eur"] == 100.0 and a1["valeur_indice_eur"] == 76.68
               and a1["valeur_indice_eur"] == round(calcul_comparaison(60.0, 600.0, 702.9, 1.20, 1.10), 2)
               and a1["ventes_comprises"] is False)
            _ecrire(JOURNAL, [achat1, achat2, vente])
            j = journal_compare()
            d1, d2 = j["decisions"][0], j["decisions"][1]
            ok("la vente de la moitié : l'achat 1 garde 0,25 action (50 €) et reçoit 2/3 des 75 € (50 €) → 100 € ; son "
               "jumeau vend 0,06 part à 600 € (36 €) et garde 0,06 × 639 € → 74,34 € ; écart +25,66 €",
               d1["valeur_choix_eur"] == 100.0 and d1["valeur_indice_eur"] == 74.34 and d1["ecart_eur"] == 25.66
               and d1["ventes_comprises"] is True)
            ok("l'achat 2 : 0,125 action (25 €) + 1/3 des 75 € (25 €) = 50 € ; jumeau 0,0375 × 639 + 22,50 = 46,46 € ; +3,54 €",
               d2["valeur_choix_eur"] == 50.0 and d2["valeur_indice_eur"] == 46.46 and d2["ecart_eur"] == 3.54)
            b = j["bilan"]
            ok("le bilan du portefeuille : 75 € de positions + 75 € encaissés = 150 € ; les 100 € versés, placés dans SPY "
               "et gardés : (0,12 + 0,075) × 639 = 124,605 €",
               b["toi_eur"] == 150.0 and b["positions_eur"] == 75.0 and b["encaisse_eur"] == 75.0
               and b["verse_eur"] == 100.0 and abs(b["indice_eur"] - 124.605) < 0.006
               and b["ecart_eur"] == round(150.0 - b["indice_eur"], 2) and b["ventes"] is True and b["devant"] == 2)
            # Une vente RACHETÉE : 45 € de MSFT, tout revendu 46 €, puis 46 € d'AAPL. Seuls 45 € ont été versés.
            _ecrire(JOURNAL, [{**achat1, "quantite": 0.1, "prix_eur": 450.0, "montant_eur": 45.0},
                              {**vente, "date": "2026-03-03T15:45:00", "quantite": 0.1, "prix_eur": 460.0,
                               "montant_eur": 46.0},
                              {**achat1, "date": "2026-03-04T15:45:00", "ticker": "AAPL", "quantite": 0.2,
                               "prix_eur": 230.0, "montant_eur": 46.0}])
            r = journal_compare()
            ok("une vente rachetée n'est pas comptée deux fois : AAPL 0,2 × 320 = 64 € (comme « Ta position ») contre les "
               "45 € versés, 0,09 part de SPY → 57,51 € ; écart +6,49 €",
               r["bilan"]["toi_eur"] == 64.0 and r["bilan"]["indice_eur"] == 57.51 and r["bilan"]["ecart_eur"] == 6.49
               and r["bilan"]["verse_eur"] == 45.0 and r["bilan"]["encaisse_eur"] == 0.0)
            ok("… les duels par achat restent : MSFT tout vendu 46 € contre son jumeau vendu 48 € (−2,00 €), AAPL 64 € "
               "contre 48,99 €",
               [(x["valeur_choix_eur"], x["valeur_indice_eur"]) for x in r["decisions"] if x["sens"] == "achat"]
               == [(46.0, 48.0), (64.0, 48.99)])
            # Deux valeurs, deux ventes de MSFT : chaque vente ne touche que MSFT, la seconde prend la moitié du reste.
            _ecrire(JOURNAL, [achat1,
                              {**achat2, "ticker": "AAPL", "quantite": 0.205, "prix_eur": 200.0, "montant_eur": 41.0},
                              {**vente, "quantite": 0.25, "montant_eur": 50.0},
                              {**vente, "date": "2026-03-04T16:00:00", "quantite": 0.125, "montant_eur": 25.0}])
            k = journal_compare()
            km, ka = [x for x in k["decisions"] if x["sens"] == "achat"]
            ok("deux ventes de MSFT : 0,125 restante (25 €) + 75 € encaissés = 100 € ; jumeau 0,03 × 639 + 54 = 73,17 € ; "
               "AAPL intacte : 65,60 € contre 0,076875 × 639 = 49,12 €",
               (km["valeur_choix_eur"], km["valeur_indice_eur"], ka["valeur_choix_eur"], ka["valeur_indice_eur"])
               == (100.0, 73.17, 65.6, 49.12))
            ok("… et le bilan : 25 + 65,60 + 75 = 165,60 € contre les 101 € versés, (0,12 + 0,076875) × 639 = 125,80 €",
               k["bilan"]["toi_eur"] == 165.6 and k["bilan"]["indice_eur"] == 125.8 and k["bilan"]["ecart_eur"] == 39.8)
            ok("même règle que le prix moyen pondéré : il reste 0,375 MSFT, comme agreger",
               proche(agreger([achat1, achat2, vente])[0]["quantite"], 0.25 + 0.125))
            ok("chaque décision porte sa date en UTC (15 h 45 à Paris le 02/03 = 14 h 45 UTC)",
               d1["date_utc"] == "2026-03-02T14:45:00+00:00" and j["decisions"][2]["date_utc"] == "2026-03-04T14:45:00+00:00")
            _ecrire(JOURNAL, [achat1, {**achat1, "date": "2026-03-03T15:45:00"}])
            cours_actuel = lambda symbole: ({"SPY": 702.9, "EURUSD=X": 1.10, "MSFT": 24.2088}[symbole],
                                            datetime.now(timezone.utc))
            b = journal_compare(prix_actuels={"MSFT": 24.2088}, fx_actuel=1.10)["bilan"]
            ok("le bilan arrondit une seule fois : 2 × 0,5 × 24,2088/1,10 = 22,008 → 22,01 € (comme « Ta position »), "
               "pas 11,00 + 11,00", b["toi_eur"] == 22.01)
            cours_actuel = lambda symbole: ({"SPY": 702.9, "EURUSD=X": 1.10, "MSFT": 220.0}[symbole],
                                            datetime.now(timezone.utc))
            _ecrire(JOURNAL, [achat1, {**vente, "quantite": 0.75, "montant_eur": 150.0}])
            tout = journal_compare()["decisions"][0]
            ok("tout vendu : l'achat vaut ce qu'il a rapporté (150 €), son jumeau aussi (0,12 × 600 = 72 €)",
               tout["valeur_choix_eur"] == 150.0 and tout["valeur_indice_eur"] == 72.0)

            # ── Les cours passés gardés sur disque (à côté du journal).
            cours_a = reel[2]
            appels = []

            def faux_historique(symbole, **kw):
                appels.append(kw.get("interval"))
                if kw.get("interval") == "5m" and symbole != "AAPL":
                    index = pd.DatetimeIndex([pd.Timestamp("2026-03-02 14:40", tz="UTC"), pd.Timestamp("2026-03-02 14:45", tz="UTC")])
                    return pd.Series([600.0, 601.0], index=index)
                if kw.get("interval") == "5m":
                    raise ValueError("pas de barres de 5 minutes")
                return pd.Series([590.0], index=pd.DatetimeIndex([pd.Timestamp("2026-03-02", tz="UTC")]))
            _historique = faux_historique
            _memo_passe.clear()
            quand = utc("2026-03-02T15:46:00")
            v1 = cours_a("SPY", quand)
            garde = os.path.join(dossier, "cours_passes.json")
            with open(garde) as f:
                sur_disque = json.load(f)
            ok("un cours lu en barres de 5 minutes (601 $) est gardé sur disque, à côté du journal",
               v1 == 601.0 and sur_disque == {"SPY|2026-03-02T14:46:00+00:00": 601.0})
            v_jour = cours_a("AAPL", quand)
            with open(garde) as f:
                ok("… mais jamais la clôture journalière de secours (ajustée des dividendes)",
                   v_jour == 590.0 and "AAPL|2026-03-02T14:46:00+00:00" not in json.load(f))
            _memo_passe.clear()
            _disque_passe["chemin"] = None
            n = len(appels)
            ok("au lancement suivant, le cours gardé est relu sur disque, sans Yahoo",
               cours_a("SPY", quand) == 601.0 and len(appels) == n)
            recent = datetime.now(timezone.utc) - timedelta(minutes=2)
            cours_a("SPY", recent)
            with open(garde) as f:
                ok("un cours de moins de 10 minutes (la barre peut encore bouger) n'est pas gardé",
                   len(json.load(f)) == 1)
            with open(garde, "w") as f:
                f.write("{abîmé")
            _memo_passe.clear()
            _disque_passe["chemin"] = None
            cours_a("SPY", utc("2026-03-02T15:47:00"))
            with open(garde) as f:
                ok("un fichier de cours gardés abîmé n'est jamais remplacé", f.read() == "{abîmé")

            # ── L'heure de Paris, même si le Mac est à Tokyo.
            ancien = os.environ.get("TZ")
            os.environ["TZ"] = "Asia/Tokyo"
            horloge.tzset()
            try:
                note = datetime.fromisoformat(maintenant_paris())
                paris = datetime.now(PARIS).replace(tzinfo=None)
                cours_a = lambda _s, _q: 1.10
                _ecrire(JOURNAL, [])
                d = ajouter_decision({**achat1, "date": ""})
                ok("Mac à Tokyo : une décision sans date prend l'heure de Paris (pas celle du Mac, 8 h plus tard)",
                   abs((note - paris).total_seconds()) < 5 and abs((note - datetime.now()).total_seconds()) > 3600
                   and abs((datetime.fromisoformat(d["date"]) - paris).total_seconds()) < 5)
            finally:
                if ancien is None:
                    os.environ.pop("TZ", None)
                else:
                    os.environ["TZ"] = ancien
                horloge.tzset()

            ok("d'où vient un cours (heure de New York, en hiver UTC − 5) : 9 h 29 avant-séance, 9 h 30 et 15 h 59 "
               "séance, 16 h après-séance",
               [seance_du_cours(datetime(2026, 11, 17, h, mi, tzinfo=timezone.utc))
                for h, mi in ((13, 0), (14, 29), (14, 30), (20, 59), (21, 0), (21, 30))]
               == ["avant", "avant", "ouverte", "ouverte", "apres", "apres"])
        finally:
            JOURNAL, CARNET, cours_a, cours_actuel, _historique = reel
            _memo_passe.clear()
            _disque_passe["chemin"] = None


def _verifier_lot4(ok, proche):
    """Les défauts trouvés par la vérification indépendante du 17/11/2026, et les deux choix de Mathis
    (le total garde le résultat des ventes ; une action offerte compte à 0 €). Chiffres inventés."""
    global JOURNAL, CARNET, cours_a, cours_actuel, _historique
    import tempfile
    reel = (JOURNAL, CARNET, cours_a, cours_actuel, _historique)
    cours_a_reel = cours_a

    def refus(fn, morceau=""):
        try:
            fn()
        except ValueError as e:
            return morceau in str(e)
        return False

    with tempfile.TemporaryDirectory() as dossier:
        JOURNAL = os.path.join(dossier, "journal.json")
        CARNET = os.path.join(dossier, "stock_positions.json")
        _ecrire(CARNET, {})
        _memo_passe.clear()
        _memo_repli.clear()
        _disque_passe["chemin"] = None
        try:
            passes = {("SPY", "2026-03-02"): 600.0, ("SPY", "2026-03-03"): 640.0, ("SPY", "2026-03-04"): 660.0,
                      ("EURUSD=X", "2026-03-02"): 1.20, ("EURUSD=X", "2026-03-03"): 1.20,
                      ("EURUSD=X", "2026-03-04"): 1.10}
            cours_a = lambda symbole, quand: passes[(symbole, quand.date().isoformat())]
            maintenant = {"SPY": 702.9, "EURUSD=X": 1.10, "AAPL": 352.0}      # MSFT absent : jamais demandée
            cours_actuel = lambda symbole: (maintenant[symbole], datetime.now(timezone.utc))
            achat1 = {"date": "2026-03-02T15:45:00", "sens": "achat", "ticker": "MSFT", "quantite": 0.5,
                      "prix_eur": 120.0, "montant_eur": 60.0, "frais_eur": 1.0, "raison": "Un"}
            vente = {**achat1, "date": "2026-03-03T16:00:00", "sens": "vente", "prix_eur": 130.0,
                     "montant_eur": 65.0, "raison": "Tout"}
            achat2 = {**achat1, "date": "2026-03-03T16:00:00", "ticker": "AAPL", "quantite": 0.2, "prix_eur": 200.0,
                      "montant_eur": 40.0, "raison": "Deux"}

            # ── 1. Une vente et un achat à la même minute : la vente passe d'abord, quel que soit l'ordre du fichier.
            f1, f2 = flux([achat1, achat2, vente]), flux([achat1, vente, achat2])
            ok("même minute, achat saisi d'abord ou vente saisie d'abord : le même argent (61 € versés, 23 € en "
               "caisse après les 41 € d'AAPL payés par les 64 € de la vente)",
               f1 == f2 and proche(f1["verse_eur"], 61.0) and proche(f1["encaisse_eur"], 23.0)
               and proche(f1["verse_sans_frais_eur"], 60.0) and proche(f1["encaisse_sans_frais_eur"], 25.0))
            # Même valeur, même minute : la vente prend sa part du coût AVANT l'achat (prix moyen pondéré).
            meme = {**achat1, "date": "2026-03-03T16:00:00", "prix_eur": 140.0, "montant_eur": 70.0}
            petite = {**vente, "quantite": 0.25, "prix_eur": 140.0, "montant_eur": 35.0}
            lm = agreger([achat1, meme, petite])[0]
            ok("même valeur, même minute : la vente de 0,25 retire la moitié des 61 € (30,50 €) avant l'achat de "
               "70 € + 1 € : 0,75 action pour 101,50 € (et non 99 €)",
               proche(lm["quantite"], 0.75) and proche(lm["cout_eur"], 101.5) and proche(lm["montant_eur"], 100.0))
            mini = {**vente, "date": "2026-03-04T16:00:00", "quantite": 0.004, "prix_eur": 125.0, "montant_eur": 0.5}
            fm = flux([achat1, mini])
            ok("une vente de 0,50 € avec 1 € de frais : les 0,50 € qui manquent sortent de ta poche (61,50 € versés)",
               proche(fm["verse_eur"], 61.5) and proche(fm["encaisse_eur"], 0.0))
            _ecrire(JOURNAL, [achat1, achat2, vente])
            b1 = journal_compare()["bilan"]
            _ecrire(JOURNAL, [achat1, vente, achat2])
            j2 = journal_compare()
            b2 = j2["bilan"]
            ok("… et le même bilan : AAPL 0,2 × 352 / 1,10 = 64 € + 25 € en caisse = 89 € ; les 60 € versés, "
               "0,12 part de SPY → 76,68 € ; écart +12,32 €",
               b1 == b2 and b1["toi_eur"] == 89.0 and b1["verse_eur"] == 60.0 and b1["encaisse_eur"] == 25.0
               and b1["indice_eur"] == 76.68 and b1["ecart_eur"] == 12.32)
            # ── 4. Une valeur vendue en entier n'a plus besoin de son cours (MSFT n'en a pas ici).
            dm = next(x for x in j2["decisions"] if x["sens"] == "achat" and x["ticker"] == "MSFT")
            ok("MSFT vendue en entier, sans cours du jour : le carnet reste lisible, son duel = les 65 € de la vente "
               "contre son jumeau vendu 0,12 × 640 / 1,20 = 64 € (+1,00 €)",
               (dm["valeur_choix_eur"], dm["valeur_indice_eur"], dm["ecart_eur"]) == (65.0, 64.0, 1.0))

            # ── Les arrondis : « X de positions et Y de ventes » tombe sur « Ton portefeuille vaut Z ».
            maintenant["MSFT"] = 132.01
            _ecrire(JOURNAL, [achat1, {**vente, "date": "2026-03-04T16:00:00", "quantite": 0.1,
                                       "montant_eur": 9.86227947}])
            b = journal_compare()["bilan"]
            ok(f"0,4 × 132,01 / 1,10 = 48,0036 € et 9,8623 € de vente : {b['positions_eur']} + {b['encaisse_eur']} "
               f"= {b['toi_eur']} € (et non 57,87 €)",
               b["toi_eur"] == 57.86 and b["toi_eur"] == round(b["positions_eur"] + b["encaisse_eur"], 2))

            # ── 3. « Ta position » garde le résultat des ventes (choix de Mathis du 17/11).
            #    0,5 MSFT pour 60 € + 1 €, puis 0,25 vendue 35 € − 1 € ; aujourd'hui 165 $ et 1 € = 1,10 $ (150 €).
            maintenant["MSFT"] = 165.0
            _ecrire(JOURNAL, [achat1, {**vente, "date": "2026-03-04T16:00:00", "quantite": 0.25,
                                       "prix_eur": 140.0, "montant_eur": 35.0}])
            t = position()["total"]
            ok("après une vente : 37,50 € d'actions + 34 € en caisse − 61 € versés = +10,50 € (+17,21 %), soit "
               "+7,00 € sur ce qui reste et +3,50 € sur la vente (35 − 1 − 30,50)",
               proche(t["valeur_eur"], 37.5) and proche(t["gain_eur"], 10.5) and proche(t["gain_pct"], 10.5 / 61)
               and proche(t["ventes_eur"], 3.5) and t["nombre_ventes"] == 1)
            ok("… sans les frais : 37,50 + 35 − 60 = +12,50 € (+20,83 %), dont +5,00 € sur la vente",
               proche(t["gain_sans_frais_eur"], 12.5) and proche(t["gain_sans_frais_pct"], 12.5 / 60)
               and proche(t["ventes_sans_frais_eur"], 5.0))

            # ── L'action offerte compte à 0 € dans le total, et garde son prix du jour du cadeau sur sa ligne.
            maintenant.update({"TSLA": 330.0, "MSFT": 132.0})
            prime = {"date": "2026-03-02T15:00:00", "sens": "achat", "ticker": "TSLA", "quantite": 0.03,
                     "prix_eur": 300.0, "montant_eur": 9.0, "frais_eur": 0.0, "raison": "Offerte", "offert": True}
            _ecrire(JOURNAL, [prime, {**achat1, "date": "2026-03-03T15:45:00"}])
            p = position()
            t, tsla = p["total"], next(x for x in p["lignes"] if x["ticker"] == "TSLA")
            ok("une action offerte de 9 € et 60 € + 1 € de MSFT : 69 € d'actions − 61 € versés = +8,00 € "
               "(dont +9,00 € d'action offerte) ; +9,00 € sans les frais",
               proche(t["valeur_eur"], 69.0) and proche(t["gain_eur"], 8.0) and proche(t["offert_eur"], 9.0)
               and proche(t["verse_eur"], 61.0) and proche(t["gain_sans_frais_eur"], 9.0))
            ok("… sa ligne compte depuis son prix du jour du cadeau, comme le courtier : 0,03 × 300 € = 9 €, +0,00 €",
               proche(tsla["valeur_eur"], 9.0) and proche(tsla["gain_eur"], 0.0) and proche(tsla["prix_achat_eur"], 300.0))
            bo = journal_compare()["bilan"]
            ok("… et le bilan la place dans le S&P 500 comme un achat de 9 €, en le disant : 69 € dont 9 € offerts",
               bo["verse_eur"] == 69.0 and bo["offert_eur"] == 9.0)

            # ── 2. Les dates des décisions : une vente se contrôle à SA date ; rien dans le futur.
            cours_a = lambda _symbole, _quand: 1.10
            _ecrire(JOURNAL, [{**achat1, "date": "2026-03-03T15:45:00"}])
            base = {"sens": "vente", "ticker": "MSFT", "quantite": 0.1, "prix_eur": 130.0, "montant_eur": 13.0,
                    "frais_eur": 1.0, "raison": "Essai"}
            ok("une vente datée d'avant l'achat est refusée, et rien n'est écrit",
               refus(lambda: ajouter_decision({**base, "date": "2026-03-02T16:00:00"}), "à cette date")
               and len(lire_journal()) == 1)
            ok("une vente de plus que ce qui était détenu à sa date est refusée",
               refus(lambda: ajouter_decision({**base, "date": "2026-03-04T16:00:00", "quantite": 0.6}), "à cette date"))
            ajouter_decision({**base, "date": "2026-03-04T16:00:00", "quantite": 0.3, "montant_eur": 39.0})
            ok("une vente antidatée qui rendrait impossible une vente déjà notée est refusée (0,5 − 0,3 < 0,3)",
               refus(lambda: ajouter_decision({**base, "date": "2026-03-03T16:00:00", "quantite": 0.3}),
                     "rendrait impossible") and len(lire_journal()) == 2)
            demain = (datetime.now(PARIS) + timedelta(days=1)).replace(tzinfo=None).isoformat(timespec="seconds")
            ok("une décision datée de demain est refusée",
               refus(lambda: ajouter_decision({**base, "sens": "achat", "date": demain}), "futur"))
            ok("une date illisible est refusée avec une phrase en français",
               refus(lambda: ajouter_decision({**base, "sens": "achat", "date": "hier"}), "date illisible"))
            sans_montant = {k: v for k, v in base.items() if k != "montant_eur"}
            d = ajouter_decision({**sans_montant, "date": "2026-03-05T16:00:00", "prix_eur": 123.456})
            ok("une vente notée sans montant est gardée au centime : 0,1 × 123,456 = 12,35 €", d["montant_eur"] == 12.35)
            d = ajouter_decision({**base, "sens": "achat", "date": "2026-03-05T16:05:00", "offert": True})
            ok("un achat « offert » est gardé sans frais, avec sa marque", d["frais_eur"] == 0.0 and d["offert"] is True)
            d = ajouter_decision({**base, "date": "2026-03-05T16:10:00", "offert": True})
            ok("… une vente ne peut pas être « offerte »", "offert" not in d and d["frais_eur"] == 1.0)

            # ── Le repli de Yahoo (la clôture du jour) n'est plus gardé jusqu'au redémarrage.
            moment = datetime(2026, 3, 2, 15, 0, tzinfo=timezone.utc)
            etat, appels = {"barre": None}, []

            def faux_historique(_symbole, **k):
                appels.append(k.get("interval"))
                if k.get("interval") == "5m":
                    if etat["barre"] is None:
                        raise ValueError("Yahoo n'a rien rendu")
                    return pd.Series([etat["barre"]], index=pd.DatetimeIndex([moment - timedelta(minutes=2)]))
                return pd.Series([100.0], index=pd.DatetimeIndex([moment.replace(hour=0)]))

            _historique = faux_historique
            v1 = cours_a_reel("SPY", moment)
            v2 = cours_a_reel("SPY", moment)
            ok("Yahoo sans barre de 5 min : le repli (clôture du jour, 100) sert, et se garde 10 minutes",
               v1 == 100.0 and v2 == 100.0 and appels == ["5m", "1d"])
            _memo_repli[("SPY", moment.isoformat())] = (time.monotonic() - REPLI_S - 1, 100.0)
            etat["barre"] = 105.0
            v3, v4 = cours_a_reel("SPY", moment), cours_a_reel("SPY", moment)
            ok("… après 10 minutes, la vraie barre revenue (105) remplace le repli, puis se garde",
               v3 == 105.0 and v4 == 105.0 and appels == ["5m", "1d", "5m"])
        finally:
            JOURNAL, CARNET, cours_a, cours_actuel, _historique = reel
            _memo_passe.clear()
            _memo_repli.clear()
            _disque_passe["chemin"] = None


def main():
    parser = argparse.ArgumentParser(description="Ma position et le journal de mes décisions.")
    parser.add_argument("--verifier", action="store_true", help="les cas calculés à la main")
    args = parser.parse_args()
    if args.verifier:
        sys.exit(0 if verifier() else 1)
    print(json.dumps({"position": position(), "journal": journal_compare()},
                     indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
