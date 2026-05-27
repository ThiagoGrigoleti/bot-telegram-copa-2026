import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "model"))

from predictor import predict_match

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def detect_value(
    home_team: str,
    away_team: str,
    match_date: str,
    odd_home: float,
    odd_draw: float,
    odd_away: float,
) -> dict:
    try:
        prediction = predict_match(home_team, away_team, match_date)
    except Exception:
        logger.exception("predict_match failed: %s vs %s", home_team, away_team)
        raise

    prob_home = prediction["prob_home"]
    prob_draw = prediction["prob_draw"]
    prob_away = prediction["prob_away"]

    implied_home = 1.0 / odd_home
    implied_draw = 1.0 / odd_draw
    implied_away = 1.0 / odd_away

    edge_home = round(prob_home - implied_home, 4)
    edge_draw = round(prob_draw - implied_draw, 4)
    edge_away = round(prob_away - implied_away, 4)

    edges = {"home": edge_home, "draw": edge_draw, "away": edge_away}
    value_edges = {k: v for k, v in edges.items() if v > 0.05}
    is_value_bet = bool(value_edges)
    value_outcome = max(value_edges, key=value_edges.get) if value_edges else None

    return {
        "home_team": home_team,
        "away_team": away_team,
        "prob_home": prob_home,
        "prob_draw": prob_draw,
        "prob_away": prob_away,
        "odd_home": odd_home,
        "odd_draw": odd_draw,
        "odd_away": odd_away,
        "edge_home": edge_home,
        "edge_draw": edge_draw,
        "edge_away": edge_away,
        "is_value_bet": is_value_bet,
        "value_outcome": value_outcome,
    }


if __name__ == "__main__":
    result = detect_value("Brazil", "Argentina", "2026-06-15", 3.20, 3.10, 2.15)
    print(result)
    print(f"Value bet: {result['is_value_bet']} → {result['value_outcome']}")
