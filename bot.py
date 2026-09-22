import sys
import time
import signal
import argparse

import config
from logger import get_logger
from exchange import Exchange
from strategy import SmaRsiStrategy
from risk_manager import RiskManager
from portfolio import Portfolio
from notifier import Notifier

log = get_logger("bot")


class TradingBot:
    def __init__(self, use_dashboard=False):
        log.info("Initialisation du bot...")
        self.exchange = Exchange()
        self.strategy = SmaRsiStrategy()
        self.risk_manager = RiskManager()
        self.portfolio = Portfolio()
        self.notifier = Notifier()
        self.running = True
        self.use_dashboard = use_dashboard
        self.dashboard = None
        self.last_indicators = {}

        if use_dashboard:
            from dashboard import Dashboard
            self.dashboard = Dashboard()

    def _execute_buy(self, price):
        balance = self.exchange.get_balance()
        amount = self.risk_manager.calculate_position_size(balance, price)

        if amount <= 0:
            log.warning("Quantité calculée nulle — achat annulé")
            return

        if config.DRY_RUN:
            log.info(f"[DRY-RUN] ACHAT simulé: {amount:.6f} {config.SYMBOL} à {price:.2f}")
        else:
            order = self.exchange.place_order("buy", amount)
            price = order.get("average") or price

        self.portfolio.open_position(price, amount, config.SYMBOL)
        self.risk_manager.record_trade()
        self.notifier.notify_trade("BUY", config.SYMBOL, price, amount)
        log.info(f"ACHAT {'(simulé) ' if config.DRY_RUN else ''}exécuté: {amount:.6f} à {price:.2f}")

    def _execute_sell(self, price, reason="signal"):
        if not self.portfolio.has_open_position():
            return

        entry_price = self.portfolio.get_entry_price()
        balance = self.exchange.get_balance()
        amount = self.risk_manager.calculate_position_size(balance, price)

        if config.DRY_RUN:
            log.info(f"[DRY-RUN] VENTE simulée: {amount:.6f} {config.SYMBOL} à {price:.2f} ({reason})")
        else:
            order = self.exchange.place_order("sell", amount)
            price = order.get("average") or price

        trade = self.portfolio.close_position(price, amount, config.SYMBOL)
        self.risk_manager.record_trade()

        pnl = trade["pnl"] if trade else None
        self.notifier.notify_trade("SELL", config.SYMBOL, price, amount, pnl)
        log.info(
            f"VENTE {'(simulée) ' if config.DRY_RUN else ''}exécutée: "
            f"{amount:.6f} à {price:.2f} ({reason}) | "
            f"P&L: {pnl:.4f} USDT" if pnl else ""
        )

    def run_cycle(self):
        log.info(f"--- Cycle {config.SYMBOL} ({config.TIMEFRAME}) ---")

        # Récupérer les données
        df = self.exchange.fetch_ohlcv()
        signal_type, indicators = self.strategy.evaluate(df)
        self.last_indicators = indicators
        price = indicators["price"]

        log.info(
            f"Prix: {price:.2f} | "
            f"SMA: {indicators['sma_short']}/{indicators['sma_long']} | "
            f"EMA: {indicators['ema_short']}/{indicators['ema_long']} | "
            f"RSI: {indicators['rsi']} | "
            f"MACD: {indicators['macd_hist']}"
        )
        log.info(
            f"Bollinger: [{indicators['bb_lower']} — {indicators['bb_upper']}] | "
            f"Signal: {signal_type} (confiance: {indicators['confidence']}/5)"
        )

        # Vérifier stop-loss et take-profit sur position ouverte
        if self.portfolio.has_open_position():
            entry = self.portfolio.get_entry_price()
            unrealized_pnl, unrealized_pct = self.portfolio.get_unrealized_pnl(price)
            log.info(f"Position ouverte — Entrée: {entry:.2f} | P&L latent: {unrealized_pnl:.4f} ({unrealized_pct:+.2f}%)")

            if self.risk_manager.check_stop_loss(entry, price):
                self._execute_sell(price, reason="stop-loss")
                self.notifier.notify_risk_alert("STOP-LOSS", f"Vente forcée à {price:.2f}")
                return

            if self.risk_manager.check_take_profit(entry, price):
                self._execute_sell(price, reason="take-profit")
                return

        # Vérifier les limites de risque
        balance = self.exchange.get_balance()
        self.risk_manager.update_balance(balance)
        can_trade, reason = self.risk_manager.can_trade(balance)

        if not can_trade:
            log.info(f"Trading bloqué: {reason}")
            if reason == "max_drawdown":
                self.notifier.notify_risk_alert("MAX DRAWDOWN", "Trading suspendu")
            return

        # Exécuter selon le signal
        if signal_type == "BUY" and not self.portfolio.has_open_position():
            self._execute_buy(price)
        elif signal_type == "SELL" and self.portfolio.has_open_position():
            self._execute_sell(price, reason="signal")
        else:
            log.info("Aucune action")

    def print_summary(self):
        stats = self.portfolio.get_stats()
        balance = self.exchange.get_balance()

        log.info("=" * 50)
        log.info("RÉSUMÉ DE SESSION")
        log.info("=" * 50)
        log.info(f"Trades totaux: {stats['total_trades']}")
        log.info(f"Wins/Losses: {stats['wins']}/{stats['losses']}")
        log.info(f"Win Rate: {stats['win_rate']}%")
        log.info(f"P&L Total: {stats['total_pnl']:.4f} USDT")
        log.info(f"Profit Factor: {stats['profit_factor']}")
        log.info(f"Meilleur trade: {stats['best_trade']:.4f} USDT")
        log.info(f"Pire trade: {stats['worst_trade']:.4f} USDT")
        log.info(f"Solde actuel: {balance:.2f} USDT")
        log.info("=" * 50)

        self.notifier.notify_summary(stats, balance)

    def run(self):
        mode = "DRY-RUN" if config.DRY_RUN else ("TESTNET" if config.TESTNET else "PRODUCTION")
        log.info(f"Bot démarré — {config.SYMBOL} | Mode: {mode} | Intervalle: {config.LOOP_INTERVAL}s")

        balance = self.exchange.get_balance()
        log.info(f"Solde USDT: {balance:.2f}")
        self.risk_manager.set_initial_balance(balance)
        self.notifier.notify_bot_status("START", f"Mode: {mode} | Solde: {balance:.2f} USDT")

        while self.running:
            try:
                self.run_cycle()

                if self.dashboard:
                    self.dashboard.render(
                        indicators=self.last_indicators,
                        portfolio=self.portfolio,
                        risk_manager=self.risk_manager,
                        balance=self.exchange.get_balance(),
                        symbol=config.SYMBOL,
                        mode=mode,
                    )

            except KeyboardInterrupt:
                break
            except Exception as e:
                log.error(f"Erreur dans le cycle: {e}")

            if self.running:
                log.debug(f"Prochain cycle dans {config.LOOP_INTERVAL}s...")
                time.sleep(config.LOOP_INTERVAL)

    def stop(self):
        log.info("Arrêt du bot...")
        self.running = False
        self.print_summary()
        self.notifier.notify_bot_status("STOP", "Arrêt propre du bot")


def main():
    parser = argparse.ArgumentParser(description="Bot de Trading Crypto")
    parser.add_argument("--dashboard", action="store_true", help="Activer le dashboard terminal")
    args = parser.parse_args()

    bot = TradingBot(use_dashboard=args.dashboard)

    def handle_shutdown(signum, frame):
        bot.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    bot.run()


if __name__ == "__main__":
    main()
