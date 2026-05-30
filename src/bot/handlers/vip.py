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
AFFILIATE_LINK = os.getenv("AFFILIATE_LINK")


async def vip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id

    try:
        conn = psycopg2.connect(DB_URL)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT m.match_date, m.competition, m.stage,
                       ht.name AS home_name, at.name AS away_name
                FROM matches m
                JOIN teams ht ON m.home_team_id = ht.id
                JOIN teams at ON m.away_team_id = at.id
                WHERE m.match_date > NOW()
                  AND m.status = 'SCHEDULED'
                ORDER BY m.match_date ASC
                LIMIT 1
                """
            )
            row = cur.fetchone()

            cur.execute(
                "UPDATE users SET is_vip = TRUE WHERE telegram_id = %s",
                (telegram_id,),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("DB error in /vip for telegram_id=%s", telegram_id)
        await update.message.reply_text("Erro ao acessar liga VIP. Tente novamente.")
        return

    if not row:
        await update.message.reply_text("Nenhum jogo agendado encontrado.")
        return

    match_date, competition, stage, home_name, away_name = row
    competition = competition or "FIFA World Cup"

    try:
        prediction = predict_match(home_name, away_name, match_date.strftime("%Y-%m-%d"), competition)
    except Exception:
        logger.exception("predict_match failed: %s vs %s", home_name, away_name)
        await update.message.reply_text("Erro ao gerar predição VIP.")
        return

    date_str = match_date.strftime("%d/%m/%Y %H:%M")
    prob_home = prediction["prob_home"] * 100
    prob_draw = prediction["prob_draw"] * 100
    prob_away = prediction["prob_away"] * 100

    msg = (
        f"🎯 Liga VIP — Copa 2026\n\n"
        f"Próximo jogo: {home_name} vs {away_name}\n"
        f"📅 {date_str}\n\n"
        f"🤖 Modelo:\n"
        f"🏠 {home_name}: {prob_home:.1f}%\n"
        f"🤝 Empate: {prob_draw:.1f}%\n"
        f"✈️ {away_name}: {prob_away:.1f}%\n\n"
        f"Quer apostar com dados reais por trás?\n"
        f"👉 {AFFILIATE_LINK}\n\n"
        f"Entrando pela liga VIP você compete no ranking separado."
    )
    await update.message.reply_text(msg)
