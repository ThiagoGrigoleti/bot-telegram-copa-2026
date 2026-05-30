import os
import sys
import logging
from pathlib import Path
from datetime import datetime, time, timezone

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "model"))
sys.path.insert(0, str(Path(__file__).parent.parent / "bolao"))
sys.path.insert(0, str(Path(__file__).parent.parent / "betting"))
sys.path.insert(0, str(Path(__file__).parent.parent / "data"))

from predictor import predict_match
from scoring import process_match_results
from elo_engine import calculate_all_elo
from value_detector import detect_value

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

DB_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")


def _fetch_match_odds(home_name, away_name, match_date):
    try:
        from odds_fetcher import get_match_odds
    except Exception:
        return None

    try:
        return get_match_odds(home_name, away_name, match_date)
    except Exception:
        logger.exception("daily_prediction: odds fetch failed: %s vs %s", home_name, away_name)
        return None


def _fetch_external_prediction(home_name, away_name, match_date_str):
    try:
        from predictions_fetcher import get_external_prediction
    except Exception:
        return None

    try:
        return get_external_prediction(home_name, away_name, match_date_str)
    except Exception:
        logger.exception("daily_prediction: external prediction failed: %s vs %s", home_name, away_name)
        return None


def _normalize_winner(name):
    value = (name or "").strip().lower()
    if value in ("draw", "empate", "none", ""):
        return "draw"
    return value


def _build_consensus_block(winner, external, match_date):
    if not external:
        return ""

    try:
        ext_winner = external.get("winner")
        our_norm = _normalize_winner(winner)
        ext_norm = _normalize_winner(ext_winner)

        if our_norm == ext_norm:
            label = "Empate" if our_norm == "draw" else winner
            return f"✅ Consenso: Modelo próprio + API externa apontam {label}"

        try:
            now = datetime.now(timezone.utc)
            md = match_date if match_date.tzinfo else match_date.replace(tzinfo=timezone.utc)
            hours = max(1, int((md - now).total_seconds() // 3600))
        except Exception:
            hours = "algumas"

        our_display = "Empate" if our_norm == "draw" else winner
        ext_display = "Empate" if ext_norm == "draw" else ext_winner

        return (
            f"⚠️ Divergência: Nosso modelo → {our_display} | Fonte externa → {ext_display}\n"
            f"Quem acerta? Resultado em {hours} horas."
        )
    except Exception:
        logger.exception("daily_prediction: consensus block build failed")
        return ""


async def daily_prediction(context) -> None:
    if not CHANNEL_ID:
        logger.error("daily_prediction: TELEGRAM_CHANNEL_ID not set")
        return

    try:
        conn = psycopg2.connect(DB_URL)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT m.id, ht.name, at.name, m.match_date, m.competition
                FROM matches m
                JOIN teams ht ON ht.id = m.home_team_id
                JOIN teams at ON at.id = m.away_team_id
                WHERE m.match_date > NOW()
                  AND m.status = 'SCHEDULED'
                ORDER BY m.match_date ASC
                LIMIT 1
                """
            )
            row = cur.fetchone()
            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("daily_prediction: DB query failed")
        return

    if not row:
        logger.info("daily_prediction: no scheduled match found")
        return

    match_id, home_name, away_name, match_date, competition = row
    competition = competition or "FIFA World Cup"

    try:
        prediction = predict_match(home_name, away_name, match_date.strftime("%Y-%m-%d"), competition)
    except Exception:
        logger.exception("daily_prediction: predict_match failed: %s vs %s", home_name, away_name)
        return

    try:
        conn = psycopg2.connect(DB_URL)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO predictions (match_id, prob_home, prob_draw, prob_away)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (match_id) DO UPDATE SET
                    prob_home = EXCLUDED.prob_home,
                    prob_draw = EXCLUDED.prob_draw,
                    prob_away = EXCLUDED.prob_away
                """,
                (match_id, prediction["prob_home"], prediction["prob_draw"], prediction["prob_away"]),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("daily_prediction: failed to persist prediction for match %s", match_id)
        return

    try:
        odds = _fetch_match_odds(home_name, away_name, match_date)
        if odds:
            detect_value(
                match_id,
                home_name,
                away_name,
                match_date.strftime("%Y-%m-%d"),
                odds["odd_home"],
                odds["odd_draw"],
                odds["odd_away"],
            )
    except Exception:
        logger.exception("daily_prediction: value detection failed for match %s", match_id)

    date_str = match_date.strftime("%d/%m/%Y %H:%M")
    prob_home = prediction["prob_home"] * 100
    prob_draw = prediction["prob_draw"] * 100
    prob_away = prediction["prob_away"] * 100
    winner = prediction["predicted_winner"]

    external = _fetch_external_prediction(home_name, away_name, match_date.strftime("%Y-%m-%d"))
    consensus_block = _build_consensus_block(winner, external, match_date)

    lines = [
        f"🤖 Predição do dia — {home_name} vs {away_name}",
        f"📅 {date_str}",
        "",
        f"🏠 {home_name}: {prob_home:.1f}%",
        f"🤝 Empate: {prob_draw:.1f}%",
        f"✈️ {away_name}: {prob_away:.1f}%",
        "",
        f"Favorito: {winner}",
    ]

    if consensus_block:
        lines.append("")
        lines.append(consensus_block)

    lines.append("")
    lines.append("/palpite para registrar seu palpite")
    lines.append("/vip para apostar com dados reais")

    msg = "\n".join(lines)

    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=msg)
    except Exception:
        logger.exception("daily_prediction: send_message failed")


async def process_results(context) -> None:
    if not CHANNEL_ID:
        logger.error("process_results: TELEGRAM_CHANNEL_ID not set")
        return

    try:
        conn = psycopg2.connect(DB_URL)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT m.id, ht.name, at.name, m.home_score, m.away_score
                FROM matches m
                JOIN teams ht ON ht.id = m.home_team_id
                JOIN teams at ON at.id = m.away_team_id
                WHERE m.status = 'FINISHED'
                  AND m.results_processed = FALSE
                  AND m.home_score IS NOT NULL
                  AND m.away_score IS NOT NULL
                ORDER BY m.match_date ASC
                """
            )
            rows = cur.fetchall()
            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("process_results: DB query failed")
        return

    for match_id, home_name, away_name, home_score, away_score in rows:
        try:
            n = process_match_results(match_id)

            conn = psycopg2.connect(DB_URL)
            try:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE matches SET results_processed = TRUE WHERE id = %s",
                    (match_id,),
                )
                conn.commit()
                cur.close()
            finally:
                conn.close()

            msg = (
                f"✅ Resultado processado: {home_name} {home_score} x {away_score} {away_name}\n"
                f"Pontos distribuídos para {n} apostadores."
            )
            await context.bot.send_message(chat_id=CHANNEL_ID, text=msg)
        except Exception:
            logger.exception("process_results: failed to process match %s", match_id)


async def update_elo(context) -> None:
    try:
        calculate_all_elo()
    except Exception:
        logger.exception("update_elo: calculate_all_elo failed")


def setup_scheduler(application) -> None:
    jq = application.job_queue
    if jq is None:
        logger.error("setup_scheduler: job_queue is None, install python-telegram-bot[job-queue]")
        return

    jq.run_daily(daily_prediction, time=time(13, 0, tzinfo=timezone.utc))
    jq.run_repeating(process_results, interval=1800, first=60)
    jq.run_daily(update_elo, time=time(2, 0, tzinfo=timezone.utc))
    logger.info("Scheduler configured: daily_prediction, process_results, update_elo")
