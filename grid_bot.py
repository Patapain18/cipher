"""
Grid Bot — Configuration optimale trouvée sur 68 268 tests.

Best config: DOGE/USDT | 4h | 3 niveaux | 0.8% espacement | ROI +14%

Usage:
    python3 grid_bot.py                  # Mode DRY-RUN (simulation)
    python3 grid_bot.py --live           # Mode LIVE (vrai argent ⚠️)
    python3 grid_bot.py --dashboard      # Avec dashboard terminal
"""

import time
import signal
import sys
import argparse
import os
from datetime import datetime

import config
from exchange import Exchange
from grid_strategy import GridStrategy
from portfolio import Portfolio
from risk_manager import RiskManager
from notifier import Notifier
from logger import get_logger

log = get_logger("grid_bot")


# Config optimale trouvée par ultra_test.py
GRID_SYMBOL = os.getenv("GRID_SYMBOL", "DOGE/USDT")
GRID_TIMEFRAME = os.getenv("GRID_TIMEFRAME", "4h")
GRID_LEVELS = int(os.getenv("GRID_LEVELS", "3"))
GRID_SPACING = float(os.getenv("GRID_SPACING", "0.8"))
GRID_CAPITAL_PCT = float(os.getenv("GRID_CAPITAL_PCT", "50"))
GRID_LOOP_INTERVAL = int(os.getenv("GRID_LOOP_INTERVAL", "30"))
GRID_MAX_CAPITAL = float(os.getenv("GRID_MAX_CAPITAL", "20"))


