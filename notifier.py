import json
from datetime import datetime

import requests

import config
from logger import get_logger

log = get_logger("notifier")


class Notifier:
    def __init__(self):
        self.enabled = config.DISCORD_ENABLED and bool(config.DISCORD_WEBHOOK_URL)
        if self.enabled:
            log.info("Notifications Discord activées")
        else:
            log.debug("Notifications Discord désactivées")

    def _send_discord(self, embed):
        if not self.enabled:
            return
        payload = {"embeds": [embed]}
        try:
            resp = requests.post(
                config.DISCORD_WEBHOOK_URL,
                json=payload,
                timeout=10,
            )
            if resp.status_code != 204:
                log.warning(f"Discord webhook erreur: {resp.status_code}")
        except Exception as e:
            log.error(f"Erreur envoi Discord: {e}")

    def notify_trade(self, side, symbol, price, amount, pnl=None):
        color = 0x00FF00 if side == "BUY" else 0xFF0000
        title = f"{'ACHAT' if side == 'BUY' else 'VENTE'} — {symbol}"

        fields = [
            {"name": "Prix", "value": f"${price:,.2f}", "inline": True},
            {"name": "Quantité", "value": f"{amount:.6f}", "inline": True},
        ]
        if pnl is not None:
            emoji = "+" if pnl >= 0 else ""
            fields.append({"name": "P&L", "value": f"{emoji}{pnl:.4f} USDT", "inline": True})

        embed = {
            "title": title,
            "color": color,
            "fields": fields,
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {"text": "Trading Bot"},
        }
        self._send_discord(embed)
        log.debug(f"Notification trade envoyée: {side} {symbol}")

    def notify_risk_alert(self, alert_type, message):
        embed = {
            "title": f"ALERTE RISQUE — {alert_type}",
            "description": message,
            "color": 0xFF6600,
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {"text": "Trading Bot — Risk Manager"},
        }
        self._send_discord(embed)
        log.warning(f"Alerte risque envoyée: {alert_type} — {message}")

    def notify_bot_status(self, status, details=""):
        color = 0x00FF00 if status == "START" else 0xFF0000
        embed = {
            "title": f"Bot {'Démarré' if status == 'START' else 'Arrêté'}",
            "description": details,
            "color": color,
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {"text": "Trading Bot"},
        }
        self._send_discord(embed)

    def notify_summary(self, stats, balance):
        fields = [
            {"name": "Total Trades", "value": str(stats["total_trades"]), "inline": True},
            {"name": "Win Rate", "value": f"{stats['win_rate']}%", "inline": True},
            {"name": "P&L Total", "value": f"{stats['total_pnl']:.4f} USDT", "inline": True},
            {"name": "Profit Factor", "value": str(stats["profit_factor"]), "inline": True},
            {"name": "Solde", "value": f"{balance:.2f} USDT", "inline": True},
        ]
        embed = {
            "title": "Résumé de Session",
            "color": 0x0099FF,
            "fields": fields,
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {"text": "Trading Bot"},
        }
        self._send_discord(embed)
