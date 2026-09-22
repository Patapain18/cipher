"""
Stratégie Scalping — Trades rapides sur petits mouvements.

Timeframe: 1-5 minutes
Objectif: capturer des micro-mouvements de 0.2-0.5%
Indicateurs: EMA rapide, RSI court, Volume spike, Momentum
"""

import ta
import pandas as pd
from logger import get_logger

log = get_logger("scalping")


class ScalpingStrategy:
    def __init__(self, ema_fast=5, ema_mid=13, ema_slow=21,
                 rsi_period=7, rsi_low=25, rsi_high=75,
                 volume_spike=1.5, min_atr_ratio=0.001):
        self.ema_fast = ema_fast
        self.ema_mid = ema_mid
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.rsi_low = rsi_low
        self.rsi_high = rsi_high
        self.volume_spike = volume_spike
        self.min_atr_ratio = min_atr_ratio

    def evaluate(self, df):
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        # EMAs rapides
        ema_fast = ta.trend.ema_indicator(close, window=self.ema_fast)
        ema_mid = ta.trend.ema_indicator(close, window=self.ema_mid)
        ema_slow = ta.trend.ema_indicator(close, window=self.ema_slow)

        # RSI court
        rsi = ta.momentum.rsi(close, window=self.rsi_period)

        # Volume relatif (spike detection)
        vol_sma = ta.trend.sma_indicator(volume, window=20)
        vol_ratio = volume / vol_sma

        # ATR pour filtrer les mouvements trop faibles
        atr = ta.volatility.average_true_range(high, low, close, window=10)
        atr_ratio = atr / close

        # Momentum court terme
        momentum_3 = close.pct_change(3) * 100
        momentum_1 = close.pct_change(1) * 100

        # Stochastic rapide
        stoch_k = ta.momentum.stoch(high, low, close, window=5, smooth_window=3)

        latest = {
            "price": close.iloc[-1],
            "ema_fast": round(ema_fast.iloc[-1], 2),
            "ema_mid": round(ema_mid.iloc[-1], 2),
            "ema_slow": round(ema_slow.iloc[-1], 2),
            "rsi": round(rsi.iloc[-1], 2),
            "vol_ratio": round(vol_ratio.iloc[-1], 2),
            "atr_ratio": round(atr_ratio.iloc[-1], 6),
            "momentum_3": round(momentum_3.iloc[-1], 3),
            "momentum_1": round(momentum_1.iloc[-1], 3),
            "stoch_k": round(stoch_k.iloc[-1], 2),
        }

        # Filtres de base
        has_volume = vol_ratio.iloc[-1] >= self.volume_spike
        has_volatility = atr_ratio.iloc[-1] >= self.min_atr_ratio

        buy_score = 0
        sell_score = 0

        # 1. EMA alignment (fast > mid > slow = tendance haussière)
        if latest["ema_fast"] > latest["ema_mid"] > latest["ema_slow"]:
            buy_score += 2
        elif latest["ema_fast"] < latest["ema_mid"] < latest["ema_slow"]:
            sell_score += 2

        # 2. RSI
        if latest["rsi"] < self.rsi_low:
            buy_score += 1
        elif latest["rsi"] > self.rsi_high:
            sell_score += 1

        # 3. Momentum positif
        if latest["momentum_3"] > 0.1:
            buy_score += 1
        elif latest["momentum_3"] < -0.1:
            sell_score += 1

        # 4. Stochastic
        if latest["stoch_k"] < 20:
            buy_score += 1
        elif latest["stoch_k"] > 80:
            sell_score += 1

        # 5. Prix au-dessus de l'EMA rapide
        if latest["price"] > latest["ema_fast"]:
            buy_score += 1
        else:
            sell_score += 1

        latest["buy_score"] = buy_score
        latest["sell_score"] = sell_score

        # Décision — on trade seulement si volume et volatilité OK
        if buy_score >= 4 and has_volume and has_volatility:
            signal = "BUY"
        elif sell_score >= 4 and has_volatility:
            signal = "SELL"
        else:
            signal = "HOLD"

        latest["signal"] = signal
        latest["has_volume"] = has_volume
        latest["has_volatility"] = has_volatility

        return signal, latest
