import os
import logging
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

API_KEY = os.getenv("ODDS_API_KEY")
BASE_URL = "https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup/odds/"


def get_match_odds(home_team: str, away_team: str) -> dict | None:
    if not API_KEY:
        logger.error("ODDS_API_KEY not set")
        return None

    params = {
        "apiKey": API_KEY,
        "regions": "eu",
        "markets": "h2h",
        "oddsFormat": "decimal",
    }

    try:
        response = requests.get(BASE_URL, params=params, timeout=30)
        logger.info("X-Requests-Remaining: %s", response.headers.get("X-Requests-Remaining"))
        response.raise_for_status()
        events = response.json()
    except Exception:
        logger.exception("get_match_odds: API request failed")
        return None

    try:
        home_q = home_team.strip().lower()
        away_q = away_team.strip().lower()

        match = None
        for event in events:
            ev_home = (event.get("home_team") or "").lower()
            ev_away = (event.get("away_team") or "").lower()
            home_ok = home_q in ev_home or ev_home in home_q
            away_ok = away_q in ev_away or ev_away in away_q
            if home_ok and away_ok:
                match = event
                break

        if match is None:
            logger.info("get_match_odds: match not found for %s vs %s", home_team, away_team)
            return None

        home_name = match["home_team"]
        away_name = match["away_team"]

        home_odds = []
        draw_odds = []
        away_odds = []

        for bookmaker in match.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    name = outcome.get("name")
                    price = outcome.get("price")
                    if price is None:
                        continue
                    if name == home_name:
                        home_odds.append(price)
                    elif name == away_name:
                        away_odds.append(price)
                    elif name == "Draw":
                        draw_odds.append(price)

        if not home_odds or not draw_odds or not away_odds:
            logger.warning("get_match_odds: incomplete odds for %s vs %s", home_team, away_team)
            return None

        return {
            "odd_home": round(sum(home_odds) / len(home_odds), 3),
            "odd_draw": round(sum(draw_odds) / len(draw_odds), 3),
            "odd_away": round(sum(away_odds) / len(away_odds), 3),
        }
    except Exception:
        logger.exception("get_match_odds: failed parsing odds for %s vs %s", home_team, away_team)
        return None


if __name__ == "__main__":
    result = get_match_odds("Brazil", "Mexico")
    print(result)
