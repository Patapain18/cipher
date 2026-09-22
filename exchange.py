import time

import ccxt
import pandas as pd

import config
from logger import get_logger

log = get_logger("exchange")

MAX_RETRIES = 3
RETRY_DELAY = 2


class Exchange:
    def __init__(self):
        self.client = ccxt.binance({
            "apiKey": config.API_KEY,
            "secret": config.API_SECRET,
            "enableRateLimit": True,
        })
        if config.TESTNET:
            self.client.set_sandbox_mode(True)
            log.info("Mode TESTNET activé")
        else:
            log.warning("Mode PRODUCTION — argent réel !")

    def _retry(self, fn, *args, **kwargs):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return fn(*args, **kwargs)
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as e:
                delay = RETRY_DELAY * attempt
                log.warning(f"Erreur réseau (tentative {attempt}/{MAX_RETRIES}): {e} — retry dans {delay}s")
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(delay)
            except ccxt.ExchangeError as e:
                log.error(f"Erreur exchange: {e}")
                raise

    def fetch_ohlcv(self, symbol=None, timeframe=None, limit=100):
        symbol = symbol or config.SYMBOL
        timeframe = timeframe or config.TIMEFRAME
        data = self._retry(self.client.fetch_ohlcv, symbol, timeframe, limit=limit)
        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df

    def get_balance(self, currency="USDT"):
        balance = self._retry(self.client.fetch_balance)
        return balance.get(currency, {}).get("free", 0)

    def get_ticker(self, symbol=None):
        symbol = symbol or config.SYMBOL
        ticker = self._retry(self.client.fetch_ticker, symbol)
        return ticker["last"]

    def check_min_balance(self, currency="USDT", min_amount=10):
        balance = self.get_balance(currency)
        if balance < min_amount:
            log.warning(f"Solde insuffisant: {balance:.2f} {currency} (minimum: {min_amount})")
            return False
        return True

    def place_market_order(self, side, amount, symbol=None):
        symbol = symbol or config.SYMBOL
        log.info(f"Ordre MARKET {side.upper()} — {amount:.6f} {symbol}")
        order = self._retry(self.client.create_market_order, symbol, side, amount)
        price = order.get("average") or order.get("price", "N/A")
        log.info(f"Ordre exécuté — ID: {order['id']}, Prix: {price}")
        return order

    def place_limit_order(self, side, amount, price, symbol=None):
        symbol = symbol or config.SYMBOL
        log.info(f"Ordre LIMIT {side.upper()} — {amount:.6f} {symbol} @ {price:.2f}")
        order = self._retry(self.client.create_limit_order, symbol, side, amount, price)
        log.info(f"Ordre placé — ID: {order['id']}")
        return order

    def place_order(self, side, amount=None, symbol=None):
        amount = amount or config.TRADE_AMOUNT
        return self.place_market_order(side, amount, symbol)
