import os
import csv
import time
import logging
import requests
import psycopg2
from psycopg2.extras import execute_values
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
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


_WC2026_GROUP_STAGE = [
    ("WC26-A-1", "Mexico",       "South Africa",  "2026-06-11 19:00:00"),
    ("WC26-A-2", "South Korea",  "Czechia",        "2026-06-12 02:00:00"),
    ("WC26-A-3", "Czechia",      "South Africa",   "2026-06-18 16:00:00"),
    ("WC26-A-4", "Mexico",       "South Korea",    "2026-06-19 01:00:00"),
    ("WC26-A-5", "Czechia",      "Mexico",         "2026-06-25 01:00:00"),
    ("WC26-A-6", "South Africa", "South Korea",    "2026-06-25 01:00:00"),

    ("WC26-B-1", "Canada",               "Bosnia and Herzegovina", "2026-06-12 19:00:00"),
    ("WC26-B-2", "Qatar",                "Switzerland",            "2026-06-13 19:00:00"),
    ("WC26-B-3", "Switzerland",          "Bosnia and Herzegovina", "2026-06-18 19:00:00"),
    ("WC26-B-4", "Canada",               "Qatar",                  "2026-06-18 22:00:00"),
    ("WC26-B-5", "Switzerland",          "Canada",                 "2026-06-24 19:00:00"),
    ("WC26-B-6", "Bosnia and Herzegovina", "Qatar",                "2026-06-24 19:00:00"),

    ("WC26-C-1", "Brazil",   "Morocco",  "2026-06-13 22:00:00"),
    ("WC26-C-2", "Haiti",    "Scotland", "2026-06-14 01:00:00"),
    ("WC26-C-3", "Scotland", "Morocco",  "2026-06-19 22:00:00"),
    ("WC26-C-4", "Brazil",   "Haiti",    "2026-06-20 01:00:00"),
    ("WC26-C-5", "Scotland", "Brazil",   "2026-06-24 22:00:00"),
    ("WC26-C-6", "Morocco",  "Haiti",    "2026-06-24 22:00:00"),

    ("WC26-D-1", "United States", "Paraguay",  "2026-06-13 01:00:00"),
    ("WC26-D-2", "Australia",     "Turkiye",   "2026-06-14 04:00:00"),
    ("WC26-D-3", "United States", "Australia", "2026-06-19 19:00:00"),
    ("WC26-D-4", "Turkiye",       "Paraguay",  "2026-06-20 04:00:00"),
    ("WC26-D-5", "Turkiye",       "United States", "2026-06-26 02:00:00"),
    ("WC26-D-6", "Paraguay",      "Australia", "2026-06-26 02:00:00"),

    ("WC26-E-1", "Germany",      "Curacao",      "2026-06-14 17:00:00"),
    ("WC26-E-2", "Ivory Coast",  "Ecuador",      "2026-06-14 23:00:00"),
    ("WC26-E-3", "Germany",      "Ivory Coast",  "2026-06-20 20:00:00"),
    ("WC26-E-4", "Ecuador",      "Curacao",      "2026-06-21 00:00:00"),
    ("WC26-E-5", "Ecuador",      "Germany",      "2026-06-25 20:00:00"),
    ("WC26-E-6", "Curacao",      "Ivory Coast",  "2026-06-25 20:00:00"),

    ("WC26-F-1", "Netherlands", "Japan",       "2026-06-14 20:00:00"),
    ("WC26-F-2", "Sweden",      "Tunisia",     "2026-06-15 02:00:00"),
    ("WC26-F-3", "Netherlands", "Sweden",      "2026-06-20 17:00:00"),
    ("WC26-F-4", "Tunisia",     "Japan",       "2026-06-21 04:00:00"),
    ("WC26-F-5", "Japan",       "Sweden",      "2026-06-25 23:00:00"),
    ("WC26-F-6", "Tunisia",     "Netherlands", "2026-06-25 23:00:00"),

    ("WC26-G-1", "Iran",        "New Zealand", "2026-06-16 01:00:00"),
    ("WC26-G-2", "Belgium",     "Egypt",       "2026-06-15 19:00:00"),
    ("WC26-G-3", "Belgium",     "Iran",        "2026-06-21 19:00:00"),
    ("WC26-G-4", "New Zealand", "Egypt",       "2026-06-22 01:00:00"),
    ("WC26-G-5", "Egypt",       "Iran",        "2026-06-27 03:00:00"),
    ("WC26-G-6", "New Zealand", "Belgium",     "2026-06-27 03:00:00"),

    ("WC26-H-1", "Spain",        "Cape Verde",   "2026-06-15 16:00:00"),
    ("WC26-H-2", "Saudi Arabia", "Uruguay",      "2026-06-15 22:00:00"),
    ("WC26-H-3", "Spain",        "Saudi Arabia", "2026-06-21 16:00:00"),
    ("WC26-H-4", "Uruguay",      "Cape Verde",   "2026-06-21 22:00:00"),
    ("WC26-H-5", "Cape Verde",   "Saudi Arabia", "2026-06-27 00:00:00"),
    ("WC26-H-6", "Uruguay",      "Spain",        "2026-06-27 00:00:00"),

    ("WC26-I-1", "France",  "Senegal", "2026-06-16 19:00:00"),
    ("WC26-I-2", "Iraq",    "Norway",  "2026-06-16 22:00:00"),
    ("WC26-I-3", "France",  "Iraq",    "2026-06-22 21:00:00"),
    ("WC26-I-4", "Norway",  "Senegal", "2026-06-23 00:00:00"),
    ("WC26-I-5", "Norway",  "France",  "2026-06-26 19:00:00"),
    ("WC26-I-6", "Senegal", "Iraq",    "2026-06-26 19:00:00"),

    ("WC26-J-1", "Argentina", "Algeria",   "2026-06-17 01:00:00"),
    ("WC26-J-2", "Austria",   "Jordan",    "2026-06-17 04:00:00"),
    ("WC26-J-3", "Argentina", "Austria",   "2026-06-22 17:00:00"),
    ("WC26-J-4", "Jordan",    "Algeria",   "2026-06-23 03:00:00"),
    ("WC26-J-5", "Algeria",   "Austria",   "2026-06-28 02:00:00"),
    ("WC26-J-6", "Jordan",    "Argentina", "2026-06-28 02:00:00"),

    ("WC26-K-1", "Portugal",                    "Democratic Republic of Congo", "2026-06-17 17:00:00"),
    ("WC26-K-2", "Uzbekistan",                  "Colombia",                     "2026-06-18 02:00:00"),
    ("WC26-K-3", "Portugal",                    "Uzbekistan",                   "2026-06-23 17:00:00"),
    ("WC26-K-4", "Colombia",                    "Democratic Republic of Congo", "2026-06-24 02:00:00"),
    ("WC26-K-5", "Colombia",                    "Portugal",                     "2026-06-27 23:30:00"),
    ("WC26-K-6", "Democratic Republic of Congo", "Uzbekistan",                  "2026-06-27 23:30:00"),

    ("WC26-L-1", "England", "Croatia", "2026-06-17 20:00:00"),
    ("WC26-L-2", "Ghana",   "Panama",  "2026-06-17 23:00:00"),
    ("WC26-L-3", "England", "Ghana",   "2026-06-23 20:00:00"),
    ("WC26-L-4", "Panama",  "Croatia", "2026-06-23 23:00:00"),
    ("WC26-L-5", "Panama",  "England", "2026-06-27 21:00:00"),
    ("WC26-L-6", "Croatia", "Ghana",   "2026-06-27 21:00:00"),
]


