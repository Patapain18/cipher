"""
Serveur du dashboard actions — Salle de lecture.

CE QUE FAIT CE SERVEUR
    Il lit. C'est tout.

    Il sert la page web, et il expose quelques routes JSON qui vont
    chercher quatre choses : les cours du moment, les signaux calculés
    par le moteur v2, l'historique déjà écrit sur le disque, et la
    fiabilité mesurée de ces signaux.

CENTRÉ SUR LES ACTIONS
    Les bots crypto (bot.py, grid_bot.py) restent dans le projet et se
    lancent à la main, mais ils ne sont plus branchés ici : un outil
    qui suit une seule chose correctement vaut mieux que deux à
    moitié.

CE QU'IL NE FAIT PAS — ET C'EST VOULU
    Il ne passe aucun ordre, et n'utilise aucune clé d'API. Le bot
    actions ne sait de toute façon pas exécuter : il signale, et les
    ordres se passent à la main chez le courtier.

    Le serveur écoute sur 127.0.0.1 uniquement : il n'est pas
    joignable depuis le réseau, seulement depuis ce Mac. Et il ne
    répond qu'aux noms localhost et 127.0.0.1 (voir HOTES_AUTORISES).

LANCEMENT
    python3 web/server.py          (depuis le dossier trading-bot)
    puis http://localhost:8770
"""

import json
import math
import os
import sys
import time
import threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

# Les modules du bot vivent dans le dossier parent (trading-bot/).
# On l'ajoute au chemin d'import pour pouvoir réutiliser TON code
# d'analyse au lieu d'en réécrire une deuxième version qui finirait
# fatalement par diverger de la première.
# ── Où vit le projet ──────────────────────────────────────────────
#
#  Ce serveur lit et écrit `data/` — historique des signaux, positions,
#  rapports d'évaluation — que le bot alimente en parallèle. Ces
#  fichiers vivent dans le projet, jamais ailleurs : c'est pour ça que
#  le dossier se déduit de l'emplacement de ce fichier et non du
#  répertoire courant, qui dépend de l'endroit d'où on lance.
#
#  L'application « Signaux actions » (web/dashboard_app.py) ne fait pas
#  exception : elle ne recopie pas ce code, elle lance CE fichier avec
#  le projet pour dossier de travail. Une copie embarquée aurait fini
#  par diverger, et surtout elle aurait cherché les données à
#  l'intérieur du bundle — un tableau vide, sans la moindre erreur.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_BASE = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, ROOT)
os.chdir(ROOT)  # pour que "data/..." et "logs/..." pointent au bon endroit

import warnings
warnings.filterwarnings("ignore")

STATIC = os.path.join(STATIC_BASE, "static")
VERSION = "1.0.0"
PORT = int(os.getenv("DASHBOARD_PORT", "8770"))

# Les seuls noms sous lesquels la page s'ouvre : l'application, le Hub
# et le Terminal disent « localhost », les outils de capture
# « 127.0.0.1 ». Tout autre nom est refusé, voir Handler._hote_refuse.
HOTES_AUTORISES = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}


# ══════════════════════════════════════════════════════════════════
#  CACHE
#  yfinance et Binance sont des services publics et gratuits : on
#  évite de les marteler. Chaque réponse est gardée quelques minutes.
#  Sans ça, ouvrir trois onglets du dashboard = trois séries d'appels
#  identiques, et un risque de se faire limiter par le fournisseur.
# ══════════════════════════════════════════════════════════════════

_cache = {}
_cache_lock = threading.Lock()
# Monte à chaque écriture du journal : un calcul commencé avant ne se range
# plus dans le cache après elle (voir `cached`).
_cache_generation = [0]


# ── Une réponse toujours lisible par la page ─────────────────────
#  json.dumps écrit un nombre non fini « NaN » ou « Infinity », que
#  JSON.parse refuse : toute la réponse deviendrait illisible pour la
#  page (le défi papier ou le carnet afficheraient une erreur). Un tel
#  nombre part donc en null, que la page sait montrer (« — »).
def _sans_nan(valeur):
    if isinstance(valeur, float) and not math.isfinite(valeur):
        return None
    if isinstance(valeur, dict):
        return {k: _sans_nan(v) for k, v in valeur.items()}
    if isinstance(valeur, (list, tuple)):
        return [_sans_nan(v) for v in valeur]
    return valeur


def encoder_json(body):
    """Le corps d'une réponse, en JSON valide (sans NaN), en octets."""
    try:
        return json.dumps(body, default=str, allow_nan=False).encode()
    except ValueError:      # un NaN quelque part : on ne le cherche que dans ce cas
        return json.dumps(_sans_nan(body), default=str).encode()


