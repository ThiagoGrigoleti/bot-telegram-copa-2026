import os
import csv
import time
import logging
import requests
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

API_KEY = os.getenv("FOOTBALL_DATA_API_KEY")
DB_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")

HEADERS = {"X-Auth-Token": API_KEY}
BASE_URL = "http://api.football-data.org/v4"

COMPETITION_WC = "WC"

MIN_REQUEST_INTERVAL = 6.5
MAX_RETRIES = 5

_last_request_time = 0.0


def _api_get(url):
    global _last_request_time

    for attempt in range(1, MAX_RETRIES + 1):
        elapsed = time.time() - _last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)

        _last_request_time = time.time()
        response = requests.get(url, headers=HEADERS, timeout=30)

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            logger.warning(
                "Rate limit 429 (attempt %d/%d). Retry-After: %ds",
                attempt, MAX_RETRIES, retry_after,
            )
            time.sleep(retry_after)
            continue

        response.raise_for_status()
        return response.json()

    raise RuntimeError(f"API request failed after {MAX_RETRIES} retries: {url}")


def get_db_connection():
    return psycopg2.connect(DB_URL)


def _build_team_map(cur):
    cur.execute("SELECT fifa_code, id FROM teams")
    return {row[0]: row[1] for row in cur.fetchall()}


def _build_team_name_map(cur):
    cur.execute("SELECT name, id FROM teams")
    return {row[0]: row[1] for row in cur.fetchall()}


def init_schema():
    logger.info("Ensuring schema is up to date...")
    conn = get_db_connection()
    try:
        conn.autocommit = True
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS teams (
                id SERIAL PRIMARY KEY,
                fifa_code VARCHAR(10) UNIQUE NOT NULL,
                name VARCHAR(100) NOT NULL,
                elo_rating FLOAT NOT NULL DEFAULT 1500.0,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id SERIAL PRIMARY KEY,
                external_id VARCHAR(50) UNIQUE,
                home_team_id INT REFERENCES teams(id),
                away_team_id INT REFERENCES teams(id),
                match_date TIMESTAMP NOT NULL,
                stage VARCHAR(50),
                home_score INT,
                away_score INT,
                status VARCHAR(20) DEFAULT 'SCHEDULED',
                competition VARCHAR(100)
            )
        """)

        cur.execute("ALTER TABLE matches ADD COLUMN IF NOT EXISTS competition VARCHAR(100)")

        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_matches_date_teams
            ON matches (match_date, home_team_id, away_team_id)
        """)

        logger.info("Schema OK")
        cur.close()
    finally:
        conn.close()


def fetch_teams():
    logger.info("Fetching World Cup 2026 teams...")
    data = _api_get(f"{BASE_URL}/competitions/{COMPETITION_WC}/teams")
    teams_data = data.get("teams", [])

    if len(teams_data) != 48:
        logger.warning("fetch_teams: expected 48 teams, got %d", len(teams_data))

    conn = get_db_connection()
    try:
        cur = conn.cursor()

        upsert_query = """
            INSERT INTO teams (fifa_code, name)
            VALUES %s
            ON CONFLICT (fifa_code) DO UPDATE SET
                name = EXCLUDED.name,
                updated_at = NOW()
        """

        values = []
        for t in teams_data:
            tla = t.get("tla") or t["name"][:3].upper()
            values.append((tla, t["name"]))

        if values:
            execute_values(cur, upsert_query, values)
            conn.commit()

        logger.info("fetch_teams: %d upserted", len(values))
        cur.close()
        return len(values)
    finally:
        conn.close()


def fetch_matches():
    logger.info("Fetching World Cup 2026 matches...")
    data = _api_get(f"{BASE_URL}/competitions/{COMPETITION_WC}/matches")
    matches_data = data.get("matches", [])

    if len(matches_data) != 104:
        logger.warning("fetch_matches: expected 104 matches, got %d", len(matches_data))

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        team_map = _build_team_map(cur)

        upsert_query = """
            INSERT INTO matches (external_id, home_team_id, away_team_id, match_date, stage, home_score, away_score, status, competition)
            VALUES %s
            ON CONFLICT (external_id) DO UPDATE SET
                home_score = EXCLUDED.home_score,
                away_score = EXCLUDED.away_score,
                status = EXCLUDED.status
        """

        values = []
        skipped = 0
        for m in matches_data:
            home_tla = m.get("homeTeam", {}).get("tla")
            away_tla = m.get("awayTeam", {}).get("tla")

            if not home_tla or not away_tla:
                skipped += 1
                continue

            home_id = team_map.get(home_tla)
            away_id = team_map.get(away_tla)

            if not home_id or not away_id:
                skipped += 1
                continue

            score_data = m.get("score", {}).get("fullTime", {})

            values.append((
                str(m["id"]),
                home_id,
                away_id,
                m["utcDate"],
                m["stage"],
                score_data.get("home"),
                score_data.get("away"),
                m["status"],
                COMPETITION_WC,
            ))

        if values:
            execute_values(cur, upsert_query, values)
            conn.commit()

        if skipped:
            logger.info("fetch_matches: skipped %d matches (teams TBD)", skipped)
        logger.info("fetch_matches: %d upserted", len(values))
        cur.close()
        return len(values)
    finally:
        conn.close()


