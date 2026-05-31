import os
import logging
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

API_KEY = os.getenv("BALLDONTLIE_API_KEY")
BASE_URL = "https://api.balldontlie.io/fifa_wc/v1"
KEY_PLAYER_COUNT = 5


def _headers():
    return {"Authorization": f"Bearer {API_KEY}"}


def _log_rate_limit(response):
    remaining = response.headers.get("X-RateLimit-Remaining")
    if remaining is not None:
        logger.info("X-RateLimit-Remaining: %s", remaining)


def _team_matches(event, home_q, away_q):
    home = (event.get("home_team") or {})
    away = (event.get("away_team") or {})
    home_name = (home.get("name") or event.get("home_team_name") or "").lower()
    away_name = (away.get("name") or event.get("away_team_name") or "").lower()
    home_ok = home_q in home_name or home_name in home_q
    away_ok = away_q in away_name or away_name in away_q
    return home_ok and away_ok


def _find_match(match_date, home_team, away_team):
    try:
        response = requests.get(
            f"{BASE_URL}/matches",
            headers=_headers(),
            params={"date": match_date},
            timeout=30,
        )
        _log_rate_limit(response)
        response.raise_for_status()
        events = response.json().get("data", [])
    except Exception:
        logger.exception("lineup_fetcher: matches request failed for %s", match_date)
        return None

    home_q = home_team.strip().lower()
    away_q = away_team.strip().lower()

    for event in events:
        try:
            if _team_matches(event, home_q, away_q):
                return event
        except Exception:
            continue

    logger.info("lineup_fetcher: match not found for %s vs %s on %s", home_team, away_team, match_date)
    return None


def _fetch_lineups(match_id):
    try:
        response = requests.get(
            f"{BASE_URL}/lineups",
            headers=_headers(),
            params={"match_id": match_id},
            timeout=30,
        )
        _log_rate_limit(response)
        response.raise_for_status()
        return response.json().get("data", [])
    except Exception:
        logger.exception("lineup_fetcher: lineups request failed for match %s", match_id)
        return []


def _fetch_team_players(team_id):
    if not team_id:
        return []
    try:
        response = requests.get(
            f"{BASE_URL}/players",
            headers=_headers(),
            params={"team_id": team_id},
            timeout=30,
        )
        _log_rate_limit(response)
        response.raise_for_status()
        return response.json().get("data", [])
    except Exception:
        logger.exception("lineup_fetcher: players request failed for team %s", team_id)
        return []


def _player_name(player):
    name = player.get("name") or player.get("full_name")
    if name:
        return name
    parts = [player.get("first_name"), player.get("last_name")]
    return " ".join(p for p in parts if p).strip()


def _player_rating(player):
    for field in ("rating", "overall", "overall_rating", "ovr"):
        value = player.get(field)
        if isinstance(value, (int, float)):
            return value
    return None


def _split_lineup(side):
    players = side.get("players") or side.get("lineup") or []
    starters = []
    for player in players:
        is_starter = player.get("starter")
        if is_starter is None:
            is_starter = (player.get("type") or "").lower() in ("starter", "starting")
        name = _player_name(player)
        if not name:
            continue
        if is_starter:
            starters.append(name)
    if not starters:
        starters = [n for n in (_player_name(p) for p in players[:11]) if n]
    formation = side.get("formation") or side.get("tactics") or ""
    return starters, formation


def _missing_key_players(team_id, starters):
    if not team_id or not starters:
        return []
    players = _fetch_team_players(team_id)
    rated = []
    for player in players:
        name = _player_name(player)
        rating = _player_rating(player)
        if name and rating is not None:
            rated.append((rating, name))
    if not rated:
        return []
    rated.sort(reverse=True)
    starters_lower = {s.lower() for s in starters}
    missing = []
    for _, name in rated[:KEY_PLAYER_COUNT]:
        if name.lower() not in starters_lower:
            missing.append(name)
    return missing


def _resolve_side(lineups, event, key):
    team = (event.get(key) or {})
    team_id = team.get("id")
    for side in lineups:
        side_team = (side.get("team") or {})
        if team_id and side_team.get("id") == team_id:
            return side, team_id
    return None, team_id


def get_match_lineup(home_team: str, away_team: str, match_date: str) -> dict | None:
    if not API_KEY:
        logger.error("BALLDONTLIE_API_KEY not set")
        return None

    event = _find_match(match_date, home_team, away_team)
    if not event:
        return None

    match_id = event.get("id")
    if not match_id:
        logger.info("lineup_fetcher: no match_id for %s vs %s", home_team, away_team)
        return None

    lineups = _fetch_lineups(match_id)
    if not lineups:
        return {
            "home_formation": "",
            "away_formation": "",
            "home_starters": [],
            "away_starters": [],
            "home_missing_key_players": [],
            "away_missing_key_players": [],
            "lineup_available": False,
        }

    try:
        home_side, home_id = _resolve_side(lineups, event, "home_team")
        away_side, away_id = _resolve_side(lineups, event, "away_team")

        if home_side is None and len(lineups) >= 1:
            home_side = lineups[0]
        if away_side is None and len(lineups) >= 2:
            away_side = lineups[1]

        if not home_side or not away_side:
            return {
                "home_formation": "",
                "away_formation": "",
                "home_starters": [],
                "away_starters": [],
                "home_missing_key_players": [],
                "away_missing_key_players": [],
                "lineup_available": False,
            }

        home_starters, home_formation = _split_lineup(home_side)
        away_starters, away_formation = _split_lineup(away_side)

        if not home_starters or not away_starters:
            return {
                "home_formation": "",
                "away_formation": "",
                "home_starters": [],
                "away_starters": [],
                "home_missing_key_players": [],
                "away_missing_key_players": [],
                "lineup_available": False,
            }

        home_missing = _missing_key_players(home_id, home_starters)
        away_missing = _missing_key_players(away_id, away_starters)

        return {
            "home_formation": home_formation,
            "away_formation": away_formation,
            "home_starters": home_starters,
            "away_starters": away_starters,
            "home_missing_key_players": home_missing,
            "away_missing_key_players": away_missing,
            "lineup_available": True,
        }
    except Exception:
        logger.exception("lineup_fetcher: failed parsing lineup for %s vs %s", home_team, away_team)
        return None


def format_lineup_context(lineup: dict) -> str:
    if not lineup or not lineup.get("lineup_available"):
        return ""

    try:
        home_formation = lineup.get("home_formation") or "?"
        away_formation = lineup.get("away_formation") or "?"

        lines = [
            "🔢 Formações confirmadas:",
            f"🏠 {home_formation} | ✈️ {away_formation}",
        ]

        missing = list(lineup.get("home_missing_key_players", [])) + list(lineup.get("away_missing_key_players", []))
        if missing:
            lines.append("")
            lines.append(f"⚠️ Desfalques: {', '.join(missing)}")

        return "\n".join(lines)
    except Exception:
        logger.exception("lineup_fetcher: format_lineup_context failed")
        return ""


if __name__ == "__main__":
    result = get_match_lineup("Brazil", "Mexico", "2026-06-22")
    print(result)
    print(format_lineup_context(result) if result else "No lineup")
