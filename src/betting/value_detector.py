import logging
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "model"))

from predictor import predict_match

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

DB_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")

VALUE_EDGE_THRESHOLD = 0.05


def _persist_prediction(match_id: int, result: dict) -> None:
    if not DB_URL:
        logger.warning("value_detector: DATABASE_URL not set, skipping persist")
        return

    try:
        conn = psycopg2.connect(DB_URL)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO predictions (
                    match_id, prob_home, prob_draw, prob_away,
                    edge_home, edge_draw, edge_away, is_value_bet
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (match_id) DO UPDATE SET
                    edge_home = EXCLUDED.edge_home,
                    edge_draw = EXCLUDED.edge_draw,
                    edge_away = EXCLUDED.edge_away,
                    is_value_bet = EXCLUDED.is_value_bet
                """,
                (
                    match_id,
                    result["prob_home"],
                    result["prob_draw"],
                    result["prob_away"],
                    result["edge_home"],
                    result["edge_draw"],
                    result["edge_away"],
                    result["is_value_bet"],
                ),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("value_detector: failed to persist prediction for match %s", match_id)


def detect_value(
    match_id,
    home_team: str,
    away_team: str,
    match_date: str,
    odd_home: float,
    odd_draw: float,
    odd_away: float,
) -> dict:
    if min(odd_home, odd_draw, odd_away) <= 0:
        raise ValueError("odds must be positive")

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
    value_edges = {k: v for k, v in edges.items() if v > VALUE_EDGE_THRESHOLD}
    is_value_bet = bool(value_edges)
    value_outcome = max(value_edges, key=value_edges.get) if value_edges else None

    result = {
        "match_id": match_id,
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

    if match_id is not None:
        _persist_prediction(match_id, result)

    return result


if __name__ == "__main__":
    result = detect_value(None, "Brazil", "Argentina", "2026-06-15", 3.20, 3.10, 2.15)
    print(result)
    print(f"Value bet: {result['is_value_bet']} → {result['value_outcome']}")
