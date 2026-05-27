import os
import logging
from pathlib import Path

import numpy as np
import joblib
from dotenv import load_dotenv
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

from feature_builder import build_training_dataset

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
MODELS_DIR = BASE_DIR / "models"
MODEL_PATH = MODELS_DIR / "xgb_model.pkl"

FEATURES = [
    "elo_home", "elo_away", "elo_diff", "form_home", "form_away",
    "h2h_home_winrate", "avg_goals_scored_home", "avg_goals_scored_away",
    "avg_goals_conceded_home", "avg_goals_conceded_away",
    "days_since_last_match_home", "days_since_last_match_away", "is_knockout",
]


def load_training_data() -> tuple[np.ndarray, np.ndarray]:
    df = build_training_dataset()
    X = df[FEATURES].to_numpy(dtype=np.float32)
    y = df["result"].to_numpy(dtype=np.int32)
    return X, y


def train_model() -> None:
    X, y = load_training_data()
    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.2, shuffle=True, random_state=42
    )

    model = XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        objective="multi:softprob",
        num_class=3,
        eval_metric="mlogloss",
        random_state=42,
    )

    try:
        model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
    except Exception:
        logger.exception("XGBClassifier.fit failed")
        raise

    y_proba = model.predict_proba(X_val)
    y_pred = np.argmax(y_proba, axis=1)

    acc = accuracy_score(y_val, y_pred)
    prob_home = y_proba[:, 2]
    y_home_binary = (y_val == 2).astype(int)
    brier = brier_score_loss(y_home_binary, prob_home)
    ll = log_loss(y_val, y_proba)

    print(f"Accuracy:    {acc:.4f}")
    print(f"Brier Score: {brier:.4f}  (home win prob vs binary home win)")
    print(f"Log Loss:    {ll:.4f}")
    logger.info("Metrics — acc=%.4f  brier=%.4f  log_loss=%.4f", acc, brier, ll)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    logger.info("Model saved → %s", MODEL_PATH)


def load_model() -> XGBClassifier:
    try:
        model = joblib.load(MODEL_PATH)
    except Exception:
        logger.exception("Failed to load model from %s", MODEL_PATH)
        raise
    return model


if __name__ == "__main__":
    train_model()
