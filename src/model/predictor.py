import logging

import numpy as np
from pathlib import Path
from dotenv import load_dotenv

from feature_builder import build_features_for_match
from trainer import FEATURES, load_model

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def predict_match(
    home_team: str,
    away_team: str,
    match_date: str,
    competition: str = "FIFA World Cup",
) -> dict:
    try:
        model = load_model()
    except Exception:
        logger.exception("load_model failed")
        raise

    try:
        features = build_features_for_match(home_team, away_team, match_date, competition)
    except Exception:
        logger.exception("build_features_for_match failed: %s vs %s", home_team, away_team)
        raise

    if features is None:
        raise ValueError(f"Teams not found in DB: {home_team!r} / {away_team!r}")

    row = [float(features[f]) if features[f] is not None else 0.0 for f in FEATURES]
    X = np.array([row], dtype=np.float32)

    proba = model.predict_proba(X)[0]

    prob_away = round(float(proba[0]), 4)
    prob_draw = round(float(proba[1]), 4)
    prob_home = round(float(proba[2]), 4)

    max_idx = int(np.argmax(proba))
    if max_idx == 2:
        predicted_winner = home_team
    elif max_idx == 1:
        predicted_winner = "Draw"
    else:
        predicted_winner = away_team

    return {
        "home_team": home_team,
        "away_team": away_team,
        "prob_home": prob_home,
        "prob_draw": prob_draw,
        "prob_away": prob_away,
        "predicted_winner": predicted_winner,
    }


if __name__ == "__main__":
    result = predict_match("Brazil", "Argentina", "2026-06-15", "FIFA World Cup")
    print(result)
