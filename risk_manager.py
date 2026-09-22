import time
from datetime import datetime, date

import config
from logger import get_logger

log = get_logger("risk_manager")


class RiskManager:
    def __init__(self):
        self.stop_loss_pct = config.STOP_LOSS_PCT / 100
        self.take_profit_pct = config.TAKE_PROFIT_PCT / 100
        self.max_drawdown_pct = config.MAX_DRAWDOWN_PCT / 100
        self.position_size_pct = config.POSITION_SIZE_PCT / 100
        self.cooldown_seconds = config.TRADE_COOLDOWN
        self.max_trades_per_day = config.MAX_TRADES_PER_DAY

        self.last_trade_time = 0
        self.trades_today = 0
        self.trades_today_date = date.today()
        self.initial_balance = None
        self.peak_balance = None

    def set_initial_balance(self, balance):
        self.initial_balance = balance
        self.peak_balance = balance
        log.info(f"Solde initial enregistré: {balance:.2f} USDT")

    def update_balance(self, current_balance):
        if self.peak_balance is None:
            self.peak_balance = current_balance
            return
        if current_balance > self.peak_balance:
            self.peak_balance = current_balance

    def calculate_position_size(self, balance, price):
        usdt_amount = balance * self.position_size_pct
        quantity = usdt_amount / price
        log.debug(
            f"Position sizing: {self.position_size_pct*100}% de {balance:.2f} USDT = "
            f"{usdt_amount:.2f} USDT = {quantity:.6f} unités à {price:.2f}"
        )
        return quantity

    def check_stop_loss(self, entry_price, current_price):
        if entry_price is None:
            return False
        loss_pct = (entry_price - current_price) / entry_price
        if loss_pct >= self.stop_loss_pct:
            log.warning(
                f"STOP-LOSS déclenché: perte de {loss_pct*100:.2f}% "
                f"(seuil: {self.stop_loss_pct*100:.1f}%)"
            )
            return True
        return False

    def check_take_profit(self, entry_price, current_price):
        if entry_price is None:
            return False
        profit_pct = (current_price - entry_price) / entry_price
        if profit_pct >= self.take_profit_pct:
            log.info(
                f"TAKE-PROFIT déclenché: gain de {profit_pct*100:.2f}% "
                f"(seuil: {self.take_profit_pct*100:.1f}%)"
            )
            return True
        return False

    def check_max_drawdown(self, current_balance):
        if self.peak_balance is None or self.peak_balance == 0:
            return False
        drawdown = (self.peak_balance - current_balance) / self.peak_balance
        if drawdown >= self.max_drawdown_pct:
            log.critical(
                f"MAX DRAWDOWN atteint: {drawdown*100:.2f}% "
                f"(seuil: {self.max_drawdown_pct*100:.1f}%) — Trading suspendu"
            )
            return True
        return False

    def check_cooldown(self):
        elapsed = time.time() - self.last_trade_time
        if elapsed < self.cooldown_seconds:
            remaining = int(self.cooldown_seconds - elapsed)
            log.info(f"Cooldown actif: {remaining}s restantes")
            return False
        return True

    def check_daily_limit(self):
        today = date.today()
        if today != self.trades_today_date:
            self.trades_today = 0
            self.trades_today_date = today

        if self.trades_today >= self.max_trades_per_day:
            log.warning(
                f"Limite quotidienne atteinte: {self.trades_today}/{self.max_trades_per_day} trades"
            )
            return False
        return True

    def can_trade(self, current_balance):
        if not self.check_cooldown():
            return False, "cooldown"
        if not self.check_daily_limit():
            return False, "daily_limit"
        if self.check_max_drawdown(current_balance):
            return False, "max_drawdown"
        return True, "ok"

    def record_trade(self):
        self.last_trade_time = time.time()
        self.trades_today += 1
        log.debug(f"Trade enregistré — {self.trades_today}/{self.max_trades_per_day} aujourd'hui")

    def get_status(self):
        today = date.today()
        if today != self.trades_today_date:
            trades = 0
        else:
            trades = self.trades_today

        drawdown = 0
        if self.peak_balance and self.initial_balance:
            drawdown = ((self.peak_balance - (self.initial_balance or self.peak_balance))
                        / self.peak_balance * 100) if self.peak_balance > 0 else 0

        return {
            "trades_today": trades,
            "max_trades": self.max_trades_per_day,
            "cooldown_remaining": max(0, int(self.cooldown_seconds - (time.time() - self.last_trade_time))),
            "stop_loss_pct": self.stop_loss_pct * 100,
            "take_profit_pct": self.take_profit_pct * 100,
            "max_drawdown_pct": self.max_drawdown_pct * 100,
            "peak_balance": self.peak_balance,
        }
