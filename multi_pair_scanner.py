"""
Scanner Multi-Paires — Scanne les top cryptos et trade les meilleures.

Analyse plusieurs paires simultanément et sélectionne celles
avec les signaux les plus forts pour concentrer le capital.
"""

import ccxt
import pandas as pd
import ta
from logger import get_logger

log = get_logger("scanner")

TOP_PAIRS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "DOT/USDT", "LINK/USDT",
    "MATIC/USDT", "UNI/USDT", "ATOM/USDT", "LTC/USDT", "NEAR/USDT",
]


class MultiPairScanner:
    def __init__(self, pairs=None, timeframe="1h", min_score=3):
        self.pairs = pairs or TOP_PAIRS
        self.timeframe = timeframe
        self.min_score = min_score
        self.exchange = ccxt.binance({"enableRateLimit": True})

    def _analyze_pair(self, symbol, df):
        """Analyse une paire et retourne un score."""
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        score = 0
        details = {}

        # 1. Tendance (EMA 10 vs 30)
        ema_short = ta.trend.ema_indicator(close, window=10)
        ema_long = ta.trend.ema_indicator(close, window=30)
        trend_up = ema_short.iloc[-1] > ema_long.iloc[-1]
        if trend_up:
            score += 1
        details["trend"] = "UP" if trend_up else "DOWN"

        # 2. RSI en zone favorable
        rsi = ta.momentum.rsi(close, window=14)
        rsi_val = rsi.iloc[-1]
        if rsi_val < 35:
            score += 2  # Forte survente = opportunité
        elif rsi_val < 45:
            score += 1
        details["rsi"] = round(rsi_val, 1)

        # 3. MACD momentum
        macd_hist = ta.trend.macd_diff(close)
        macd_positive = macd_hist.iloc[-1] > 0
        macd_crossing = macd_hist.iloc[-1] > 0 > macd_hist.iloc[-2] if len(macd_hist) > 1 else False
        if macd_crossing:
            score += 2  # Cross haussier = signal fort
        elif macd_positive:
            score += 1
        details["macd"] = "CROSS UP" if macd_crossing else ("POS" if macd_positive else "NEG")

        # 4. Volume au-dessus de la moyenne
        vol_sma = ta.trend.sma_indicator(volume, window=20)
        vol_ratio = volume.iloc[-1] / vol_sma.iloc[-1] if vol_sma.iloc[-1] > 0 else 0
        if vol_ratio > 1.5:
            score += 1
        details["vol_ratio"] = round(vol_ratio, 2)

        # 5. Bollinger — prix proche de la bande basse
        bb_low = ta.volatility.bollinger_lband(close, window=20)
        bb_high = ta.volatility.bollinger_hband(close, window=20)
        bb_range = bb_high.iloc[-1] - bb_low.iloc[-1]
        if bb_range > 0:
            bb_pos = (close.iloc[-1] - bb_low.iloc[-1]) / bb_range
        else:
            bb_pos = 0.5
        if bb_pos < 0.2:
            score += 2
        elif bb_pos < 0.4:
            score += 1
        details["bb_position"] = round(bb_pos, 2)

        # 6. Momentum 24h
        returns_24h = close.pct_change(24).iloc[-1] * 100
        details["return_24h"] = round(returns_24h, 2)

        # 7. ATR (volatilité) — plus c'est volatile, plus de potentiel
        atr = ta.volatility.average_true_range(high, low, close, window=14)
        atr_pct = (atr.iloc[-1] / close.iloc[-1]) * 100
        if atr_pct > 2:
            score += 1
        details["atr_pct"] = round(atr_pct, 2)

        return {
            "symbol": symbol,
            "price": close.iloc[-1],
            "score": score,
            "details": details,
        }

    def scan(self, limit=100):
        """Scanne toutes les paires et retourne un classement."""
        results = []

        for symbol in self.pairs:
            try:
                data = self.exchange.fetch_ohlcv(symbol, self.timeframe, limit=limit)
                df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

                result = self._analyze_pair(symbol, df)
                results.append(result)
            except Exception as e:
                log.warning(f"Erreur scan {symbol}: {e}")

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def get_best_pairs(self, limit=100):
        """Retourne les paires avec un score >= min_score."""
        all_results = self.scan(limit)
        return [r for r in all_results if r["score"] >= self.min_score]

    def scan_from_dataframes(self, dataframes):
        """Scanne à partir de DataFrames pré-chargés (pour le backtest)."""
        results = []
        for symbol, df in dataframes.items():
            try:
                result = self._analyze_pair(symbol, df)
                results.append(result)
            except Exception as e:
                log.warning(f"Erreur analyse {symbol}: {e}")
        results.sort(key=lambda x: x["score"], reverse=True)
        return results
