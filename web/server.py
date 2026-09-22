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
    joignable depuis le réseau, seulement depuis ce Mac.

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
from datetime import datetime
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


# ══════════════════════════════════════════════════════════════════
#  CACHE
#  yfinance et Binance sont des services publics et gratuits : on
#  évite de les marteler. Chaque réponse est gardée quelques minutes.
#  Sans ça, ouvrir trois onglets du dashboard = trois séries d'appels
#  identiques, et un risque de se faire limiter par le fournisseur.
# ══════════════════════════════════════════════════════════════════

_cache = {}
_cache_lock = threading.Lock()


def cached(key, ttl_seconds, producer):
    """Renvoie la valeur en cache si elle est fraîche, sinon la recalcule."""
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry["at"]) < ttl_seconds:
            return entry["value"], entry["at"]

    value = producer()  # calculé HORS du verrou : un appel réseau lent
                        # ne doit pas bloquer les autres routes.
    with _cache_lock:
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
        return {"series": {}, "events": [], "count": 0}

    with open(path) as f:
        raw = json.load(f)

    series = {}
    events = []
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
        # `is_change` marque les instants où la recommandation a basculé.
        # Ce sont les seuls moments qui méritaient une notification.
        if entry.get("is_change"):
            events.append({
                "t": entry.get("timestamp"),
                "ticker": ticker,
                "reco": entry.get("recommendation"),
                "price": entry.get("price"),
                "signals": entry.get("signals", []),
            })

    events.sort(key=lambda e: e["t"] or "", reverse=True)
    return {
        "series": series,
        "events": events[:40],
        "count": len(raw),
        "from": raw[0].get("timestamp") if raw else None,
        "to": raw[-1].get("timestamp") if raw else None,
    }


# ══════════════════════════════════════════════════════════════════
#  POSITIONS  —  les seules écritures du serveur
# ══════════════════════════════════════════════════════════════════
#
#  Le serveur était jusqu'ici en lecture pure. Ces deux routes sont ses
#  seules écritures, et leur portée est étroite : elles ne touchent que
#  `data/stock_positions.json`, le carnet des achats que TU as passés
#  chez ton courtier. Aucun ordre n'est émis, aucune clé d'API n'entre
#  en jeu — on note ce qui a déjà eu lieu, rien de plus.
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

POSITIONS_FILE = os.path.join("data", "stock_positions.json")


class CarnetIllisible(Exception):
    """Le carnet existe mais n'a pas pu être lu."""


def read_positions():
    """
    Lit le carnet, ou refuse d'agir.

    L'ancienne version renvoyait `{}` dès qu'une lecture échouait —
    fichier corrompu, disque occupé, JSON tronqué. Comme `add_position`
    écrit ensuite `positions[ticker] = ...` puis sauvegarde, un simple
    ajout depuis le dashboard aurait REMPLACÉ tout le carnet par une
    seule ligne. Une erreur de lecture doit interrompre l'opération, pas
    la faire continuer sur des données inventées.

    L'absence de fichier reste un cas normal : c'est un carnet vide.
    """
    if not os.path.exists(POSITIONS_FILE):
        return {}
    try:
        with open(POSITIONS_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise CarnetIllisible(
            f"carnet de positions illisible ({e}) — aucune modification "
            f"effectuée, pour ne pas l'écraser") from e
    if not isinstance(data, dict):
        raise CarnetIllisible("carnet de positions au mauvais format")
    return data


def write_positions(positions):
    os.makedirs("data", exist_ok=True)
    # Écriture par fichier temporaire puis remplacement atomique : le bot
    # relit ce fichier à chaque cycle, il ne doit jamais tomber sur un
    # JSON à moitié écrit.
    tmp = POSITIONS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(positions, f, indent=2)
    os.replace(tmp, POSITIONS_FILE)


def add_position(payload):
    """Enregistre un achat déjà passé chez le courtier."""
    import stock_engine

    ticker = str(payload.get("ticker", "")).strip().upper()
    if not ticker:
        raise ValueError("ticker manquant")
    if ticker not in stock_engine.UNIVERSE:
        raise ValueError(f"{ticker} ne fait pas partie des valeurs suivies")

    try:
        quantity = float(payload.get("quantity"))
        price = float(payload.get("price"))
    except (TypeError, ValueError):
        raise ValueError("quantité et prix doivent être des nombres")
    # `float("nan")` et `float("inf")` passent la conversion sans broncher,
    # et `json.dump` les écrit tels quels : le fichier devient alors du
    # JSON invalide que plus rien ne sait relire.
    if not (math.isfinite(quantity) and math.isfinite(price)):
        raise ValueError("quantité et prix doivent être des nombres finis")
    if quantity <= 0 or price <= 0:
        raise ValueError("quantité et prix doivent être positifs")

    positions = read_positions()
    positions[ticker] = {
        "quantity": quantity,
        "entry_price": price,
        "entry_date": datetime.now().isoformat(),
    }
    write_positions(positions)
    return {"ticker": ticker, "positions": len(positions)}


def remove_position(payload):
    ticker = str(payload.get("ticker", "")).strip().upper()
    positions = read_positions()
    if ticker not in positions:
        raise ValueError(f"aucune position sur {ticker}")
    del positions[ticker]
    write_positions(positions)
    return {"ticker": ticker, "positions": len(positions)}


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

ROUTES = {
    # chemin              → (fonction, durée de cache en secondes)
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

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ROUTES:
            producer, ttl = ROUTES[path]
            try:
                payload, computed_at = cached(path, ttl, producer)
                body = json.dumps(
                    {"ok": True, "data": payload, "cached_at": computed_at},
                    default=str,
                ).encode()
                status = 200
            except Exception as e:
                body = json.dumps({"ok": False, "error": str(e)}).encode()
                status = 500

            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()   # ajoute le Cache-Control (voir end_headers)
            self.wfile.write(body)
            return

        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        actions = {
            "/api/positions/add": add_position,
            "/api/positions/remove": remove_position,
        }
        if path not in actions:
            self.send_error(404)
            return

        # Voir le commentaire au-dessus de POSITIONS_FILE : cet en-tête
        # est ce qui empêche une page tierce d'écrire dans ton carnet.
        if self.headers.get("X-Dashboard") != "1":
            self._json(403, {"ok": False, "error": "requête refusée (origine non reconnue)"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 10_000:
                raise ValueError("corps de requête invalide")
            payload = json.loads(self.rfile.read(length))
            result = actions[path](payload)
        except (ValueError, CarnetIllisible) as e:
            self._json(400, {"ok": False, "error": str(e)})
            return
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})
            return

        # Les positions viennent de changer : le prochain appel à
        # /api/stocks doit les refléter, pas resservir un scan calculé
        # avant l'ajout.
        with _cache_lock:
            _cache.pop("/api/stocks", None)

        self._json(200, {"ok": True, "data": result})

    def _json(self, status, body):
        raw = json.dumps(body, default=str).encode()
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