def seed_wc2026_matches() -> int:
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        team_map = _build_team_name_map(cur)

        inserted = 0
        for ext_id, home_name, away_name, match_date in _WC2026_GROUP_STAGE:
            home_id = team_map.get(home_name)
            away_id = team_map.get(away_name)

            if home_id is None:
                logger.warning("seed_wc2026: team not found in DB: %r", home_name)
            if away_id is None:
                logger.warning("seed_wc2026: team not found in DB: %r", away_name)

            cur.execute(
                """
                INSERT INTO matches
                    (external_id, home_team_id, away_team_id, match_date,
                     stage, status, competition)
                VALUES (%s, %s, %s, %s, 'GROUP', 'SCHEDULED', 'FIFA World Cup 2026')
                ON CONFLICT (match_date, home_team_id, away_team_id) DO NOTHING
                """,
                (ext_id, home_id, away_id, match_date),
            )
            if cur.rowcount > 0:
                inserted += 1

        conn.commit()
        cur.close()
        logger.info("seed_wc2026_matches: %d rows inserted", inserted)
        return inserted
    except Exception:
        logger.exception("seed_wc2026_matches failed")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    init_schema()
    print(f"Kaggle import: {import_kaggle_csv()}")
    print(f"Teams Copa 2026: {fetch_teams()}")
    print(f"Matches Copa 2026: {fetch_matches()}")