def cached(key, ttl_seconds, producer):
    """Renvoie la valeur en cache si elle est fraîche, sinon la recalcule."""
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry["at"]) < ttl_seconds:
            return entry["value"], entry["at"]
        generation = _cache_generation[0]

    value = producer()  # calculé HORS du verrou : un appel réseau lent
                        # ne doit pas bloquer les autres routes.
    with _cache_lock:
        # Une décision notée PENDANT ce calcul l'a rendu périmé : il sert à
        # cette réponse, mais ne se range pas. Sinon l'ancien total revenait
        # pendant 60 s après une vente (relecture du 17/11/2026).
        if _cache_generation[0] == generation:
            _cache[key] = {"value": value, "at": time.time()}
    return value, time.time()


# ══════════════════════════════════════════════════════════════════
#  ACTIONS  (stock_bot.py)
# ══════════════════════════════════════════════════════════════════

def stock_engine_sector(ticker):
    import stock_engine
    return stock_engine.sector_of(ticker)


def scan_stocks():
    """
    Rejoue exactement l'analyse de stock_bot.py sur chaque ticker.

    On instancie ta classe `StockSignalBot` et on appelle sa méthode
    `analyze` : les scores affichés ici sont donc, à la virgule près,
    ceux que le bot afficherait dans son terminal — moteur v2 compris.
    """
    import stock_bot

    bot = stock_bot.StockSignalBot(
        tickers=stock_bot.DEFAULT_TICKERS,
        capital=stock_bot.CAPITAL,
        dashboard=False,
    )

    # Une seule requête groupée pour tout l'univers : voir fetch_many.
    frames = bot.fetch_many(bot.tickers)

    rows = []
    for ticker in bot.tickers:
        df = frames.get(ticker)
        if df is None or df.empty:
            rows.append({"ticker": ticker, "error": "données indisponibles"})
            continue
        try:
            analysis = bot.analyze(ticker, df)
            analysis["secteur"] = stock_engine_sector(ticker)
            # Simple lecture du cache alimenté par le bot : aucun appel
            # réseau ici (voir stock_engine.rafraichir_earnings).
            import stock_engine
            analysis["resultats_dans"] = stock_engine.jours_avant_publication(ticker)
            analysis["resultats_le"] = stock_engine.prochaine_publication(ticker)
            # La position éventuellement détenue, pour afficher le P&L.
            # Le P&L passe par stock_engine.pnl_position : les cours sont
            # ajustés des divisions d'actions, le prix saisi ne l'est pas.
            pos = bot.positions.get(ticker)
            if pos:
                import stock_engine
                calcul = stock_engine.pnl_position(pos, analysis["price"], ticker)
                if calcul:
                    analysis["position"] = calcul
            rows.append(analysis)
        except Exception as e:
            rows.append({"ticker": ticker, "error": str(e)})

    # --- Classement du jour, par momentum 12-1 ---
    #
    # POURQUOI CE CRITÈRE ET PAS LE SCORE DU MOTEUR
    #     Les trois candidats ont été mis en concurrence sur 9 ans et 49
    #     valeurs (stock_eval.py --ranking --large). Résultat sans appel :
    #       — score calibré : sous le hasard (18ᵉ-64ᵉ percentile)
    #       — note brute du moteur : indistincte du hasard (72ᵉ-84ᵉ)
    #       — momentum 12-1 : 100ᵉ percentile aux trois tailles testées,
    #         et le seul à battre le panier équipondéré à la fois en
    #         performance, en Sharpe et en drawdown.
    #
    #     Classer par le score du moteur reviendrait donc à ordonner les
    #     valeurs avec une mesure dont on a montré qu'elle n'ordonne
    #     rien. On classe par ce qui a fait ses preuves, même si ce n'est
    #     pas ce que le moteur produit.
    #
    #     Réserve à garder en tête : cette démonstration vaut sur un
    #     univers large et diversifié. Sur les neuf valeurs suivies ici,
    #     presque toutes du même secteur, aucun critère n'a battu le
    #     panier en risque-ajusté — le rang départage, il ne décide pas.
    ranked = sorted(
        [r for r in rows if r.get("mom_12_1") is not None],
        key=lambda r: r["mom_12_1"], reverse=True,
    )
    for position, row in enumerate(ranked, start=1):
        row["rang"] = position

    return {
        "rows": rows,
        "capital": bot.capital,
        "classes": len(ranked),
        "scanned_at": datetime.now().isoformat(),
    }


