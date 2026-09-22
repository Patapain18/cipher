import ta
import config
from logger import get_logger

log = get_logger("strategy")


class SmaRsiStrategy:
    def __init__(self):
        self.sma_short = config.SMA_SHORT_PERIOD
        self.sma_long = config.SMA_LONG_PERIOD
        self.rsi_period = config.RSI_PERIOD
        self.rsi_oversold = config.RSI_OVERSOLD
        self.rsi_overbought = config.RSI_OVERBOUGHT
        self.macd_fast = config.MACD_FAST
        self.macd_slow = config.MACD_SLOW
        self.macd_signal = config.MACD_SIGNAL
        self.bb_period = config.BB_PERIOD
        self.bb_std = config.BB_STD_DEV
        self.min_confidence = config.MIN_CONFIDENCE

    def evaluate(self, df):
        close = df["close"]

        # --- SMA ---
        sma_short = ta.trend.sma_indicator(close, window=self.sma_short)
        sma_long = ta.trend.sma_indicator(close, window=self.sma_long)

        # --- EMA ---
        ema_short = ta.trend.ema_indicator(close, window=self.sma_short)
        ema_long = ta.trend.ema_indicator(close, window=self.sma_long)

        # --- RSI ---
        rsi = ta.momentum.rsi(close, window=self.rsi_period)

        # --- MACD ---
        macd_line = ta.trend.macd(close, window_slow=self.macd_slow, window_fast=self.macd_fast)
        macd_signal_line = ta.trend.macd_signal(
            close, window_slow=self.macd_slow, window_fast=self.macd_fast, window_sign=self.macd_signal
        )
        macd_hist = ta.trend.macd_diff(
            close, window_slow=self.macd_slow, window_fast=self.macd_fast, window_sign=self.macd_signal
        )

        # --- Bollinger Bands ---
        bb_upper = ta.volatility.bollinger_hband(close, window=self.bb_period, window_dev=self.bb_std)
        bb_lower = ta.volatility.bollinger_lband(close, window=self.bb_period, window_dev=self.bb_std)
        bb_mid = ta.volatility.bollinger_mavg(close, window=self.bb_period)

        # --- Précision de l'arrondi ---
        # Un arrondi fixe à 2 décimales convient au bitcoin, mais détruit
        # l'information sur une paire à faible prix : à 0,089 USDT, le
        # DOGE voit ses SMA et ses bandes de Bollinger s'écraser toutes
        # sur « 0,09 ». Les comparaisons plus bas (price <= bb_lower,
        # sma_short > sma_long) ne départagent alors plus rien et le
        # score de confiance devient du bruit — précisément sur la paire
        # retenue par le grid bot.
        # On adapte donc le nombre de décimales à l'ordre de grandeur.
        price = float(close.iloc[-1])
        decimals = 5 if price < 1 else 3 if price < 100 else 2
        r = lambda v: round(float(v), decimals)

        # Dernières valeurs
        latest = {
            "price": price,
            "decimals": decimals,
            "sma_short": r(sma_short.iloc[-1]),
            "sma_long": r(sma_long.iloc[-1]),
            "ema_short": r(ema_short.iloc[-1]),
            "ema_long": r(ema_long.iloc[-1]),
            "rsi": round(float(rsi.iloc[-1]), 2),      # toujours dans 0-100
            "macd": r(macd_line.iloc[-1]),
            "macd_signal": r(macd_signal_line.iloc[-1]),
            "macd_hist": r(macd_hist.iloc[-1]),
            "bb_upper": r(bb_upper.iloc[-1]),
            "bb_lower": r(bb_lower.iloc[-1]),
            "bb_mid": r(bb_mid.iloc[-1]),
        }

        # --- Calcul du score de confiance (0-5) ---
        buy_score = 0
        sell_score = 0

        # 1. SMA crossover
        if latest["sma_short"] > latest["sma_long"]:
            buy_score += 1
        else:
            sell_score += 1

        # 2. EMA crossover
        if latest["ema_short"] > latest["ema_long"]:
            buy_score += 1
        else:
            sell_score += 1

        # 3. RSI
        if latest["rsi"] < self.rsi_oversold:
            buy_score += 1
        elif latest["rsi"] > self.rsi_overbought:
            sell_score += 1

        # 4. MACD
        if latest["macd_hist"] > 0:
            buy_score += 1
        elif latest["macd_hist"] < 0:
            sell_score += 1

        # 5. Bollinger Bands
        if latest["price"] <= latest["bb_lower"]:
            buy_score += 1
        elif latest["price"] >= latest["bb_upper"]:
            sell_score += 1

        latest["buy_score"] = buy_score
        latest["sell_score"] = sell_score

        # Décision basée sur le score de confiance
        if buy_score >= self.min_confidence:
            signal = "BUY"
            confidence = buy_score
        elif sell_score >= self.min_confidence:
            signal = "SELL"
            confidence = sell_score
        else:
            signal = "HOLD"
            confidence = max(buy_score, sell_score)

        latest["confidence"] = confidence
        latest["signal"] = signal

        log.debug(
            f"Scores — BUY: {buy_score}/5, SELL: {sell_score}/5 | "
            f"Signal: {signal} (confiance: {confidence}, min requis: {self.min_confidence})"
        )

        return signal, latest
