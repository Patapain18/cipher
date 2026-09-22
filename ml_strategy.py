import numpy as np
import pandas as pd
import ta
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import classification_report, accuracy_score

from logger import get_logger

log = get_logger("ml_strategy")


class MLStrategy:
    """Stratégie basée sur le Machine Learning.

    Entraîne un modèle sur les données historiques pour prédire
    si le prix va monter ou descendre dans les prochaines bougies.
    """

    def __init__(self, model_type="random_forest", lookahead=5, min_profit_pct=1.0):
        """
        Args:
            model_type: "random_forest" ou "gradient_boosting"
            lookahead: nombre de bougies à regarder dans le futur pour le label
            min_profit_pct: % de profit minimum pour considérer un signal BUY
        """
        self.model_type = model_type
        self.lookahead = lookahead
        self.min_profit_pct = min_profit_pct
        self.model = None
        self.scaler = StandardScaler()
        self.feature_names = []
        self.is_trained = False
        self.train_accuracy = 0
        self.test_accuracy = 0

    def _compute_features(self, df):
        """Calcule toutes les features techniques pour le ML."""
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        features = pd.DataFrame(index=df.index)

        # --- Moyennes mobiles ---
        for period in [5, 10, 20, 50]:
            sma = ta.trend.sma_indicator(close, window=period)
            ema = ta.trend.ema_indicator(close, window=period)
            features[f"sma_{period}_ratio"] = close / sma - 1
            features[f"ema_{period}_ratio"] = close / ema - 1

        # --- RSI ---
        for period in [7, 14, 21]:
            features[f"rsi_{period}"] = ta.momentum.rsi(close, window=period)

        # --- MACD ---
        features["macd"] = ta.trend.macd(close)
        features["macd_signal"] = ta.trend.macd_signal(close)
        features["macd_hist"] = ta.trend.macd_diff(close)

        # --- Bollinger Bands ---
        bb_high = ta.volatility.bollinger_hband(close)
        bb_low = ta.volatility.bollinger_lband(close)
        features["bb_position"] = (close - bb_low) / (bb_high - bb_low)
        features["bb_width"] = (bb_high - bb_low) / close

        # --- ATR (volatilité) ---
        features["atr_14"] = ta.volatility.average_true_range(high, low, close, window=14)
        features["atr_ratio"] = features["atr_14"] / close

        # --- Stochastic ---
        features["stoch_k"] = ta.momentum.stoch(high, low, close)
        features["stoch_d"] = ta.momentum.stoch_signal(high, low, close)

        # --- Volume ---
        features["volume_sma_ratio"] = volume / ta.trend.sma_indicator(volume, window=20)
        features["obv_change"] = ta.volume.on_balance_volume(close, volume).pct_change(5)

        # --- Momentum ---
        for period in [3, 5, 10, 20]:
            features[f"return_{period}"] = close.pct_change(period)

        # --- Volatilité historique ---
        features["volatility_10"] = close.pct_change().rolling(10).std()
        features["volatility_20"] = close.pct_change().rolling(20).std()

        # --- Pattern recognition (price action) ---
        features["high_low_ratio"] = (high - low) / close
        features["close_open_ratio"] = (close - df["open"]) / close

        # --- Tendance ---
        features["adx"] = ta.trend.adx(high, low, close, window=14)
        features["cci"] = ta.trend.cci(high, low, close, window=20)

        return features

    def _create_labels(self, df):
        """Crée les labels: 1 = prix monte de min_profit_pct% dans les prochaines bougies, 0 sinon."""
        close = df["close"]
        future_max = close.shift(-self.lookahead).rolling(self.lookahead).max().shift(-self.lookahead + self.lookahead)

        # Recalcul plus simple: max des N prochaines bougies
        future_returns = pd.Series(index=df.index, dtype=float)
        for i in range(len(df) - self.lookahead):
            future_max_price = close.iloc[i + 1: i + 1 + self.lookahead].max()
            future_returns.iloc[i] = (future_max_price - close.iloc[i]) / close.iloc[i] * 100

        labels = (future_returns >= self.min_profit_pct).astype(int)
        return labels

    def train(self, df):
        """Entraîne le modèle sur les données historiques."""
        log.info(f"Calcul des features ({len(df)} bougies)...")
        features = self._compute_features(df)
        labels = self._create_labels(df)

        # Supprimer les NaN
        valid_mask = features.notna().all(axis=1) & labels.notna()
        features = features[valid_mask]
        labels = labels[valid_mask]

        self.feature_names = features.columns.tolist()
        log.info(f"Features: {len(self.feature_names)} | Échantillons valides: {len(features)}")
        log.info(f"Distribution labels — BUY: {labels.sum()} ({labels.mean()*100:.1f}%) | HOLD: {len(labels)-labels.sum()}")

        # Split temporel (pas random — c'est des séries temporelles !)
        split_idx = int(len(features) * 0.8)
        X_train = features.iloc[:split_idx]
        y_train = labels.iloc[:split_idx]
        X_test = features.iloc[split_idx:]
        y_test = labels.iloc[split_idx:]

        # Normalisation
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)

        # Entraînement
        if self.model_type == "gradient_boosting":
            self.model = GradientBoostingClassifier(
                n_estimators=200,
                max_depth=5,
                learning_rate=0.05,
                min_samples_split=20,
                random_state=42,
            )
        else:
            self.model = RandomForestClassifier(
                n_estimators=200,
                max_depth=10,
                min_samples_split=20,
                random_state=42,
                n_jobs=-1,
            )

        log.info(f"Entraînement du modèle {self.model_type}...")
        self.model.fit(X_train_scaled, y_train)

        # Évaluation
        train_pred = self.model.predict(X_train_scaled)
        test_pred = self.model.predict(X_test_scaled)

        self.train_accuracy = accuracy_score(y_train, train_pred)
        self.test_accuracy = accuracy_score(y_test, test_pred)

        log.info(f"Accuracy train: {self.train_accuracy*100:.1f}%")
        log.info(f"Accuracy test:  {self.test_accuracy*100:.1f}%")

        # Feature importance
        importances = sorted(
            zip(self.feature_names, self.model.feature_importances_),
            key=lambda x: x[1],
            reverse=True,
        )
        log.info("Top 10 features les plus importantes:")
        for name, imp in importances[:10]:
            log.info(f"  {name}: {imp:.4f}")

        self.is_trained = True
        return {
            "train_accuracy": self.train_accuracy,
            "test_accuracy": self.test_accuracy,
            "train_samples": len(X_train),
            "test_samples": len(X_test),
            "buy_signals_train": int(y_train.sum()),
            "buy_signals_test": int(y_test.sum()),
            "top_features": importances[:10],
        }

    def predict(self, df):
        """Prédit le signal pour les données actuelles."""
        if not self.is_trained:
            raise ValueError("Le modèle n'est pas encore entraîné. Appelez train() d'abord.")

        features = self._compute_features(df)
        latest = features.iloc[[-1]]

        if latest.isna().any(axis=1).iloc[0]:
            return "HOLD", {"confidence": 0, "probability": 0.5}

        latest_scaled = self.scaler.transform(latest)
        prediction = self.model.predict(latest_scaled)[0]
        probability = self.model.predict_proba(latest_scaled)[0]

        prob_buy = probability[1] if len(probability) > 1 else 0
        confidence = abs(prob_buy - 0.5) * 2  # 0 à 1

        if prediction == 1 and prob_buy > 0.6:
            signal = "BUY"
        elif prob_buy < 0.35:
            signal = "SELL"
        else:
            signal = "HOLD"

        return signal, {
            "probability": round(prob_buy, 4),
            "confidence": round(confidence, 4),
            "signal": signal,
            "price": df["close"].iloc[-1],
        }