class GridBot:
    def __init__(self, dry_run=True, use_dashboard=False):
        log.info("Initialisation du Grid Bot...")
        self.exchange = Exchange()
        self.portfolio = Portfolio()
        self.risk_manager = RiskManager()
        self.notifier = Notifier()
        self.dry_run = dry_run
        self.running = True
        self.use_dashboard = use_dashboard
        self.dashboard = None

        # Grid state
        self.grid = None
        self.grid_initialized = False
        self.holdings = 0
        self.initial_capital = 0
        self.cash = 0
        self.total_trades = 0
        self.total_profit = 0
        self.wins = 0
        self.losses = 0
        self.grid_orders = []

        if use_dashboard:
            from dashboard_grid import GridDashboard
            self.dashboard = GridDashboard()

    def setup(self):
        """Initialise la grille autour du prix actuel."""
        price = self.exchange.get_ticker(GRID_SYMBOL)

        if self.dry_run:
            # En DRY-RUN on utilise le capital virtuel configuré
            balance = GRID_MAX_CAPITAL
            log.info(f"Mode DRY-RUN — Capital virtuel: {balance:.2f} USDT")
        else:
            full_balance = self.exchange.get_balance()
            balance = min(full_balance, GRID_MAX_CAPITAL)
            log.info(f"Solde Binance : {full_balance:.2f} USDT")
            log.info(f"Capital utilisé: {balance:.2f} USDT (max: {GRID_MAX_CAPITAL})")

        self.initial_capital = balance
        self.cash = balance
        self.risk_manager.set_initial_balance(balance)

        log.info(f"{'='*50}")
        log.info(f"GRID BOT — Configuration Optimale")
        log.info(f"{'='*50}")
        log.info(f"Paire       : {GRID_SYMBOL}")
        log.info(f"Timeframe   : {GRID_TIMEFRAME}")
        log.info(f"Mode        : {'DRY-RUN (simulation)' if self.dry_run else 'LIVE (argent réel) ⚠️'}")
        log.info(f"Solde       : {balance:.2f} USDT")
        log.info(f"Prix actuel : {price:.6f} USDT")
        log.info(f"Niveaux     : {GRID_LEVELS} (au-dessus + en-dessous)")
        log.info(f"Espacement  : {GRID_SPACING}%")
        log.info(f"{'='*50}")

        # Créer la grille
        self.grid = GridStrategy(
            grid_levels=GRID_LEVELS,
            grid_spacing_pct=GRID_SPACING,
        )
        grid_capital = balance * (GRID_CAPITAL_PCT / 100)
        self.grid.setup_grid(price, grid_capital)
        self.grid_initialized = True

        # Afficher les niveaux
        log.info("Niveaux de la grille:")
        for g in self.grid.grids:
            side = "ACHAT " if g["side"] == "buy" else "VENTE"
            log.info(f"  [{side}] {g['price']:.6f} USDT (niveau {g['level']:+d})")

        self.notifier.notify_bot_status("START",
            f"Grid Bot démarré\n"
            f"Paire: {GRID_SYMBOL}\n"
            f"Mode: {'DRY-RUN' if self.dry_run else 'LIVE'}\n"
            f"Solde: {balance:.2f} USDT\n"
            f"Niveaux: {GRID_LEVELS} × {GRID_SPACING}%"
        )

    def run_cycle(self):
        """Vérifie si le prix a touché un niveau de la grille."""
        price = self.exchange.get_ticker(GRID_SYMBOL)
        timestamp = datetime.now().strftime("%H:%M:%S")

        # Vérifier les niveaux de la grille
        actions = self.grid.evaluate(price)

        if not actions:
            log.debug(f"Prix: {price:.6f} | Aucun niveau touché")
        else:
            for action in actions:
                side = action["action"]
                grid_price = action["price"]
                amount = action["amount"]
                level = action["level"]

                if side == "BUY":
                    if self.dry_run:
                        cost = amount * price
                        log.info(f"[DRY-RUN] ACHAT Grid Lvl{level:+d} | "
                                f"Prix: {price:.6f} | Qté: {amount:.2f} DOGE | "
                                f"Coût: {cost:.4f} USDT")
                    else:
                        try:
                            self.exchange.place_market_order("buy", amount, GRID_SYMBOL)
                        except Exception as e:
                            log.error(f"Erreur achat: {e}")
                            continue

                    self.holdings += amount
                    self.total_trades += 1

                    self.notifier.notify_trade("BUY", GRID_SYMBOL, price, amount)
                    self.grid_orders.append({
                        "time": datetime.now().isoformat(),
                        "side": "BUY",
                        "price": price,
                        "amount": amount,
                        "level": level,
                    })

                elif side == "SELL":
                    profit = amount * (price - grid_price)

                    if self.dry_run:
                        log.info(f"[DRY-RUN] VENTE Grid Lvl{level:+d} | "
                                f"Prix: {price:.6f} | Qté: {amount:.2f} DOGE | "
                                f"Profit: {profit:+.4f} USDT")
                    else:
                        try:
                            self.exchange.place_market_order("sell", amount, GRID_SYMBOL)
                        except Exception as e:
                            log.error(f"Erreur vente: {e}")
                            continue

                    self.holdings -= amount
                    self.total_trades += 1
                    self.total_profit += profit

                    if profit > 0:
                        self.wins += 1
                    else:
                        self.losses += 1

                    self.notifier.notify_trade("SELL", GRID_SYMBOL, price, amount, profit)
                    self.grid_orders.append({
                        "time": datetime.now().isoformat(),
                        "side": "SELL",
                        "price": price,
                        "amount": amount,
                        "level": level,
                        "profit": profit,
                    })

        # Dashboard
        if self.dashboard:
            equity = self.cash + self.holdings * price
            self.dashboard.render(
                price=price,
                grid=self.grid,
                holdings=self.holdings,
                cash=self.cash,
                equity=equity,
                initial=self.initial_capital,
                trades=self.total_trades,
                wins=self.wins,
                losses=self.losses,
                profit=self.total_profit,
                orders=self.grid_orders[-10:],
                symbol=GRID_SYMBOL,
                dry_run=self.dry_run,
            )

    def print_summary(self):
        """Affiche le résumé de session."""
        try:
            price = self.exchange.get_ticker(GRID_SYMBOL)
            equity = self.cash + self.holdings * price
            roi = ((equity - self.initial_capital) / self.initial_capital) * 100 if self.initial_capital > 0 else 0
        except:
            equity = self.cash
            roi = 0

        log.info(f"{'='*50}")
        log.info(f"RÉSUMÉ DE SESSION — Grid Bot")
        log.info(f"{'='*50}")
        log.info(f"Trades totaux : {self.total_trades}")
        log.info(f"Wins/Losses   : {self.wins}/{self.losses}")
        log.info(f"Win Rate      : {self.wins/self.total_trades*100:.1f}%" if self.total_trades > 0 else "Win Rate      : N/A")
        log.info(f"Profit Grid   : {self.total_profit:+.4f} USDT")
        log.info(f"Holdings      : {self.holdings:.2f} DOGE")
        log.info(f"ROI           : {roi:+.2f}%")
        log.info(f"{'='*50}")

        grid_status = self.grid.get_grid_status() if self.grid else {}
        self.notifier.notify_summary(
            {"total_trades": self.total_trades, "wins": self.wins, "losses": self.losses,
             "win_rate": round(self.wins/self.total_trades*100, 1) if self.total_trades > 0 else 0,
             "total_pnl": round(self.total_profit, 4), "profit_factor": 0},
            equity
        )

    def run(self):
        """Boucle principale."""
        self.setup()

        while self.running:
            try:
                self.run_cycle()
            except KeyboardInterrupt:
                break
            except Exception as e:
                log.error(f"Erreur dans le cycle: {e}")

            if self.running:
                time.sleep(GRID_LOOP_INTERVAL)

    def stop(self):
        log.info("Arrêt du Grid Bot...")
        self.running = False
        self.print_summary()


def main():
    parser = argparse.ArgumentParser(description="Grid Bot — Config optimale DOGE/USDT")
    parser.add_argument("--live", action="store_true", help="Mode LIVE (vrai argent ⚠️)")
    parser.add_argument("--dashboard", action="store_true", help="Dashboard terminal")
    args = parser.parse_args()

    dry_run = not args.live

    if args.live:
        print("\n⚠️  MODE LIVE — Vous allez trader avec de l'argent réel !")
        print("    Paire: DOGE/USDT | Grid: 3 niveaux × 0.8%")
        confirm = input("    Tapez 'OUI' pour confirmer: ")
        if confirm != "OUI":
            print("Annulé.")
            return

    bot = GridBot(dry_run=dry_run, use_dashboard=args.dashboard)

    def handle_shutdown(signum, frame):
        bot.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    bot.run()


if __name__ == "__main__":
    main()
