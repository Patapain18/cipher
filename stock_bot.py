"""
Stock Signal Bot — Bot de signaux pour actions.

Comme on ne peut pas trader automatiquement en France, ce bot :
1. Surveille les actions configurées en temps réel
2. Calcule leur état technique et les classe par momentum 12-1
3. Affiche un dashboard avec les recommandations
4. Notifie sur le CLASSEMENT, pas sur le score technique :
   — une recomposition du top 12, au rythme validé de ~10 séances
   — le décrochage immédiat d'une valeur que tu détiens

Le score technique reste calculé et historisé, mais il ne déclenche plus
d'alerte : la mesure (stock_eval.py) a montré qu'il n'annonce rien.

Tu fais les ordres manuellement sur ton broker (Trade Republic, DEGIRO, etc.).
"""

import time
import signal
import sys
import argparse
import os
import json
import subprocess
from datetime import datetime, timedelta

import yfinance as yf
import pandas as pd
import ta
import requests

import stock_engine as engine
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
import warnings
warnings.filterwarnings("ignore")

load_dotenv()
console = Console()

# Config par défaut (basée sur les tests)
DEFAULT_TICKERS = os.getenv("STOCK_TICKERS", ",".join(engine.UNIVERSE_TICKERS)).split(",")
CAPITAL = float(os.getenv("STOCK_CAPITAL", "100"))
LOOP_INTERVAL = int(os.getenv("STOCK_LOOP_INTERVAL", "300"))  # 5 minutes
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "")
HISTORY_FILE = "data/signal_history.json"

# Intervalle minimum entre deux instantanés d'une même valeur, en
# secondes. Mesuré en temps réel et non en nombre de cycles, pour
# résister aux mises en veille du Mac (voir _due_for_snapshot).
SNAPSHOT_INTERVAL = int(os.getenv("STOCK_SNAPSHOT_INTERVAL", "3600"))

PID_FILE = "logs/stock_bot.pid"

# ── Notifications fondées sur le momentum ────────────────────────
#
#  Les notifications reposaient sur le score technique, dont la mesure
#  (stock_eval.py) a montré qu'il n'annonce rien : IC négatif, écart nul
#  avec « ne rien faire ». Elles suivent désormais le momentum 12-1, seul
#  critère du projet à avoir battu à la fois le tirage au sort et le
#  panier équipondéré.
#
#  MAIS LE MOMENTUM N'EST PAS UN SIGNAL, C'EST UN CLASSEMENT.
#      Ce qui a été validé, c'est une stratégie complète : détenir les
#      douze mieux classées et rééquilibrer tous les dix jours de bourse.
#      Alerter dès qu'une valeur entre ou sort du top douze reviendrait à
#      rééquilibrer en continu — soit une rotation bien supérieure aux
#      15 % mesurés, plus de frais, et surtout un comportement qui n'est
#      plus celui qu'on a testé.
#
#      D'où le rythme : une notification de rééquilibrage tous les
#      REBALANCE_DAYS, et entre deux, le silence.
REBALANCE_TOP_N = int(os.getenv("STOCK_TOP_N", "12"))
# 14 jours calendaires ≈ 10 séances, l'horizon sur lequel la stratégie a
# été mesurée. Compté en dates réelles et non en cycles, pour rester juste
# quand le Mac a dormi entre-temps.
REBALANCE_DAYS = int(os.getenv("STOCK_REBALANCE_DAYS", "14"))
# Rang au-delà duquel une valeur DÉTENUE est considérée comme ayant
# décroché. Celle-là est signalée tout de suite : il ne s'agit plus
# d'arbitrer un portefeuille théorique mais de l'argent réellement engagé.
HELD_ALERT_RANK = int(os.getenv("STOCK_HELD_ALERT_RANK", "25"))
REBALANCE_STATE = "data/rebalance_state.json"


def running_instance():
    """
    PID d'un bot déjà en cours, ou None.

    Le bot peut désormais être lancé de deux façons — à la main via
    manage_bot.sh, ou automatiquement à l'ouverture de session par
    launchd. Sans ce verrou, les deux pourraient tourner en même temps :
    deux séries de notifications pour un même signal, et deux processus
    écrivant tour à tour dans le même fichier d'historique.

    Le PID est écrit par le bot lui-même plutôt que par le script de
    lancement, pour que `manage_bot.sh status` dise la vérité quelle que
    soit la façon dont le bot a démarré.
    """
    if not os.path.exists(PID_FILE):
        return None
    try:
        pid = int(open(PID_FILE).read().strip())
    except (ValueError, OSError):
        return None
    if pid == os.getpid():
        return None            # c'est nous : manage_bot.sh a pris les devants
    try:
        os.kill(pid, 0)        # signal 0 : « es-tu vivant ? », ne tue rien
        return pid
    except OSError:
        return None            # PID périmé, le processus n'existe plus


def notify_macos(title, message, subtitle=""):
    """
    Bulle de notification native macOS.

    Passe par `osascript`, présent sur tout Mac : aucune dépendance à
    installer, et surtout aucune donnée qui sort de la machine — c'est
    le Centre de notifications du système qui affiche, rien d'autre.

    Les textes sont sérialisés en JSON pour être échappés proprement :
    un nom de valeur ou un montant contenant un guillemet casserait
    sinon le script AppleScript. `ensure_ascii=False` est indispensable,
    car AppleScript ne sait pas lire les séquences \\uXXXX que produirait
    l'échappement par défaut — les accents et les emoji s'afficheraient
    en charabia.

    Une notification qui échoue ne doit jamais interrompre un cycle :
    toute erreur est avalée. Au premier envoi, macOS peut demander
    l'autorisation de notifier ; si elle est refusée, le bot continue
    simplement sans bulles.
    """
    def esc(s):
        return json.dumps(str(s), ensure_ascii=False)

    script = f"display notification {esc(message)} with title {esc(title)}"
    if subtitle:
        script += f" subtitle {esc(subtitle)}"
    try:
        subprocess.run(["osascript", "-e", script],
                       capture_output=True, timeout=5, check=False)
    except Exception:
        pass


