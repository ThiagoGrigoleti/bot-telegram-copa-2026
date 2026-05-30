import os
import time
import logging
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

API_KEY = os.getenv("API_FOOTBALL_KEY")
BASE_URL = "https://v3.football.api-sports.io"
LEAGUE_ID = 1
SEASON = 2026
REQUEST_DELAY = 1


def _log_remaining(response) -> None:
    logger.info("x-ratelimit-requests-remaining: %s", response.headers.get("x-ratelimit-requests-remaining"))


def _parse_percent(value) -> float | None:
    try:
        return round(float(str(value).strip().rstrip("%")) / 100, 4)
    except Exception:
        return None


def get_external_prediction(home_team: str, away_team: str, match_date: str) -> dict | None:
    if not API_KEY:
        logger.error("API_FOOTBALL_KEY not set")
        return None

    headers = {"x-apisports-key": API_KEY}

    try:
        resp = requests.get(
            f"{BASE_URL}/teams",
            headers=headers,
            params={"name": home_team, "league": LEAGUE_ID, "season": SEASON},
            timeout=30,
        )
        _log_remaining(resp)
        resp.raise_for_status()
        teams = resp.json().get("response", [])
        if not teams:
            logger.info("get_external_prediction: team not found: %s", home_team)
            return None
        team_id = teams[0]["team"]["id"]
    except Exception:
        logger.exception("get_external_prediction: team lookup failed: %s", home_team)
        return None

    time.sleep(REQUEST_DELAY)

    try:
        resp = requests.get(
            f"{BASE_URL}/fixtures",
            headers=headers,
            params={"league": LEAGUE_ID, "season": SEASON, "team": team_id, "date": match_date},
            timeout=30,
        )
        _log_remaining(resp)
        resp.raise_for_status()
        fixtures = resp.json().get("response", [])
    except Exception:
        logger.exception("get_external_prediction: fixtures lookup failed: %s", home_team)
        return None

    away_q = away_team.strip().lower()
    fixture_id = None
    for fixture in fixtures:
        teams_block = fixture.get("teams", {})
        away_name = (teams_block.get("away", {}).get("name") or "").lower()
        home_name = (teams_block.get("home", {}).get("name") or "").lower()
        if away_q in away_name or away_name in away_q or away_q in home_name:
            fixture_id = fixture.get("fixture", {}).get("id")
            break

    if not fixture_id:
        logger.info("get_external_prediction: fixture not found: %s vs %s on %s", home_team, away_team, match_date)
        return None

    time.sleep(REQUEST_DELAY)

    try:
        resp = requests.get(
            f"{BASE_URL}/predictions",
            headers=headers,
            params={"fixture": fixture_id},
            timeout=30,
        )
        _log_remaining(resp)
        resp.raise_for_status()
        data = resp.json().get("response", [])
    except Exception:
        logger.exception("get_external_prediction: predictions lookup failed for fixture %s", fixture_id)
        return None

    try:
        if not data:
            logger.info("get_external_prediction: no predictions for fixture %s", fixture_id)
            return None

        predictions = data[0].get("predictions", {})
        percent = predictions.get("percent", {})

        prob_home = _parse_percent(percent.get("home"))
        prob_draw = _parse_percent(percent.get("draw"))
        prob_away = _parse_percent(percent.get("away"))

        if prob_home is None or prob_draw is None or prob_away is None:
            logger.warning("get_external_prediction: incomplete percent for fixture %s", fixture_id)
            return None

        return {
            "winner": (predictions.get("winner") or {}).get("name"),
            "prob_home": prob_home,
            "prob_draw": prob_draw,
            "prob_away": prob_away,
            "advice": predictions.get("advice"),
        }
    except Exception:
        logger.exception("get_external_prediction: failed parsing prediction for fixture %s", fixture_id)
        return None


if __name__ == "__main__":
    result = get_external_prediction("Brazil", "Mexico", "2026-06-22")
    print(result)