def stocks_history():
    """
    Relit data/signal_history.json et le réorganise par ticker.

    Le fichier est une liste plate d'événements ; le graphique, lui, a
    besoin d'une courbe par valeur. C'est cette transposition qu'on fait
    ici plutôt que dans le navigateur : le serveur a déjà les données
    sous la main, autant lui laisser le travail.
    """
    path = os.path.join("data", "signal_history.json")
    if not os.path.exists(path):
        return {"series": {}, "count": 0}

    with open(path) as f:
        raw = json.load(f)

    # Les « bascules » (`is_change`) ne sont plus lues ici : leur section a
    # laissé la place au verdict du banc des prévisions (20/11/2026). Le bot
    # continue de les noter dans le fichier.
    series = {}
    for entry in raw:
        ticker = entry.get("ticker")
        if not ticker:
            continue
        series.setdefault(ticker, []).append({
            "t": entry.get("timestamp"),
            "price": entry.get("price"),
            "buy": entry.get("buy_score"),
            "sell": entry.get("sell_score"),
            "rsi": entry.get("rsi"),
            "reco": entry.get("recommendation"),
        })

    return {
        "series": series,
        "count": len(raw),
        "from": raw[0].get("timestamp") if raw else None,
        "to": raw[-1].get("timestamp") if raw else None,
    }