def _sell_line(analysis, position):
    """Une ligne du panneau de vente, qu'on connaisse la position ou non."""
    head = f"[bold red]🔴 VENDRE {analysis['ticker']}[/bold red] @ ${analysis['price']:.2f}"
    if not position:
        return head + "\n   [dim]aucune position enregistrée[/dim]"
    calcul = engine.pnl_position(position, analysis["price"], analysis["ticker"])
    if not calcul:
        return head + "\n   [dim]position illisible[/dim]"
    split = "  (ajusté d'une division)" if calcul["split"] != 1.0 else ""
    return (f"{head}\n"
            f"   Position actuelle: {calcul['quantity']:.4f} actions{split}\n"
            f"   P&L: {calcul['pnl_pct']:+.2f}%")


class StockSignalBot:
    def __init__(self, tickers, capital, dashboard=True, legacy=False):
        self.tickers = [t.strip() for t in tickers]
        self.capital = capital
        self.use_dashboard = dashboard
        self.legacy = legacy   # True = ancien score v1 à poids fixes
        self.running = True
        self.positions = self._load_positions()
        self.last_signals = {}
        self.signal_history = self._load_history()
        # Quand chaque valeur a eu son dernier instantané. Reconstruit
        # depuis l'historique déjà sur le disque, pour qu'un redémarrage
        # ne réécrive pas aussitôt 49 relevés en double.
        self._last_snapshot = self._restore_snapshot_times()
        self.discord_enabled = bool(DISCORD_WEBHOOK)

    def _load_history(self):
        """Charge l'historique des signaux."""
        os.makedirs("data", exist_ok=True)
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE) as f:
                    return json.load(f)
            except (OSError, ValueError) as e:
                # Reprendre à zéro écraserait le fichier au prochain
                # enregistrement. On le met de côté pour pouvoir le
                # récupérer, et on le dit.
                secours = HISTORY_FILE + ".corrompu"
                try:
                    os.replace(HISTORY_FILE, secours)
                    console.print(f"[red]Historique illisible ({e}) — "
                                  f"mis de côté dans {secours}[/red]")
                except OSError:
                    console.print(f"[red]Historique illisible ({e})[/red]")
        return []

    def _restore_snapshot_times(self):
        """Date du dernier relevé de chaque valeur, lue dans l'historique."""
        times = {}
        for entry in self.signal_history:
            try:
                ts = datetime.fromisoformat(entry["timestamp"]).timestamp()
            except (ValueError, KeyError, TypeError):
                continue
            ticker = entry.get("ticker")
            if ticker:
                times[ticker] = max(times.get(ticker, 0), ts)
        return times

    def _save_history(self):
        """
        Sauvegarde l'historique des signaux.

        La limite s'adapte au nombre de valeurs suivies. Le plafond fixe
        de 1000 entrées convenait à neuf valeurs — il représentait des
        semaines de relevés. Avec quarante-neuf, chaque instantané
        horaire en consomme quarante-neuf : on ne garderait plus qu'une
        vingtaine d'heures, et le graphique d'historique deviendrait
        inutilisable au bout d'une journée.
        """
        os.makedirs("data", exist_ok=True)
        keep = max(1000, len(self.tickers) * 250)   # ~250 relevés par valeur
        # Écriture par fichier temporaire puis remplacement atomique.
        # En écrivant directement, une interruption au mauvais moment
        # (veille du Mac, arrêt du service) laissait un JSON tronqué —
        # que _load_history avalait ensuite en silence, effaçant des mois
        # de relevés sans que rien ne le signale.
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.signal_history[-keep:], f, indent=2, default=str)
        os.replace(tmp, HISTORY_FILE)

    def _add_to_history(self, analysis, signal_change=False):
        """Ajoute un signal à l'historique."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "ticker": analysis["ticker"],
            "price": analysis["price"],
            "rsi": analysis["rsi"],
            "buy_score": analysis["buy_score"],
            "sell_score": analysis["sell_score"],
            "recommendation": analysis["recommendation"],
            "signals": analysis["signals"],
            "change_1d": analysis["change_1d"],
            "change_1w": analysis["change_1w"],
            "change_1m": analysis["change_1m"],
            "is_change": signal_change,
        }
        self.signal_history.append(entry)
        self._save_history()

    def _send_discord(self, ticker, signal, analysis):
        """Envoie une notification Discord."""
        if not self.discord_enabled:
            return

        color_map = {
            "BUY": 0x00FF00,    # Vert
            "SELL": 0xFF0000,   # Rouge
            "WATCH": 0xFFFF00,  # Jaune
        }
        emoji = {"BUY": "🟢", "SELL": "🔴", "WATCH": "👀"}.get(signal, "📊")

        fields = [
            {"name": "💰 Prix", "value": f"${analysis['price']:.2f}", "inline": True},
            {"name": "📊 RSI", "value": f"{analysis['rsi']:.0f}", "inline": True},
            {"name": "🎯 Score", "value": f"BUY: {analysis['buy_score']}/5 | SELL: {analysis['sell_score']}/5", "inline": True},
            {"name": "📈 1 jour", "value": f"{analysis['change_1d']:+.2f}%", "inline": True},
            {"name": "📈 1 semaine", "value": f"{analysis['change_1w']:+.2f}%", "inline": True},
            {"name": "📈 1 mois", "value": f"{analysis['change_1m']:+.2f}%", "inline": True},
        ]

        if analysis["signals"]:
            fields.append({
                "name": "🔍 Signaux détectés",
                "value": ", ".join(analysis["signals"]),
                "inline": False
            })

        if signal == "BUY":
            qty = self.capital / analysis["price"]
            fields.append({
                "name": "💡 Suggestion",
                "value": f"Acheter ~{qty:.2f} actions ({self.capital:.0f}€)",
                "inline": False
            })

        embed = {
            "title": f"{emoji} {signal} — {ticker}",
            "color": color_map.get(signal, 0x808080),
            "fields": fields,
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {"text": f"Stock Signal Bot | Capital: {self.capital}€"}
        }

        try:
            requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=10)
        except Exception as e:
            console.print(f"[red]Erreur Discord: {e}[/red]")

    # ── Classement et rééquilibrage ──────────────────────────────

    def _load_rebalance_state(self):
        try:
            with open(REBALANCE_STATE) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"last_rebalance": None, "top": [], "alerted": {}}

    def _save_rebalance_state(self, state):
        os.makedirs("data", exist_ok=True)
        tmp = REBALANCE_STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp, REBALANCE_STATE)

    @staticmethod
    def rank_by_momentum(analyses):
        """
        Classe les valeurs par momentum 12-1 décroissant.

        Celles dont le momentum n'est pas calculable — moins de 273
        séances d'historique — sont écartées plutôt que placées en fin de
        liste : les absentes ne doivent pas peser sur le rang des autres.
        """
        ranked = sorted(
            [a for a in analyses if a.get("mom_12_1") is not None],
            key=lambda a: a["mom_12_1"], reverse=True,
        )
        for position, a in enumerate(ranked, start=1):
            a["rang"] = position
        return ranked

    def check_rebalance(self, ranked):
        """
        Notifie une recomposition du portefeuille, au rythme validé.

        Renvoie True si une notification a été envoyée.
        """
        # --- Garde de couverture ---
        # fetch_many écarte silencieusement toute valeur dont yfinance n'a
        # pas rendu assez d'historique, ce qui arrive couramment sur une
        # requête groupée de 49 tickers. Un classement tiré d'un univers
        # amputé n'est pas un classement : le 2026-09-07, un cycle à 38/49
        # a gravé un top 12 faux — sans NVDA, AVGO ni META, absentes du
        # téléchargement — et réarmé le compteur de 14 jours, empêchant
        # toute correction ultérieure.
        # Un rééquilibrage reporté ne coûte qu'un cycle ; un rééquilibrage
        # faux déclenche des ordres sur de l'argent réel.
        attendu = max(1, len(self.tickers))
        couverture = len(ranked) / attendu
        if couverture < 0.95:
            console.print(f"[yellow]Rééquilibrage reporté : seulement {len(ranked)}/"
                          f"{attendu} valeurs classables ({couverture:.0%})[/yellow]")
            return False

        state = self._load_rebalance_state()
        top = [a["ticker"] for a in ranked[:REBALANCE_TOP_N]]
        now = datetime.now()

        last = state.get("last_rebalance")
        if last:
            try:
                elapsed = (now - datetime.fromisoformat(last)).days
            except (ValueError, TypeError):
                elapsed = REBALANCE_DAYS
        else:
            elapsed = None      # premier passage

        if elapsed is not None and elapsed < REBALANCE_DAYS:
            return False

        previous = state.get("top", [])
        entrants = [t for t in top if t not in previous]
        sortants = [t for t in previous if t not in top]

        state.update({"last_rebalance": now.isoformat(), "top": top})
        self._save_rebalance_state(state)

        # Premier passage : on enregistre la composition sans alerter.
        # Annoncer « 12 valeurs entrent » au tout premier lancement
        # n'apprendrait rien — c'est un point de départ, pas un mouvement.
        if elapsed is None:
            console.print(f"[dim]Composition initiale du top {REBALANCE_TOP_N} "
                          f"enregistrée : {', '.join(top)}[/dim]")
            return False

        if not entrants and not sortants:
            console.print(f"[dim]Rééquilibrage : aucun changement dans le "
                          f"top {REBALANCE_TOP_N}.[/dim]")
            return False

        parts = []
        if entrants:
            parts.append(f"Entrent : {', '.join(entrants)}")
        if sortants:
            parts.append(f"Sortent : {', '.join(sortants)}")

        # Ce que tu détiens et qui vient de sortir mérite d'être nommé :
        # c'est la seule partie du message qui appelle une action sur de
        # l'argent déjà engagé.
        detenues_sortantes = [t for t in sortants if t in self.positions]
        subtitle = (f"Tu détiens {', '.join(detenues_sortantes)}"
                    if detenues_sortantes else f"Top {REBALANCE_TOP_N} par momentum")

        console.print(f"\n[bold cyan]🔄 RÉÉQUILIBRAGE[/bold cyan] — {' | '.join(parts)}\n")
        notify_macos(
            title=f"Rééquilibrage du top {REBALANCE_TOP_N}",
            message=" — ".join(parts),
            subtitle=subtitle,
        )
        return True

    def check_held_positions(self, ranked):
        """
        Alerte immédiate sur une valeur détenue qui décroche au classement.

        Celle-ci ne suit pas le rythme du rééquilibrage : quand une valeur
        qu'on détient réellement quitte le haut du panier, attendre deux
        semaines pour le mentionner n'aurait pas de sens.

        Une même valeur n'est signalée qu'une fois : l'état retient qui a
        déjà fait l'objet d'une alerte, et ne la relance que si la valeur
        est remontée entre-temps.
        """
        if not self.positions or not ranked:
            return

        rangs = {a["ticker"]: a["rang"] for a in ranked}
        total = len(ranked)
        state = self._load_rebalance_state()
        alerted = state.get("alerted", {})
        changed = False

        for ticker in list(self.positions):
            rang = rangs.get(ticker)
            if rang is None:
                continue
            # Seuil RELATIF : HELD_ALERT_RANK est calibré sur 49 valeurs.
            # Comparé tel quel à un classement amputé (yfinance incomplet),
            # il ne se déclencherait jamais — un 25ᵉ sur 30 est pourtant
            # bien plus bas qu'un 25ᵉ sur 49.
            seuil = max(3, round(HELD_ALERT_RANK * total / len(self.tickers)))
            if rang > seuil and ticker not in alerted:
                notify_macos(
                    title=f"{ticker} décroche",
                    message=f"{rang}ᵉ sur {total} au momentum — tu détiens cette valeur",
                    subtitle="Le classement ne la place plus dans le haut du panier",
                )
                console.print(f"\n[bold red]⚠️  {ticker} détenue et {rang}ᵉ/{total} "
                              f"au momentum[/bold red]\n")
                alerted[ticker] = datetime.now().isoformat()
                changed = True
            elif rang <= seuil and ticker in alerted:
                del alerted[ticker]     # remontée : l'alerte peut resservir
                changed = True

        # Les valeurs vendues ne doivent pas garder d'alerte en mémoire.
        for ticker in [t for t in alerted if t not in self.positions]:
            del alerted[ticker]
            changed = True

        if changed:
            state["alerted"] = alerted
            self._save_rebalance_state(state)

    def _due_for_snapshot(self, ticker):
        """
        Faut-il enregistrer un instantané de cette valeur ?

        L'ancienne règle comptait les cycles (`cycle % 12`), ce qui
        supposait un bot tournant sans interruption. Or le Mac dort : à
        chaque réveil le compteur repartait de son point d'arrêt, et une
        nuit entière passait sans qu'aucun relevé ne soit écrit — c'est
        pourquoi l'historique est resté quasi vide.

        On raisonne donc en temps écoulé, pas en tours de boucle. Au
        réveil, plus d'une heure s'est passée depuis le dernier
        instantané : il est écrit immédiatement. L'historique porte alors
        les trous du sommeil, ce qui est honnête, plutôt que d'être
        simplement absent.
        """
        now = time.time()
        last = self._last_snapshot.get(ticker, 0)
        if now - last < SNAPSHOT_INTERVAL:
            return False
        self._last_snapshot[ticker] = now
        return True

    def _load_positions(self):
        """Charge les positions sauvegardées."""
        path = "data/stock_positions.json"
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return {}

    def _save_positions(self):
        os.makedirs("data", exist_ok=True)
        with open("data/stock_positions.json", "w") as f:
            json.dump(self.positions, f, indent=2)

    def fetch_data(self, ticker, period="2y"):
        """
        Récupère les données récentes.

        Deux ans, et non trois mois comme à l'origine, pour deux raisons :

        — le moteur v2 situe le score du jour par rapport aux notes des
          séances précédentes (stock_engine._to_scores) ; avec trois mois
          il ne resterait qu'une trentaine de séances exploitables une
          fois les moyennes 50 calculées ;
        — le momentum 12-1 réclame 252 + 21 = 273 séances avant de
          produire sa première valeur. Sur un an d'historique il serait
          vide en permanence.
        """
        try:
            df = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=True)
            if df.empty: return None
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return df
        except Exception as e:
            console.print(f"[red]Erreur {ticker}: {e}[/red]")
            return None

    def fetch_many(self, tickers, period="2y"):
        """
        Télécharge plusieurs valeurs en UNE requête groupée.

        Avec neuf valeurs, les récupérer une par une était acceptable.
        Avec quarante-neuf, chaque scan enchaînerait quarante-neuf
        allers-retours et prendrait plusieurs minutes — un dashboard qui
        met trois minutes à s'afficher ne se consulte plus.

        `yf.download` accepte une liste et parallélise les requêtes en
        interne. Il renvoie alors un tableau à colonnes hiérarchiques
        (indicateur, valeur) qu'on redécoupe ici en un DataFrame par
        ticker, au format qu'attend le reste du code.

        Renvoie un dict {ticker: DataFrame}, sans les valeurs pour
        lesquelles rien n'est revenu.
        """
        tickers = [t.strip().upper() for t in tickers if t.strip()]
        if not tickers:
            return {}

        raw = yf.download(tickers, period=period, interval="1d",
                          progress=False, auto_adjust=True,
                          group_by="ticker", threads=True)
        if raw is None or raw.empty:
            return {}

        out = {}
        for ticker in tickers:
            try:
                # Un seul ticker demandé : pas de niveau supplémentaire.
                df = raw[ticker] if isinstance(raw.columns, pd.MultiIndex) else raw
            except KeyError:
                continue
            df = df.dropna(how="all")
            # 273 séances sont nécessaires au momentum 12-1 ; en dessous
            # de 300 on préfère écarter la valeur plutôt que d'afficher
            # des colonnes vides.
            if len(df) >= 300:
                out[ticker] = df

        # Un téléchargement partiel doit se voir. Sans cette ligne, une
        # requête groupée incomplète produisait un classement amputé sans
        # que rien ne le signale — ni dans les logs, ni à l'écran.
        manquants = [t for t in tickers if t not in out]
        if manquants:
            console.print(f"[yellow]{len(manquants)} valeur(s) sans données "
                          f"exploitables : {', '.join(manquants)}[/yellow]")
        return out

    def get_current_price(self, ticker):
        """Prix actuel."""
        try:
            t = yf.Ticker(ticker)
            info = t.fast_info
            return float(info["lastPrice"])
        except:
            return None

    def analyze(self, ticker, df):
        """
        Calcule les signaux pour une action — moteur v2.

        Délègue à stock_engine, qui pondère les indicateurs selon le
        régime de marché au lieu de les additionner à poids fixes. Le
        dictionnaire renvoyé garde toutes les clés de la v1 pour que
        l'affichage terminal, les notifications Discord et l'historique
        déjà enregistré continuent de fonctionner sans retouche ; les
        nouvelles informations (régime, moteurs du score, fourchette
        projetée) s'y ajoutent.

        Mesures comparant les deux moteurs : stock_eval.py.
        Pour retrouver l'ancien comportement : `analyze_legacy`, ou
        l'option --legacy en ligne de commande.
        """
        if self.legacy:
            return self.analyze_legacy(ticker, df)

        v2 = engine.analyze(df)
        ind = engine.compute_indicators(df).iloc[-1]
        close = engine.normalize(df)["close"]

        # --- Un SELL n'a de sens que sur une valeur détenue ---
        # Le moteur est générique : il ignore ce que tu possèdes et
        # annonce SELL dès que le score baissier atteint son seuil. La
        # v1, elle, exigeait `ticker in self.positions`. Ce bot ne
        # pratiquant pas la vente à découvert, un « VENDRE AAPL » sur
        # une action qu'on n'a jamais achetée n'est pas seulement inutile
        # — il fait planter le panneau de vente, qui va lire la position
        # correspondante.
        # Le signal baissier n'est pas perdu pour autant : il reste
        # lisible dans `sell_score`.
        reco = v2["recommendation"]
        if reco == "SELL" and ticker.upper() not in self.positions:
            reco = "HOLD"

        def change(periods):
            if len(close) <= periods:
                return 0.0
            past = float(close.iloc[-1 - periods])
            return (v2["price"] - past) / past * 100 if past else 0.0

        # Les « déclencheurs » affichés viennent désormais des composantes
        # qui ont réellement pesé sur le score, et non d'une liste de
        # conditions binaires. On y lit pourquoi le score est ce qu'il est.
        signals = [d["nom"] for d in v2["drivers"] if abs(d["poids"]) > 0.05]

        return {
            "ticker": ticker,
            "price": v2["price"],
            "change_1d": change(1),
            "change_1w": change(5),
            "change_1m": change(22),
            "rsi": float(ind["rsi"]),
            "macd_hist": float(ind["macd_hist"]),
            "sma_20": float(ind["sma20"]),
            "sma_50": float(ind["sma50"]),
            "bb_lower": float(ind["bb_low"]),
            "bb_upper": float(ind["bb_high"]),
            "buy_score": v2["buy_score"],
            "sell_score": v2["sell_score"],
            "recommendation": reco,
            "signals": signals,
            # --- apports de la v2 ---
            "regime": v2["regime"],
            "adx": v2["adx"],
            "trend_weight": v2["trend_weight"],
            "volume_ratio": v2["volume_ratio"],
            "atr_pct": v2["atr_pct"],
            "drivers": v2["drivers"],
            "projection": v2["projection"],
            "strength": v2["strength"],
            # Note brute non calibrée : la seule des deux qui soit
            # comparable d'une valeur à l'autre, donc la seule qui
            # permette de les classer entre elles. Le score calibré,
            # lui, ramène chaque valeur à son propre passé et efface
            # justement ce qui les distingue (mesuré : stock_eval.py
            # --ranking).
            "raw_score": v2["raw_score"],
            # Rendement 12 mois hors dernier mois. Mesuré sur 9 ans et
            # 49 valeurs (stock_eval.py --ranking --large), c'est le seul
            # critère de classement qui batte à la fois le hasard et le
            # panier équipondéré — y compris en risque. Il vaut donc
            # d'être affiché, même si le moteur ne s'en sert pas pour
            # produire son score.
            "mom_12_1": v2["mom_12_1"],
        }

    def analyze_legacy(self, ticker, df):
        """Ancien score à poids fixes (v1), conservé pour comparaison."""
        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        # Indicateurs
        sma_20 = ta.trend.sma_indicator(close, window=20).iloc[-1]
        sma_50 = ta.trend.sma_indicator(close, window=50).iloc[-1]
        ema_12 = ta.trend.ema_indicator(close, window=12).iloc[-1]
        ema_26 = ta.trend.ema_indicator(close, window=26).iloc[-1]
        rsi = ta.momentum.rsi(close, window=14).iloc[-1]
        macd_hist = ta.trend.macd_diff(close).iloc[-1]
        bb_lower = ta.volatility.bollinger_lband(close, window=20).iloc[-1]
        bb_upper = ta.volatility.bollinger_hband(close, window=20).iloc[-1]

        price = float(close.iloc[-1])
        change_1d = ((price - float(close.iloc[-2])) / float(close.iloc[-2])) * 100 if len(close) > 1 else 0
        change_1w = ((price - float(close.iloc[-6])) / float(close.iloc[-6])) * 100 if len(close) > 6 else 0
        change_1m = ((price - float(close.iloc[-22])) / float(close.iloc[-22])) * 100 if len(close) > 22 else 0

        # Score d'achat (0-5)
        buy_score = 0
        signals = []

        if rsi < 30:
            buy_score += 2
            signals.append("RSI survente")
        elif rsi < 45:
            buy_score += 1

        if price < bb_lower:
            buy_score += 2
            signals.append("Sous Bollinger")
        elif price < (bb_lower + bb_upper) / 2:
            buy_score += 1

        if ema_12 > ema_26:
            buy_score += 1
            signals.append("EMA haussière")

        if macd_hist > 0:
            buy_score += 1
            signals.append("MACD positif")

        if sma_20 > sma_50:
            buy_score += 1
            signals.append("Tendance haussière")

        # Score de vente
        sell_score = 0
        if rsi > 70:
            sell_score += 2
        if price > bb_upper:
            sell_score += 2
        if ema_12 < ema_26:
            sell_score += 1
        if macd_hist < 0:
            sell_score += 1

        # Décision
        if buy_score >= 4 and ticker not in self.positions:
            recommendation = "BUY"
        elif sell_score >= 3 and ticker in self.positions:
            recommendation = "SELL"
        elif buy_score >= 3:
            recommendation = "WATCH"
        else:
            recommendation = "HOLD"

        return {
            "ticker": ticker,
            "price": price,
            "change_1d": change_1d,
            "change_1w": change_1w,
            "change_1m": change_1m,
            "rsi": float(rsi),
            "macd_hist": float(macd_hist),
            "sma_20": float(sma_20),
            "sma_50": float(sma_50),
            "bb_lower": float(bb_lower),
            "bb_upper": float(bb_upper),
            "buy_score": buy_score,
            "sell_score": sell_score,
            "recommendation": recommendation,
            "signals": signals,
        }

    def render_dashboard(self, analyses):
        """Affiche le dashboard."""
        console.clear()

        # Header
        console.print(Panel(
            f"[bold cyan]📊 STOCK SIGNAL BOT[/bold cyan]  |  "
            f"Capital: [yellow]{self.capital}€[/yellow]  |  "
            f"Actions: {len(self.tickers)}  |  "
            f"{datetime.now().strftime('%H:%M:%S')}",
            style="cyan"
        ))

        # Tableau principal
        table = Table(title="📈 Surveillance des Actions", show_header=True, header_style="bold magenta")
        table.add_column("Action", style="bold", width=8)
        table.add_column("Prix", justify="right", width=10)
        table.add_column("1j", justify="right", width=7)
        table.add_column("1sem", justify="right", width=8)
        table.add_column("1m", justify="right", width=8)
        table.add_column("RSI", justify="right", width=6)
        table.add_column("Signal", justify="center", width=10)
        table.add_column("Score", justify="center", width=8)
        table.add_column("Position", justify="center", width=12)

        for a in analyses:
            # Couleurs
            d1_s = "green" if a["change_1d"] >= 0 else "red"
            d7_s = "green" if a["change_1w"] >= 0 else "red"
            d30_s = "green" if a["change_1m"] >= 0 else "red"

            if a["rsi"] < 30: rsi_s = "green bold"
            elif a["rsi"] > 70: rsi_s = "red bold"
            else: rsi_s = "yellow"

            rec_styles = {
                "BUY": "[bold green]🟢 BUY[/bold green]",
                "SELL": "[bold red]🔴 SELL[/bold red]",
                "WATCH": "[yellow]👀 WATCH[/yellow]",
                "HOLD": "[dim]HOLD[/dim]",
            }

            # Position
            if a["ticker"] in self.positions:
                pos = self.positions[a["ticker"]]
                calcul = engine.pnl_position(pos, a["price"], a["ticker"])
                pnl = calcul["pnl_pct"] if calcul else 0.0
                pnl_s = "green" if pnl >= 0 else "red"
                pos_str = f"[{pnl_s}]{pnl:+.1f}%[/{pnl_s}]"
            else:
                pos_str = "[dim]-[/dim]"

            table.add_row(
                a["ticker"],
                f"${a['price']:.2f}",
                f"[{d1_s}]{a['change_1d']:+.1f}%[/{d1_s}]",
                f"[{d7_s}]{a['change_1w']:+.1f}%[/{d7_s}]",
                f"[{d30_s}]{a['change_1m']:+.1f}%[/{d30_s}]",
                f"[{rsi_s}]{a['rsi']:.0f}[/{rsi_s}]",
                rec_styles[a["recommendation"]],
                f"{a['buy_score']}/{a['sell_score']}",
                pos_str,
            )

        console.print(table)

        # Recommandations actives
        buys = [a for a in analyses if a["recommendation"] == "BUY"]
        sells = [a for a in analyses if a["recommendation"] == "SELL"]
        watches = [a for a in analyses if a["recommendation"] == "WATCH"]

        if buys:
            console.print()
            console.print(Panel(
                "\n".join([
                    f"[bold green]🟢 ACHETER {a['ticker']}[/bold green] @ ${a['price']:.2f}\n"
                    f"   Signaux: {', '.join(a['signals'])}\n"
                    f"   Quantité suggérée: {self.capital/len(buys)/a['price']:.2f} actions ({self.capital/len(buys):.2f}€)\n"
                    f"   [dim]Va sur ton broker (Trade Republic, DEGIRO...) et passe l'ordre manuellement[/dim]"
                    for a in buys
                ]),
                title="💰 SIGNAUX D'ACHAT",
                border_style="green",
            ))

        if sells:
            console.print()
            console.print(Panel(
                # `.get` plutôt qu'un accès direct : même si la règle
                # ci-dessus garantit qu'un SELL correspond à une position,
                # une position supprimée entre le scan et l'affichage
                # suffirait à faire tomber tout le cycle — historisation
                # comprise — pour un simple panneau décoratif.
                "\n".join([
                    _sell_line(a, self.positions.get(a["ticker"].upper()))
                    for a in sells
                ]),
                title="💸 SIGNAUX DE VENTE",
                border_style="red",
            ))

        if watches:
            console.print()
            wt = Table(title="👀 Surveillance rapprochée", show_header=False)
            wt.add_column()
            for a in watches:
                wt.add_row(f"  {a['ticker']} @ ${a['price']:.2f} — Score {a['buy_score']}/5 (proche signal d'achat)")
            console.print(wt)

        # Performance globale
        if self.positions:
            console.print()
            total_value = 0
            total_cost = 0
            for ticker, pos in self.positions.items():
                a = next((x for x in analyses if x["ticker"] == ticker), None)
                if a:
                    calcul = engine.pnl_position(pos, a["price"], ticker)
                    if calcul:
                        total_value += calcul["quantity"] * a["price"]
                        total_cost += calcul["quantity"] * calcul["entry_price"]

            pnl = total_value - total_cost
            roi = (pnl / total_cost * 100) if total_cost > 0 else 0
            pnl_s = "green" if pnl >= 0 else "red"

            console.print(Panel(
                f"[bold]Portefeuille:[/bold]\n"
                f"  Valeur: ${total_value:.2f}\n"
                f"  Coût: ${total_cost:.2f}\n"
                f"  P&L: [{pnl_s}]{pnl:+.2f}$ ({roi:+.2f}%)[/{pnl_s}]",
                title="📊 PORTEFEUILLE",
                border_style="cyan",
            ))

        console.print(f"\n[dim]Prochaine analyse dans {LOOP_INTERVAL}s | Ctrl+C pour arrêter[/dim]")
        console.print(f"[dim]💡 Pour enregistrer un achat: python3 stock_bot.py --add TICKER QUANTITY PRICE[/dim]")

    def run(self):
        """Boucle principale."""
        console.print(Panel(
            f"[bold cyan]Démarrage du Stock Signal Bot[/bold cyan]\n"
            f"Actions surveillées: {', '.join(self.tickers)}\n"
            f"Capital simulé: {self.capital}€\n"
            f"Intervalle: {LOOP_INTERVAL}s\n"
            f"Notifications: bulles macOS sur le classement (rééquilibrage + décrochage)\n"
            f"Historique: {HISTORY_FILE} ({len(self.signal_history)} entrées)",
            border_style="cyan"
        ))

        cycle = 0
        while self.running:
            try:
                cycle += 1
                # Les positions sont relues à chaque tour : elles peuvent
                # avoir été modifiées depuis le dashboard pendant que le
                # bot tournait. Sans cette relecture, il continuerait de
                # raisonner sur le carnet qu'il a chargé à son démarrage
                # — et ne verrait jamais un achat saisi entre-temps.
                self.positions = self._load_positions()

                # Calendrier des résultats : rafraîchi au plus une fois
                # par jour. C'est le bot qui s'en charge parce qu'il a le
                # temps — interroger 49 valeurs prend des dizaines de
                # secondes, et le serveur web ne doit jamais attendre le
                # réseau pour afficher une page.
                if engine.cache_earnings_perime():
                    n = engine.rafraichir_earnings(self.tickers)
                    if n is not None:
                        console.print(f"[dim]Calendrier des résultats : "
                                      f"{n} dates connues[/dim]")

                analyses = []
                # Une seule requête groupée par cycle. En séquentiel,
                # quarante-neuf allers-retours prendraient une bonne
                # partie de l'intervalle de 300 s ; groupés, ils tiennent
                # en quelques secondes.
                frames = self.fetch_many(self.tickers)
                for ticker in self.tickers:
                    df = frames.get(ticker)
                    if df is not None and len(df) > 50:
                        try:
                            analyses.append(self.analyze(ticker, df))
                        except Exception as e:
                            console.print(f"[red]Erreur analyse {ticker}: {e}[/red]")

                if self.use_dashboard:
                    self.render_dashboard(analyses)

                # Le classement par momentum est établi avant tout le
                # reste : c'est lui qui pilote désormais les alertes.
                ranked = self.rank_by_momentum(analyses)
                self.check_rebalance(ranked)
                self.check_held_positions(ranked)

                # Les changements de score technique restent HISTORISÉS —
                # ils décrivent fidèlement l'état d'une valeur et servent
                # au graphique — mais ils ne déclenchent plus de
                # notification : la mesure a montré qu'ils n'annoncent
                # rien, et sonner pour un signal sans valeur prédictive
                # apprend au lecteur à ignorer ses propres alertes.
                for a in analyses:
                    ticker = a["ticker"]
                    prev = self.last_signals.get(ticker)
                    signal_changed = prev is not None and prev != a["recommendation"]

                    if signal_changed and a["recommendation"] in ("BUY", "SELL"):
                        console.print(f"[dim]{ticker} → {a['recommendation']} "
                                      f"(score technique, non notifié)[/dim]")
                        self._add_to_history(a, signal_change=True)
                    elif self._due_for_snapshot(ticker):
                        self._add_to_history(a, signal_change=False)

                    self.last_signals[ticker] = a["recommendation"]

            except KeyboardInterrupt:
                break
            except Exception as e:
                console.print(f"[red]Erreur: {e}[/red]")

            if self.running:
                time.sleep(LOOP_INTERVAL)

    def stop(self):
        console.print("\n[yellow]Arrêt du bot...[/yellow]")
        self.running = False

    def add_position(self, ticker, quantity, price):
        """Enregistre un achat manuel."""
        self.positions[ticker.upper()] = {
            "quantity": float(quantity),
            "entry_price": float(price),
            "entry_date": datetime.now().isoformat(),
        }
        self._save_positions()
        console.print(f"[green]✓ Position enregistrée: {quantity} {ticker} @ ${price}[/green]")

    def remove_position(self, ticker):
        """Supprime une position."""
        if ticker.upper() in self.positions:
            del self.positions[ticker.upper()]
            self._save_positions()
            console.print(f"[green]✓ Position {ticker} supprimée[/green]")
        else:
            console.print(f"[red]Position {ticker} non trouvée[/red]")


def show_history(bot, ticker_filter=None, last=20):
    """Affiche l'historique des signaux."""
    if not bot.signal_history:
        console.print("[dim]Aucun historique disponible[/dim]")
        return

    history = bot.signal_history
    if ticker_filter:
        history = [h for h in history if h["ticker"] == ticker_filter.upper()]

    # Afficher les changements de signal uniquement
    changes = [h for h in history if h.get("is_change")]

    console.print(Panel(
        f"[bold cyan]Historique des Signaux[/bold cyan]\n"
        f"Total entrées: {len(history)} | Changements de signal: {len(changes)}",
        border_style="cyan"
    ))

    # Tableau des changements de signal
    if changes:
        t = Table(title="🔔 Changements de Signal", show_header=True, header_style="bold magenta")
        t.add_column("Date/Heure", style="dim", width=18)
        t.add_column("Action", style="bold", width=8)
        t.add_column("Signal", justify="center", width=10)
        t.add_column("Prix", justify="right", width=10)
        t.add_column("RSI", justify="right", width=6)
        t.add_column("Score B/S", justify="center", width=10)
        t.add_column("Indicateurs", width=40)

        for h in changes[-last:]:
            ts = h["timestamp"][:16].replace("T", " ")
            sig_styles = {
                "BUY": "[bold green]🟢 BUY[/bold green]",
                "SELL": "[bold red]🔴 SELL[/bold red]",
                "WATCH": "[yellow]👀 WATCH[/yellow]",
                "HOLD": "[dim]HOLD[/dim]",
            }
            rsi_s = "green" if h["rsi"] < 30 else ("red" if h["rsi"] > 70 else "yellow")
            t.add_row(
                ts, h["ticker"],
                sig_styles.get(h["recommendation"], h["recommendation"]),
                f"${h['price']:.2f}",
                f"[{rsi_s}]{h['rsi']:.0f}[/{rsi_s}]",
                f"{h['buy_score']}/{h['sell_score']}",
                ", ".join(h.get("signals", [])) or "-"
            )
        console.print(t)
    else:
        console.print("[dim]Aucun changement de signal enregistré pour le moment[/dim]")

    # Stats par action
    if not ticker_filter:
        console.print()
        ticker_stats = {}
        for h in history:
            tk = h["ticker"]
            if tk not in ticker_stats:
                ticker_stats[tk] = {"buy": 0, "sell": 0, "watch": 0, "hold": 0}
            ticker_stats[tk][h["recommendation"].lower()] = ticker_stats[tk].get(h["recommendation"].lower(), 0) + 1

        st = Table(title="📊 Statistiques par Action", show_header=True, header_style="bold yellow")
        st.add_column("Action", style="bold")
        st.add_column("BUY", justify="right")
        st.add_column("SELL", justify="right")
        st.add_column("WATCH", justify="right")
        st.add_column("HOLD", justify="right")

        for tk, stats in sorted(ticker_stats.items()):
            st.add_row(
                tk,
                f"[green]{stats.get('buy', 0)}[/green]",
                f"[red]{stats.get('sell', 0)}[/red]",
                f"[yellow]{stats.get('watch', 0)}[/yellow]",
                f"[dim]{stats.get('hold', 0)}[/dim]"
            )
        console.print(st)


