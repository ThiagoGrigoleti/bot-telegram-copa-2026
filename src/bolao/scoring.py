import os
import psycopg2
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

DB_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")


def get_conn():
    return psycopg2.connect(DB_URL)


def calculate_points(
    guessed_home: int,
    guessed_away: int,
    actual_home: int,
    actual_away: int,
    model_certainty: float,
) -> int:
    if guessed_home == actual_home and guessed_away == actual_away:
        return 10

    def result(h, a):
        if h > a:
            return "W"
        if h < a:
            return "L"
        return "D"

    if result(guessed_home, guessed_away) == result(actual_home, actual_away):
        base = 3
        if model_certainty < 0.55:
            base *= 2
        return base

    return 0


def process_match_results(match_id: int) -> int:
    conn = get_conn()
    try:
        cur = conn.cursor()

        cur.execute(
            "SELECT home_score, away_score FROM matches WHERE id = %s",
            (match_id,),
        )
        match_row = cur.fetchone()
        if not match_row:
            raise ValueError(f"Match {match_id} not found")
        actual_home, actual_away = match_row

        cur.execute(
            "SELECT prob_home, prob_draw, prob_away FROM predictions WHERE match_id = %s ORDER BY created_at DESC LIMIT 1",
            (match_id,),
        )
        pred_row = cur.fetchone()
        if not pred_row:
            raise ValueError(f"Prediction for match {match_id} not found")
        certainty = max(pred_row)

        cur.execute(
            "SELECT id, user_id, guessed_home_score, guessed_away_score FROM guesses WHERE match_id = %s",
            (match_id,),
        )
        guesses = cur.fetchall()

        processed = 0
        for guess_id, user_id, g_home, g_away in guesses:
            pts = calculate_points(g_home, g_away, actual_home, actual_away, certainty)

            cur.execute(
                "UPDATE guesses SET points_earned = %s WHERE id = %s",
                (pts, guess_id),
            )
            cur.execute(
                "UPDATE users SET points = points + %s WHERE id = %s",
                (pts, user_id),
            )
            processed += 1

        conn.commit()
        cur.close()
        return processed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    assert calculate_points(2, 1, 2, 1, 0.60) == 10
    assert calculate_points(2, 0, 2, 1, 0.60) == 3
    assert calculate_points(2, 0, 2, 1, 0.50) == 6
    assert calculate_points(0, 2, 2, 1, 0.60) == 0
    print("All tests passed")