def signal_quality():
    """
    Fiabilité mesurée du score, produite par stock_eval.py --save.

    C'est l'information la plus importante du dashboard après les
    scores eux-mêmes. Un signal affiché sans son taux de réussite
    connu invite à lui accorder une confiance qu'il n'a peut-être pas
    méritée ; les mesures sont là pour que la question « à quel point
    est-ce que j'y crois ? » ait une réponse chiffrée sous les yeux.

    Renvoie None tant que l'évaluation n'a jamais été lancée — le
    dashboard affiche alors comment la produire, plutôt que d'inventer
    un chiffre rassurant.
    """
    path = os.path.join("data", "eval_report.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def ranking_quality():
    """
    Résultats de la mise en concurrence des critères de classement,
    produits par stock_eval.py --ranking --save.

    Sert la page d'explications : quand on avance des chiffres pour
    justifier une décision, ils doivent venir de la dernière mesure et
    porter sa date, pas d'une valeur recopiée dans du HTML.
    """
    path = os.path.join("data", "ranking_report.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


# ══════════════════════════════════════════════════════════════════
#  ÉTAT GÉNÉRAL
# ══════════════════════════════════════════════════════════════════

def bots_status():
    """
    Qui tourne, sur quoi, et depuis quand.

    Recentré sur les actions : les garde-fous DRY_RUN / TESTNET ne sont
    plus remontés ici parce qu'ils ne concernent que les bots crypto.
    Le bot actions, lui, ne passe aucun ordre par construction — c'est
    ce que dit le badge affiché en permanence en haut de page.
    """
    import stock_bot

    def process_alive(pid_file):
        path = os.path.join("logs", pid_file)
        if not os.path.exists(path):
            return None
        try:
            pid = int(open(path).read().strip())
        except (ValueError, OSError):
            return None
        try:
            os.kill(pid, 0)  # signal 0 = "es-tu vivant ?", ne tue rien
            return {"pid": pid, "running": True}
        except OSError:
            return {"pid": pid, "running": False}

    def last_write(*paths):
        stamps = [os.path.getmtime(p) for p in paths if os.path.exists(p)]
        return datetime.fromtimestamp(max(stamps)).isoformat() if stamps else None

    return {
        "moteur": "v2",
        "seuil_achat": 4,
        "tickers": stock_bot.DEFAULT_TICKERS,
        "capital": stock_bot.CAPITAL,
        "stock_bot": {
            "process": process_alive("stock_bot.pid"),
            "last_activity": last_write(
                os.path.join("logs", "stock_bot_live.log"),
                os.path.join("data", "signal_history.json"),
            ),
        },
        "server_time": datetime.now().isoformat(),
    }


# ══════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════

_thermo_verrou = threading.Lock()
_thermo_recent = {"valeur": None, "quand": 0.0}


def thermometre_du_marche():
    """
    Le thermomètre du marché (thermometre.py) : la lecture du jour, et sa
    mesure honnête sur le passé (data/thermometre.json, écrit par
    `python3 thermometre.py --mesurer`). Un thermomètre, pas une boule de
    cristal : jamais de date de chute, jamais de conseil.

    Deux requêtes en même temps (le rafraîchissement de la page et un
    retour de focus) ne font pas deux fois les mêmes demandes à Yahoo :
    la seconde attend la première et reprend son résultat (relu le
    15/11/2026).
    """
    import thermometre
    with _thermo_verrou:
        if _thermo_recent["valeur"] is not None and time.time() - _thermo_recent["quand"] < 120:
            return _thermo_recent["valeur"]
        valeur = thermometre.thermometre()
        _thermo_recent.update(valeur=valeur, quand=time.time())
        return valeur


_creux_verrou = threading.Lock()
_creux_recent = {"valeur": None, "quand": 0.0}


def creux_des_indices():
    """
    Le creux du marché (creux.py) : de combien le S&P 500 et le Nasdaq
    Composite sont sous leur record, et les baisses de 20 % ou plus du
    passé. Des faits du passé : jamais de date de fin, jamais de conseil.

    Même forme que le thermomètre : deux requêtes en même temps ne lisent
    Yahoo qu'une fois. Un indice que Yahoo refuse garde sa dernière bonne
    lecture ; sans aucune lecture, l'erreur remonte et `cached` ne la garde
    pas (la page réessaie au rafraîchissement suivant).
    """
    import creux
    with _creux_verrou:
        if _creux_recent["valeur"] is not None and time.time() - _creux_recent["quand"] < 120:
            return _creux_recent["valeur"]
        valeur = creux.creux_du_marche()
        _creux_recent.update(valeur=valeur, quand=time.time())
        return valeur


def mur_de_la_seance():
    """
    Le mur des frais pendant la séance (seance.py) : la mesure écrite à la
    main par `python3 seance.py --mesurer`, RELUE ici sans jamais lire Yahoo.
    Une mesure absente ou abîmée arrive avec son état (« absent »,
    « illisible ») et son message en français, sans faire tomber la route.
    Tous les textes viennent de seance.py : la page ne fait que les insérer.
    """
    import seance
    return seance.section()


def point_de_la_semaine():
    """
    Le dernier point de la semaine, préparé par le bot chaque dimanche à
    19 h (point_semaine.py). None tant qu'il n'y en a pas encore : la page
    garde alors la section cachée.
    """
    path = os.path.join("data", "point_semaine.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


# ══════════════════════════════════════════════════════════════════
#  LE BANC DES PRÉVISIONS  (banc.py --mesurer, puis --critere2)
#  Deux mesures faites UNE seule fois, écrites dans data/banc/ et jamais
#  réécrites. Le serveur les LIT : il ne recalcule rien, n'écrit rien, et
#  n'importe pas le code du banc. Un fichier absent ou abîmé ne fait rien
#  tomber : sa partie arrive avec son état (« absent » ou « illisible »),
#  et la page le dit.
# ══════════════════════════════════════════════════════════════════

BANC_CRITERE1 = os.path.join("data", "banc", "etape1.json")
BANC_CRITERE2 = os.path.join("data", "banc", "critere2.json")

# Les méthodes du banc, figées avant de regarder, avec les noms du rapport.
# « comme hier » est un repère : il ne concourt pas.
BANC_METHODES = (
    ("a", "le score technique"),
    ("b", "le momentum 12-1"),
    ("c", "la régression logistique"),
    ("d", "le gradient boosting"),
    ("hier", "« comme hier »"),
)


def _fini(valeur):
    """Un nombre fini, sinon ValueError (un booléen n'est pas un nombre)."""
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)) or not math.isfinite(valeur):
        raise ValueError(f"nombre attendu, reçu {valeur!r}")
    return float(valeur)


def _entier(valeur):
    """Un entier, sinon ValueError (ni texte, ni booléen, ni nombre à virgule)."""
    if isinstance(valeur, bool) or not isinstance(valeur, int):
        raise ValueError(f"entier attendu, reçu {valeur!r}")
    return valeur


def _jour(valeur):
    """Une date « AAAA-MM-JJ » écrite en entier (pas « 2020-9-4 »), sinon ValueError."""
    if not isinstance(valeur, str) or datetime.strptime(valeur, "%Y-%m-%d").strftime("%Y-%m-%d") != valeur:
        raise ValueError(f"date attendue (AAAA-MM-JJ), reçu {valeur!r}")
    return valeur


def _horodatage(valeur):
    """L'heure d'une mesure, avec son fuseau (« 2026-11-20T17:06:45+01:00 »), sinon ValueError."""
    # Le « T » au milieu : Python accepte n'importe quel séparateur, pas le
    # navigateur, qui écrirait alors « — » à la place de l'heure.
    if not isinstance(valeur, str) or valeur[10:11] != "T" or datetime.fromisoformat(valeur).tzinfo is None:
        raise ValueError(f"heure de mesure attendue, avec son fuseau, reçu {valeur!r}")
    return valeur


def _vraie_mesure(brut):
    """Refuse ce qui n'est pas LA mesure : un essai à blanc, ou du code modifié."""
    if brut["resultat"].get("a_blanc") is not False or brut["code"].get("modifie") is not False:
        raise ValueError("ce n'est pas une mesure faite sur du code commité")


def _banc_critere1(brut):
    """Le critère 1 (data/banc/etape1.json) : bonnes réponses, barres, porte A."""
    _vraie_mesure(brut)
    r = brut["resultat"]
    tout = r["resultats"]["tout"]
    monte = _fini(tout["a"]["monte"])
    methodes = []
    for cle, nom in BANC_METHODES:
        m = tout[cle]
        tirage, face_monte = m["face"]["tirage"], m["face"]["monte"]
        if _fini(m["monte"]) != monte:
            raise ValueError("« ça monte toujours » n'a pas le même taux pour toutes les méthodes")
        methodes.append({
            "cle": cle, "nom": nom, "candidate": cle != "hier",
            "bonnes": _fini(m["bonnes"]), "barre": _fini(m["barre"]), "passe": m["passe"] is True,
            "tirage": _fini(m["tirage"]),
            "ecart_tirage": _fini(tirage["ecart"]), "seuil_tirage": _fini(tirage["seuil"]),
            "vu_tirage": tirage["vu"] is True,
            "ecart_monte": _fini(face_monte["ecart"]), "seuil_monte": _fini(face_monte["seuil"]),
            "vu_monte": face_monte["vu"] is True,
        })
    passent = list(r["passent"])
    if sorted(passent) != sorted(m["cle"] for m in methodes if m["candidate"] and m["passe"]):
        raise ValueError("la liste des méthodes qui passent ne suit pas leurs résultats")
    # La porte A s'ouvre si, et seulement si, une candidate passe (règle du plan).
    if not isinstance(r["porte_a"], bool) or r["porte_a"] != bool(passent):
        raise ValueError("la porte A ne suit pas la liste des méthodes qui passent")
    return {
        "date_mesure": _horodatage(brut["date_mesure"]), "commit": str(brut["code"]["commit"])[:7],
        "debut": _jour(r["debut"]), "fin": _jour(r["fin"]),
        "seances": _entier(r["seances"]), "valeurs": _entier(r["valeurs"]), "valeurs_jours": _entier(r["valeurs_jours"]),
        "niveau": _fini(r["reglages"]["niveau"]),
        "monte": monte, "porte_a": r["porte_a"] is True, "passent": passent,
        "methodes": methodes,
    }


def _banc_critere2(brut):
    """Le critère 2 (data/banc/critere2.json) : la stratégie de 200 €, le soir et le matin."""
    _vraie_mesure(brut)
    r = brut["resultat"]
    reglages = r["reglages"]
    heures = {}
    for heure in ("soir", "matin"):
        # La mesure principale : l'écart du courtier à 0,1 % (le contrôle à
        # 0,3 % ne décide rien).
        res = r["resultats"][f"{heure}-{reglages['ecart']}"]
        methodes = {}
        for cle, _nom in BANC_METHODES:
            m = res["methodes"][cle]
            methodes[cle] = {
                "final": _fini(m["final"]), "passe": m["passe_ici"] is True,
                "arret": _jour(m["arret"]) if m["arret"] is not None else None,
                "capital_arret": _fini(m["capital_arret"]) if m["capital_arret"] is not None else None,
                "ordres": _entier(m["ordres"]),
                "frais": _fini(m["frais"]) + _fini(m["ecart"]),   # 1 € par ordre, plus l'écart
            }
        if _fini(res["barre"]) != max(_fini(res["spy"]), _fini(res["panier"])):
            raise ValueError("la barre n'est pas le meilleur de SPY et du panier gardés")
        heures[heure] = {
            "spy": _fini(res["spy"]), "panier": _fini(res["panier"]), "barre": _fini(res["barre"]),
            "dernier_execute": _jour(r["dernier_execute"][heure]),
            "methodes": methodes,
        }
    soir = r["resultats"][f"soir-{reglages['ecart']}"]
    gardees = {v: _fini(x) for v, x in soir["valeurs"].items()}      # chaque valeur gardée seule
    ordre = sorted(gardees, key=gardees.get)
    survivant = {}
    for cle, s in soir["survivant"].items():
        if s["longues"]:
            valeur, soirs, rendement = s["longues"][0]
            survivant[cle] = {"valeur": str(valeur), "soirs": _entier(soirs), "rendement": _fini(rendement),
                              "gardee_seule": gardees.get(valeur)}
    passent = list(r["passent"])
    attendu = [cle for cle, _n in BANC_METHODES if cle != "hier"
               and all(heures[h]["methodes"][cle]["passe"] for h in heures)]
    if sorted(passent) != sorted(attendu):
        raise ValueError("la liste des méthodes qui passent ne suit pas leurs résultats")
    return {
        "date_mesure": _horodatage(brut["date_mesure"]), "commit": str(brut["code"]["commit"])[:7],
        "debut": _jour(r["debut"]),
        "capital": _fini(reglages["capital"]), "frais_ordre": _fini(reglages["frais"]),
        "ecart": _fini(reglages["ecart"]),
        "passent": passent, "heures": heures, "survivant": survivant,
        "gardees": {
            "nombre": len(ordre),
            "plus_bas": [ordre[0], gardees[ordre[0]]], "plus_haut": [ordre[-1], gardees[ordre[-1]]],
            "battent_panier": sum(1 for x in gardees.values() if x > heures["soir"]["panier"]),
        },
    }


def _lire_banc(chemin, extraire):
    """Une partie du verdict, avec son état : mesuré, absent ou illisible."""
    if not os.path.exists(chemin):
        return {"etat": "absent"}
    if not os.path.isfile(chemin):      # un dossier, ou un tube qui bloquerait open() pour toujours
        return {"etat": "illisible", "detail": "pas un fichier"}
    try:
        with open(chemin, encoding="utf-8") as f:
            brut = json.load(f)
        return {"etat": "mesure", **extraire(brut)}
    except Exception as e:      # un fichier abîmé : on le dit, sans rien faire tomber
        return {"etat": "illisible", "detail": type(e).__name__}


def verdict_du_banc():
    """
    Le verdict du banc des prévisions : ce que les deux mesures ont trouvé,
    en chiffres. La page en tire ses phrases (des faits sur le passé,
    jamais une prévision ni un conseil).
    """
    return {
        "critere1": _lire_banc(BANC_CRITERE1, _banc_critere1),
        "critere2": _lire_banc(BANC_CRITERE2, _banc_critere2),
    }


def rang_de(ticker):
    """
    Le rang d'une valeur au momentum, lu dans le dernier scan gardé en
    mémoire : la position ne relance pas un scan de 49 valeurs pour ça.
    """
    with _cache_lock:
        entree = _cache.get("/api/stocks") or _cache.get("/api/stocks:classement")
    if not entree:
        return None, None
    donnees = entree["value"]
    for r in donnees.get("rows", []):
        if r.get("ticker") == ticker:
            return r.get("rang"), donnees.get("classes")
    return None, donnees.get("classes")


def ma_position_en_direct():
    """
    Ma position (ma_position.py) : valeur, gain frais compris, seuils, rang,
    et l'état de la Bourse de New York lu chez Yahoo sur le S&P 500 (jours
    fériés et séances courtes compris ; None si Yahoo ne répond pas : la
    page reprend alors son propre calcul).
    """
    import ma_position
    import papier
    p = ma_position.position(rang_de)
    try:
        _, _, periode = papier.minute_de_seance(papier.INDICE)
        p["bourse"] = papier.etat_bourse(datetime.now(timezone.utc), periode)
    except Exception as e:
        print(f"[position] état de la Bourse : {e}", file=sys.stderr)
        p["bourse"] = None
    return p


def mon_journal():
    """
    Mes décisions, et pour chaque achat la même somme dans le S&P 500,
    comptées avec les cours de la position en cache : la page affiche
    alors la même valeur en haut et dans le journal.
    """
    import ma_position
    pos, _ = cached("/api/position", 60, ma_position_en_direct)
    prix = {l["ticker"]: l["prix_usd"] for l in pos["lignes"]}
    return ma_position.journal_compare(prix, pos["eurusd"])


# ══════════════════════════════════════════════════════════════════
#  LES DEUX ÉCRITURES DU SERVEUR : LE JOURNAL ET LE DÉFI PAPIER
# ══════════════════════════════════════════════════════════════════
#
#  Le serveur était jusqu'ici en lecture pure. Il écrit deux choses :
#    - le journal : l'ajout d'une décision déjà passée chez le courtier
#      (`ma_position.ajouter_decision`), qui tient aussi à jour
#      `data/stock_positions.json`, le carnet du bot ;
#    - le défi papier (`papier.passer_ordre`) : un ordre FICTIF, ajouté
#      à `data/papier.json` et à rien d'autre. Il ne touche ni le
#      journal ni le carnet du bot.
#  Aucun ordre réel n'est émis, aucune clé d'API n'entre en jeu.
#
#  Les anciennes routes /api/positions/add et /remove écrivaient le
#  carnet SANS le journal : retirées le 13/11/2026, quand le carnet de
#  bord (touche P) est devenu la seule façon de noter une décision.
#
#  PROTECTION CONTRE LES REQUÊTES D'AUTRES SITES
#      Un serveur qui écoute sur localhost reste joignable par n'importe
#      quelle page web ouverte dans ton navigateur : elle ne peut pas
#      lire la réponse, mais elle peut envoyer la requête, et cela
#      suffirait à polluer ton carnet de positions.
#
#      D'où l'en-tête `X-Dashboard` exigé ci-dessous. Une page tierce ne
#      peut pas l'ajouter sans déclencher au préalable une requête
#      OPTIONS de vérification — à laquelle ce serveur ne répond pas, ce
#      qui fait échouer la tentative. Le JavaScript de notre propre page,
#      lui, l'envoie sans difficulté puisqu'il est servi depuis la même
#      origine.
#
#      Le « DNS rebinding » contourne cette protection (vérifié le
#      05/11/2026) : une page piégée fait pointer son propre nom vers
#      127.0.0.1. Pour le navigateur, elle parle alors à son propre
#      site : elle lit nos réponses et ajoute l'en-tête sans rien
#      demander. Seul l'en-tête Host trahit le nom qu'elle a utilisé :
#      Handler._hote_refuse le vérifie sur TOUTES les requêtes,
#      lectures comprises.

def ajouter_au_journal(payload):
    """Note une décision déjà passée chez le courtier ; le carnet suit."""
    import ma_position
    decision = ma_position.ajouter_decision(payload)
    with _cache_lock:
        _cache_generation[0] += 1
        for cle in [k for k in _cache if k.startswith(("/api/position", "/api/journal",
                                                       "/api/graphique"))]:
            _cache.pop(cle, None)
    return decision


def mon_defi_papier():
    """Le défi papier (papier.py) : liquidités, positions, ordres, séance, tout en FICTIF."""
    import papier
    return papier.etat()


def ordre_papier(payload):
    """
    Un ordre FICTIF. Le cache de /api/stocks ne bouge pas (sinon chaque
    ordre relancerait le scan des 49 valeurs, et Yahoo refuse les
    rafales) ; /api/papier, lui, n'est jamais gardé en cache.
    """
    import papier
    return papier.passer_ordre(payload)


def recommencer_papier(payload):
    """
    Range le défi papier dans ses archives (data/papier-archives/), avec
    ses chiffres, et repart d'un défi neuf. Le corps de la requête ({})
    ne porte rien : la page demande d'abord une confirmation.
    """
    import papier
    return papier.recommencer()


# Le graphique dépend de sa période : une minute pour la journée, plus
# longtemps pour le mois et l'année, qui ne bougent qu'une fois par barre.
GRAPHIQUE_CACHE = {"1j": 60, "1m": 600, "1a": 3600}


ROUTES = {
    # chemin              → (fonction, durée de cache en secondes)
    "/api/semaine":        (point_de_la_semaine, 30),
    "/api/thermometre":    (thermometre_du_marche, 3600),   # des cours de clôture : une heure suffit
    "/api/creux":          (creux_des_indices, 3600),       # des cours de clôture aussi
    "/api/banc":           (verdict_du_banc, 300),          # deux fichiers écrits une fois, jamais réécrits
    "/api/seance":         (mur_de_la_seance, 300),         # data/seance.json, réécrit à la main par seance.py --mesurer
    "/api/position":       (ma_position_en_direct, 60),
    "/api/journal":        (mon_journal, 0),      # recalculé à chaque fois, sur la position en cache (60 s)
    "/api/papier":         (mon_defi_papier, 0),  # recalculé à chaque fois : papier.py garde déjà les cours 30 s.
                                                  # Un cache ici pouvait ranger, après un ordre, un état lu avant lui.
    "/api/status":         (bots_status,    5),
    "/api/stocks":         (scan_stocks,    180),   # 3 min : yfinance est lent
    "/api/stocks/history": (stocks_history, 10),    # simple lecture de fichier
    "/api/stocks/quality": (signal_quality, 30),
    "/api/ranking":        (ranking_quality, 30),
}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC, **kwargs)

    def end_headers(self):
        # Rien n'est mis en cache, y compris le CSS et le JS. Sur un
        # serveur public ce serait du gaspillage ; ici, c'est ce qui
        # évite de passer dix minutes à chercher pourquoi une
        # modification du style « ne se voit pas » alors qu'elle est
        # bien enregistrée sur le disque.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def _hote_refuse(self):
        """
        Refuse la requête si elle ne vise pas ce Mac par son vrai nom.

        Une page piégée peut faire pointer son propre nom de domaine vers
        127.0.0.1 : le navigateur la laisse alors lire nos réponses et
        ajouter X-Dashboard. Mais l'en-tête Host porte toujours le nom
        qu'elle a utilisé, et ce nom n'est ni localhost ni 127.0.0.1.

        Renvoie True quand la requête a été refusée (réponse déjà envoyée).
        """
        hote = (self.headers.get("Host") or "").strip().lower()
        if hote in HOTES_AUTORISES:
            return False
        self.send_error(421, explain="Ce serveur ne répond qu'à localhost et 127.0.0.1.")
        return True

    def do_HEAD(self):
        if self._hote_refuse():
            return
        super().do_HEAD()

    def do_GET(self):
        if self._hote_refuse():
            return

        path = urlparse(self.path).path

        if path == "/api/graphique":
            self._graphique()
            return

        if path in ROUTES:
            producer, ttl = ROUTES[path]
            try:
                payload, computed_at = cached(path, ttl, producer)
                body = encoder_json({"ok": True, "data": payload, "cached_at": computed_at})
                status = 200
            except Exception as e:
                body = encoder_json({"ok": False, "error": str(e)})
                status = 500

            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()   # ajoute le Cache-Control (voir end_headers)
            self.wfile.write(body)
            return

        return super().do_GET()

    def _graphique(self):
        """/api/graphique?ticker=NVDA&periode=1j|1m|1a : la seule route à paramètres."""
        import ma_position
        from urllib.parse import parse_qs
        qs = parse_qs(urlparse(self.path).query)
        ticker = (qs.get("ticker") or ["NVDA"])[0].strip().upper()
        periode = (qs.get("periode") or ["1j"])[0]
        try:
            if periode not in GRAPHIQUE_CACHE:
                raise ValueError("période : 1j, 1m ou 1a")
            # La valeur est vérifiée DANS la fonction, avant toute mise en
            # cache : un ticker inconnu ne remplit pas la mémoire du serveur.
            payload, at = cached(f"/api/graphique?{ticker}&{periode}", GRAPHIQUE_CACHE[periode],
                                 lambda: ma_position.graphique(ticker, periode))
            self._json(200, {"ok": True, "data": payload, "cached_at": at})
        except ValueError as e:
            self._json(400, {"ok": False, "error": str(e)})
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})

    def do_POST(self):
        if self._hote_refuse():
            return

        path = urlparse(self.path).path
        actions = {
            "/api/journal/ajouter":      ajouter_au_journal,
            "/api/papier/ordre":         ordre_papier,
            "/api/papier/recommencer":   recommencer_papier,
        }
        if path not in actions:
            self.send_error(404)
            return

        # Voir le commentaire « LE JOURNAL » plus haut : cet en-tête est
        # ce qui empêche une page tierce d'écrire dans ton journal.
        if self.headers.get("X-Dashboard") != "1":
            self._json(403, {"ok": False, "error": "requête refusée (origine non reconnue)"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 10_000:
                raise ValueError("corps de requête invalide")
            payload = json.loads(self.rfile.read(length))
            result = actions[path](payload)
        except ValueError as e:
            self._json(400, {"ok": False, "error": str(e)})
            return
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})
            return

        # Une vraie décision change les positions du bot : le prochain appel
        # à /api/stocks doit les refléter, pas resservir un scan calculé
        # avant l'ajout. Un ordre fictif, lui, ne change rien au bot.
        if path == "/api/journal/ajouter":
            with _cache_lock:
                ancien = _cache.pop("/api/stocks", None)
                if ancien:
                    # Le classement, lui, ne dépend pas du carnet : on le garde
                    # pour le rang de « Ma position » jusqu'au prochain scan
                    # (sinon le rang reste « en attente » une minute).
                    _cache["/api/stocks:classement"] = ancien

        self._json(200, {"ok": True, "data": result})

    def _json(self, status, body):
        raw = encoder_json(body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        # Le log par défaut écrit une ligne par requête, y compris pour
        # chaque CSS et chaque rafraîchissement automatique. On ne garde
        # que les erreurs, sinon le terminal devient illisible.
        if args and str(args[1]).startswith(("4", "5")):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def surveiller_parent(server):
    """
    S'arrêter quand celui qui nous a lancés n'est plus là.

    L'application de bureau (dashboard_app.py) démarre ce serveur puis
    ouvre une fenêtre. Si elle meurt sans prévenir — Cmd+Q, plantage,
    ou un SIGTERM du Hub reçu pendant que la boucle graphique tient le
    fil principal — personne n'est là pour nous arrêter, et le port
    reste occupé par un serveur que plus aucune fenêtre ne regarde.

    Une seconde de latence suffit : c'est plus rapide que le temps qu'il
    faut à quelqu'un pour rouvrir l'application.

    Ne s'active QUE si DASHBOARD_PARENT_PID est fourni. Lancé au
    Terminal ou par le Hub, ce serveur reste maître de sa propre durée
    de vie — il n'a alors aucun parent à surveiller.
    """
    parent = os.environ.get("DASHBOARD_PARENT_PID")
    if not parent:
        return
    parent = int(parent)

    def boucle():
        while True:
            time.sleep(1.0)
            # On compare le lien de filiation, et surtout PAS
            # os.kill(parent, 0) : un processus mort dont personne n'a
            # récupéré le code de sortie reste un « zombie », et un
            # zombie répond encore au signal 0. Le serveur se croirait
            # alors toujours utile et garderait le port pour lui.
            #
            # La filiation, elle, est rompue à la seconde où le parent
            # meurt : le système nous rattache à launchd (PID 1).
            if os.getppid() != parent:
                server.shutdown()
                return

    threading.Thread(target=boucle, daemon=True).start()


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    surveiller_parent(server)
    print()
    print("  \033[1mDashboard trading\033[0m")
    print(f"  → http://localhost:{PORT}")
    print()
    print("  Lecture seule : aucune clé d'API n'est utilisée ici,")
    print("  le serveur ne peut passer aucun ordre.")
    print("  Ctrl+C pour arrêter.")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Arrêt du dashboard.\n")
        server.shutdown()


if __name__ == "__main__":
    main()