def main():
    parser = argparse.ArgumentParser(description="Stock Signal Bot")
    parser.add_argument("--tickers", type=str, default=None, help="Actions à surveiller (séparées par virgule)")
    parser.add_argument("--capital", type=float, default=CAPITAL, help="Capital simulé")
    parser.add_argument("--add", nargs=3, metavar=("TICKER", "QTY", "PRICE"), help="Ajouter une position")
    parser.add_argument("--remove", type=str, help="Supprimer une position")
    parser.add_argument("--list", action="store_true", help="Lister les positions")
    parser.add_argument("--history", action="store_true", help="Afficher l'historique des signaux")
    parser.add_argument("--history-ticker", type=str, help="Historique d'une action spécifique")
    parser.add_argument("--test-discord", action="store_true", help="Tester la notification Discord")
    parser.add_argument("--legacy", action="store_true",
                        help="Utiliser l'ancien score v1 à poids fixes (comparaison)")
    args = parser.parse_args()

    tickers = args.tickers.split(",") if args.tickers else DEFAULT_TICKERS
    bot = StockSignalBot(tickers, args.capital, legacy=args.legacy)

    if args.add:
        ticker, qty, price = args.add
        bot.add_position(ticker, qty, price)
        return

    if args.remove:
        bot.remove_position(args.remove)
        return

    if args.list:
        if not bot.positions:
            console.print("[dim]Aucune position[/dim]")
        else:
            t = Table(title="Positions")
            t.add_column("Ticker"); t.add_column("Quantité"); t.add_column("Prix d'entrée")
            for tk, p in bot.positions.items():
                t.add_row(tk, str(p["quantity"]), f"${p['entry_price']}")
            console.print(t)
        return

    if args.history or args.history_ticker:
        show_history(bot, ticker_filter=args.history_ticker)
        return

    if args.test_discord:
        if not bot.discord_enabled:
            console.print("[red]❌ Discord webhook non configuré dans .env[/red]")
            console.print("[dim]Ajoute DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... dans ton .env[/dim]")
            return
        console.print("[cyan]Envoi d'un message test sur Discord...[/cyan]")
        test_analysis = {
            "ticker": "TEST", "price": 100.0, "rsi": 50, "buy_score": 5, "sell_score": 0,
            "signals": ["Test signal 1", "Test signal 2"],
            "change_1d": 1.2, "change_1w": 5.4, "change_1m": 15.8,
        }
        bot._send_discord("TEST", "BUY", test_analysis)
        console.print("[green]✓ Message envoyé ! Vérifie ton Discord.[/green]")
        return

    # --- Verrou d'instance unique ---
    existing = running_instance()
    if existing:
        console.print(f"[red]❌ Un bot tourne déjà (PID {existing}).[/red]")
        console.print("[dim]./manage_bot.sh stop pour l'arrêter, "
                      "./manage_bot.sh status pour le voir.[/dim]")
        return

    os.makedirs("logs", exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    def cleanup():
        """Retire le fichier PID — mais seulement s'il est bien le nôtre."""
        try:
            if os.path.exists(PID_FILE) and open(PID_FILE).read().strip() == str(os.getpid()):
                os.remove(PID_FILE)
        except OSError:
            pass

    def handle_shutdown(signum, frame):
        bot.stop()
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)   # envoyé par manage_bot.sh et launchd

    try:
        bot.run()
    finally:
        cleanup()


if __name__ == "__main__":
    main()
