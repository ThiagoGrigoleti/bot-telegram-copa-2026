import os
import logging
import unicodedata
import psycopg2
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

DB_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")


def _get_conn():
    return psycopg2.connect(DB_URL)


def _normalize(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def _k_factor(competition: str) -> int:
    c = _normalize(competition) if competition else ""
    if "world cup" in c and "qualification" not in c:
        return 60
    if "uefa euro" in c:
        return 50
    if "copa america" in c:
        return 45
    if "qualification" in c:
        return 40
    return 20


def _expected(elo_team: float, elo_opp: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((elo_opp - elo_team) / 400.0))


def calculate_all_elo():
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT m.competition,
                   t_home.id, t_away.id,
                   m.home_score, m.away_score
            FROM matches m
            JOIN teams t_home ON t_home.id = m.home_team_id
            JOIN teams t_away ON t_away.id = m.away_team_id
            WHERE m.status = 'FINISHED'
              AND m.home_score IS NOT NULL
              AND m.away_score IS NOT NULL
            ORDER BY m.match_date ASC
        """)
        rows = cur.fetchall()

        elo: dict[int, float] = {}

        for competition, home_id, away_id, home_score, away_score in rows:
            if home_id not in elo:
                elo[home_id] = 1500.0
            if away_id not in elo:
                elo[away_id] = 1500.0

            k = _k_factor(competition or "")
            e_home = _expected(elo[home_id], elo[away_id])
            e_away = _expected(elo[away_id], elo[home_id])

            if home_score > away_score:
                s_home, s_away = 1.0, 0.0
            elif home_score < away_score:
                s_home, s_away = 0.0, 1.0
            else:
                s_home, s_away = 0.5, 0.5

            elo[home_id] += k * (s_home - e_home)
            elo[away_id] += k * (s_away - e_away)

        for team_id, rating in elo.items():
            cur.execute(
                "UPDATE teams SET elo_rating = %s, updated_at = NOW() WHERE id = %s",
                (rating, team_id),
            )

        conn.commit()
        logger.info("calculate_all_elo: %d matches processed, %d teams updated", len(rows), len(elo))
        cur.close()
    except Exception:
        conn.rollback()
        logger.exception("calculate_all_elo failed")
        raise
    finally:
        conn.close()


def get_elo_ratings() -> dict[str, float]:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT name, elo_rating FROM teams")
        result = {name: rating for name, rating in cur.fetchall()}
        cur.close()
        return result
    except Exception:
        logger.exception("get_elo_ratings failed")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    calculate_all_elo()
    ratings = get_elo_ratings()
    top10 = sorted(ratings.items(), key=lambda x: x[1], reverse=True)[:10]
    print("Top 10 ELO:")
    for name, elo in top10:
        print(f"  {name}: {elo:.1f}")
