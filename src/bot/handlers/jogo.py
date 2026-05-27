import os
import sys
import logging
from pathlib import Path
import psycopg2
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ContextTypes

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "model"))
from predictor import predict_match

load_dotenv(Path(__file__).resolve().parents[3] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

DB_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")


async def jogo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        conn = psycopg2.connect(DB_URL)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT ht.name, at.name, m.match_date, m.competition, m.stage
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
        logger.exception("DB query failed for next scheduled match")
        await update.message.reply_text("Erro ao buscar próximo jogo. Tente novamente.")
        return

    if not row:
        await update.message.reply_text("Nenhum jogo agendado encontrado.")
        return

    home_name, away_name, match_date, competition, stage = row
    competition = competition or "FIFA World Cup"

    try:
        prediction = predict_match(home_name, away_name, match_date.strftime("%Y-%m-%d"), competition)
    except Exception:
        logger.exception("predict_match failed: %s vs %s", home_name, away_name)
        await update.message.reply_text("Erro ao gerar predição.")
        return

    date_str = match_date.strftime("%d/%m/%Y %H:%M")
    prob_home = prediction["prob_home"] * 100
    prob_draw = prediction["prob_draw"] * 100
    prob_away = prediction["prob_away"] * 100
    winner = prediction["predicted_winner"]

    msg = (
        f"Predição: {home_name} x {away_name}\n"
        f"Data: {date_str}\n\n"
        f"Casa   {home_name}: {prob_home:.1f}%\n"
        f"Empate: {prob_draw:.1f}%\n"
        f"Fora   {away_name}: {prob_away:.1f}%\n\n"
        f"Favorito: {winner}"
    )
    await update.message.reply_text(msg)
