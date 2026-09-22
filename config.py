import os
from dotenv import load_dotenv

load_dotenv()

# --- Binance API ---
API_KEY = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")
TESTNET = os.getenv("TESTNET", "true").lower() == "true"

# --- Trading ---
SYMBOL = os.getenv("SYMBOL", "BTC/USDT")
TIMEFRAME = os.getenv("TIMEFRAME", "1h")
TRADE_AMOUNT = float(os.getenv("TRADE_AMOUNT", "0.001"))
LOOP_INTERVAL = int(os.getenv("LOOP_INTERVAL", "60"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# --- Stratégie SMA ---
SMA_SHORT_PERIOD = int(os.getenv("SMA_SHORT_PERIOD", "10"))
SMA_LONG_PERIOD = int(os.getenv("SMA_LONG_PERIOD", "30"))

# --- Stratégie RSI ---
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
RSI_OVERSOLD = int(os.getenv("RSI_OVERSOLD", "30"))
RSI_OVERBOUGHT = int(os.getenv("RSI_OVERBOUGHT", "70"))

# --- Stratégie MACD ---
MACD_FAST = int(os.getenv("MACD_FAST", "12"))
MACD_SLOW = int(os.getenv("MACD_SLOW", "26"))
MACD_SIGNAL = int(os.getenv("MACD_SIGNAL", "9"))

# --- Bollinger Bands ---
BB_PERIOD = int(os.getenv("BB_PERIOD", "20"))
BB_STD_DEV = float(os.getenv("BB_STD_DEV", "2.0"))

# --- Score de confiance minimum pour trader (1-5) ---
MIN_CONFIDENCE = int(os.getenv("MIN_CONFIDENCE", "3"))

# --- Risk Management ---
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "2.0"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "4.0"))
MAX_DRAWDOWN_PCT = float(os.getenv("MAX_DRAWDOWN_PCT", "10.0"))
POSITION_SIZE_PCT = float(os.getenv("POSITION_SIZE_PCT", "5.0"))
TRADE_COOLDOWN = int(os.getenv("TRADE_COOLDOWN", "300"))
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "10"))

# --- Notifications Discord ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
DISCORD_ENABLED = os.getenv("DISCORD_ENABLED", "false").lower() == "true"

# --- Données ---
DATA_DIR = os.getenv("DATA_DIR", "data")
LOG_DIR = os.getenv("LOG_DIR", "logs")
