import os
import json
from datetime import datetime

import config
from logger import get_logger

log = get_logger("portfolio")


class Portfolio:
    def __init__(self):
        os.makedirs(config.DATA_DIR, exist_ok=True)
        self.trades_file = os.path.join(config.DATA_DIR, "trades.json")
        self.trades = self._load_trades()
        self.entry_price = None
        self.entry_time = None

    def _load_trades(self):
        if os.path.exists(self.trades_file):
            with open(self.trades_file, "r") as f:
                data = json.load(f)
                log.info(f"Historique chargé: {len(data)} trades")
                return data
        return []

    def _save_trades(self):
        with open(self.trades_file, "w") as f:
            json.dump(self.trades, f, indent=2, default=str)

    def open_position(self, price, amount, symbol):
        self.entry_price = price
        self.entry_time = datetime.now().isoformat()
        log.info(f"Position ouverte: {amount} {symbol} à {price:.2f}")

    def close_position(self, exit_price, amount, symbol):
        if self.entry_price is None:
            log.warning("Tentative de fermer une position inexistante")
            return None

        pnl = (exit_price - self.entry_price) * amount
        pnl_pct = ((exit_price - self.entry_price) / self.entry_price) * 100

        trade = {
            "symbol": symbol,
            "entry_price": self.entry_price,
            "exit_price": exit_price,
            "amount": amount,
            "pnl": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 2),
            "entry_time": self.entry_time,
            "exit_time": datetime.now().isoformat(),
            "result": "WIN" if pnl > 0 else "LOSS",
        }

        self.trades.append(trade)
        self._save_trades()

        emoji = "+" if pnl >= 0 else ""
        log.info(
            f"Position fermée: {symbol} | P&L: {emoji}{pnl:.4f} USDT ({emoji}{pnl_pct:.2f}%)"
        )

        self.entry_price = None
        self.entry_time = None
        return trade

    def get_unrealized_pnl(self, current_price):
        if self.entry_price is None:
            return 0, 0
        pnl_pct = ((current_price - self.entry_price) / self.entry_price) * 100
        return current_price - self.entry_price, pnl_pct

    def get_stats(self):
        if not self.trades:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0,
                "total_pnl": 0,
                "avg_win": 0,
                "avg_loss": 0,
                "best_trade": 0,
                "worst_trade": 0,
                "profit_factor": 0,
            }

        wins = [t for t in self.trades if t["pnl"] > 0]
        losses = [t for t in self.trades if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in self.trades)
        total_wins = sum(t["pnl"] for t in wins) if wins else 0
        total_losses = abs(sum(t["pnl"] for t in losses)) if losses else 0

        return {
            "total_trades": len(self.trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(self.trades) * 100, 1) if self.trades else 0,
            "total_pnl": round(total_pnl, 4),
            "avg_win": round(total_wins / len(wins), 4) if wins else 0,
            "avg_loss": round(total_losses / len(losses), 4) if losses else 0,
            "best_trade": round(max(t["pnl"] for t in self.trades), 4),
            "worst_trade": round(min(t["pnl"] for t in self.trades), 4),
            "profit_factor": round(total_wins / total_losses, 2) if total_losses > 0 else float("inf"),
        }

    def get_recent_trades(self, count=5):
        return self.trades[-count:]

    def has_open_position(self):
        return self.entry_price is not None

    def get_entry_price(self):
        return self.entry_price
