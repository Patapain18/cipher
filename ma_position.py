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


def agreger(journal):
    """
    Les lignes détenues, à partir du journal.

    Le prix de revient suit la règle française du prix moyen pondéré : une
    vente retire sa part du coût, au prorata des actions vendues. Le coût
    compte les frais d'achat, puisque c'est l'argent vraiment sorti.
    """
    lignes = {}
    for d in sorted(journal, key=lambda x: x["date"]):
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
MEMO_ACTUEL_S = 30


def cours_actuel(symbole):
    """Le dernier cours connu et son heure : le marché peut être fermé."""
    deja = _memo_actuel.get(symbole)
    if deja and time.monotonic() - deja[0] < MEMO_ACTUEL_S:
        return deja[1], deja[2]
    c = _historique(symbole, period="5d", interval="1m", prepost=True)
    cours, heure = float(c.iloc[-1]), c.index[-1]
    _memo_actuel[symbole] = (time.monotonic(), cours, heure)
    return cours, heure


def cours_a(symbole, quand_utc):
    """
    Le cours d'un symbole à un moment passé : les barres de 5 minutes du
    jour (Yahoo les garde 60 jours), sinon la clôture du jour.
    """
    cle = (symbole, quand_utc.isoformat())
    if cle in _memo_passe:
        return _memo_passe[cle]
    jour = quand_utc.date()
    v = float("nan")
    try:
        c = _historique(symbole, start=jour - timedelta(days=4), end=jour + timedelta(days=1),
                        interval="5m", prepost=True)
        v = float(c.asof(quand_utc))
    except ValueError:
        pass
    if v != v:
        c = _historique(symbole, start=jour - timedelta(days=10), end=jour + timedelta(days=1),
                        interval="1d")
        v = float(c.asof(quand_utc))
    # Gardé seulement s'il date de plus de 10 minutes : la barre de
    # 5 minutes en cours peut encore bouger.
    if datetime.now(timezone.utc) - quand_utc > timedelta(minutes=10):
        _memo_passe[cle] = v
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
    raison = " ".join(str(payload.get("raison", "")).split())[:200]
    date = str(payload.get("date") or datetime.now().isoformat(timespec="seconds"))
    quand = en_utc(date)
    prix_usd = prix_eur * cours_a(CHANGE, quand)

    with _verrou:
        journal = lire_journal()
        if sens == "vente":
            detenue = next((l for l in agreger(journal) if l["ticker"] == ticker), None)
            if not detenue or quantite > detenue["quantite"] + 1e-9:
                raise ValueError(f"tu ne détiens pas autant de {ticker}")
        decision = {"date": date, "sens": sens, "ticker": ticker, "quantite": quantite,
                    "prix_eur": prix_eur, "prix_usd": round(prix_usd, 4),
                    "montant_eur": montant_eur, "frais_eur": frais_eur, "raison": raison}
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
    """
    eurusd, _ = cours_actuel(CHANGE)
    lignes, valeur, cout = [], 0.0, 0.0
    for l in agreger(lire_journal()):
        prix_usd, heure = cours_actuel(l["ticker"])
        c = calcul_ligne(l["quantite"], l["cout_eur"], l["montant_eur"], prix_usd, eurusd)
        rang, classees = rang_de(l["ticker"]) if rang_de else (None, None)
        lignes.append({**l, **c, "cours_a": heure.isoformat(), "rang": rang, "classees": classees,
                       "suivie_par_le_bot": l["ticker"] in engine.UNIVERSE,
                       "resultats": engine.prochaine_publication(l["ticker"])})
        valeur += c["valeur_eur"]
        cout += l["cout_eur"]
    return {
        "maj": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "eurusd": eurusd,
        "lignes": lignes,
        "total": {"valeur_eur": valeur, "cout_eur": cout, "gain_eur": valeur - cout,
                  "gain_pct": (valeur / cout - 1) if cout else None},
        "frais_vente_eur": FRAIS_VENTE,
        "avertissement": AVERTISSEMENT,
    }


def journal_compare(prix_actuels=None, fx_actuel=None):
    """
    Chaque décision, et pour chaque achat ce qu'aurait fait la même somme
    dans le S&P 500. `prix_actuels` ({ticker: cours en $}) et `fx_actuel`
    sont les cours de la position déjà affichée : le journal compte avec
    les mêmes, sinon l'écran montre deux valeurs pour la même action.
    """
    journal = sorted(lire_journal(), key=lambda d: d["date"])
    valeurs = valeurs_du_journal()    # pour la liste du formulaire
    if not journal:
        return {"decisions": [], "valeurs": valeurs, "avertissement": AVERTISSEMENT}
    indice_maintenant, _ = cours_actuel(INDICE)
    fx_maintenant = fx_actuel or cours_actuel(CHANGE)[0]
    prix_maintenant = dict(prix_actuels or {})
    sortie = []
    for d in journal:
        ligne = dict(d)
        if d["sens"] == "achat":
            quand = en_utc(d["date"])
            if d["ticker"] not in prix_maintenant:
                prix_maintenant[d["ticker"]] = cours_actuel(d["ticker"])[0]
            valeur_choix = d["quantite"] * prix_maintenant[d["ticker"]] / fx_maintenant
            valeur_indice = calcul_comparaison(d["montant_eur"], cours_a(INDICE, quand),
                                               indice_maintenant, cours_a(CHANGE, quand), fx_maintenant)
            ligne.update({"valeur_choix_eur": valeur_choix, "valeur_indice_eur": valeur_indice,
                          "ecart_eur": valeur_choix - valeur_indice})
        sortie.append(ligne)
    return {"decisions": sortie, "indice": "S&P 500 (SPY)", "valeurs": valeurs,
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

    print(f"\n{sum(cas)}/{len(cas)} vérifications réussies")
    return all(cas)


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