def import_kaggle_csv(filepath="src/data/archive/results.csv"):
    logger.info("Importing Kaggle CSV from %s...", filepath)

    with open(filepath, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [r for r in reader if r["date"] >= "2000-01-01"]

    logger.info("CSV loaded: %d rows from 2000 onwards", len(rows))

    conn = get_db_connection()
    try:
        cur = conn.cursor()

        team_names = set()
        for r in rows:
            team_names.add(r["home_team"])
            team_names.add(r["away_team"])

        cur.execute("SELECT name FROM teams")
        existing_names = {row[0] for row in cur.fetchall()}
        cur.execute("SELECT fifa_code FROM teams")
        used_codes = {row[0] for row in cur.fetchall()}

        new_teams = sorted(team_names - existing_names)
        for name in new_teams:
            base_code = name[:3].upper()
            code = base_code
            suffix = 2
            while code in used_codes:
                code = base_code[:2] + str(suffix)
                suffix += 1
            used_codes.add(code)
            cur.execute(
                "INSERT INTO teams (fifa_code, name) VALUES (%s, %s) ON CONFLICT (fifa_code) DO NOTHING",
                (code, name),
            )
        conn.commit()
        logger.info("Teams upserted: %d new of %d total", len(new_teams), len(team_names))

        team_name_map = _build_team_name_map(cur)

        match_upsert = """
            INSERT INTO matches
                (home_team_id, away_team_id, match_date, home_score, away_score, status, competition)
            VALUES %s
            ON CONFLICT (match_date, home_team_id, away_team_id) DO UPDATE SET
                home_score = EXCLUDED.home_score,
                away_score = EXCLUDED.away_score,
                status = EXCLUDED.status,
                competition = EXCLUDED.competition
        """

        values = []
        skipped = 0
        for r in rows:
            home_id = team_name_map.get(r["home_team"])
            away_id = team_name_map.get(r["away_team"])

            if not home_id or not away_id:
                skipped += 1
                continue

            home_score = r["home_score"]
            away_score = r["away_score"]

            if home_score == "NA" or away_score == "NA":
                skipped += 1
                continue

            values.append((
                home_id,
                away_id,
                r["date"],
                int(home_score),
                int(away_score),
                "FINISHED",
                r["tournament"],
            ))

        BATCH_SIZE = 5000
        total_inserted = 0
        for i in range(0, len(values), BATCH_SIZE):
            batch = values[i:i + BATCH_SIZE]
            execute_values(cur, match_upsert, batch)
            conn.commit()
            total_inserted += len(batch)
            logger.info("Batch %d: %d matches upserted", i // BATCH_SIZE + 1, len(batch))

        if skipped:
            logger.info("Skipped %d rows (missing team or NA scores)", skipped)

        logger.info("Kaggle import complete: %d matches inserted", total_inserted)
        cur.close()
        return total_inserted
    finally:
        conn.close()


def update_live_results():
    logger.info("Updating live results for World Cup 2026...")
    data = _api_get(f"{BASE_URL}/competitions/{COMPETITION_WC}/matches?status=IN_PLAY,PAUSED,FINISHED")
    matches_data = data.get("matches", [])

    if not matches_data:
        logger.info("No live/finished matches to update")
        return

    conn = get_db_connection()
    try:
        cur = conn.cursor()

        update_query = """
            UPDATE matches
            SET home_score = %s,
                away_score = %s,
                status = %s
            WHERE external_id = %s
        """

        updated = 0
        for m in matches_data:
            score_data = m.get("score", {}).get("fullTime", {})
            cur.execute(update_query, (
                score_data.get("home"),
                score_data.get("away"),
                m["status"],
                str(m["id"]),
            ))
            if cur.rowcount > 0:
                updated += 1

        conn.commit()
        logger.info("update_live_results: %d matches updated", updated)
        cur.close()
    finally:
        conn.close()


if __name__ == "__main__":
    init_schema()
    print(f"Kaggle import: {import_kaggle_csv()}")
    print(f"Teams Copa 2026: {fetch_teams()}")
    print(f"Matches Copa 2026: {fetch_matches()}")