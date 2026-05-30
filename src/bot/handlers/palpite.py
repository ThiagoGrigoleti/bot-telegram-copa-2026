import os
import logging
from pathlib import Path
import psycopg2
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ContextTypes

load_dotenv(Path(__file__).resolve().parents[3] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

DB_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")


async def palpite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        conn = psycopg2.connect(DB_URL)
        try:
            cur = conn.cursor()

            sql = """
                SELECT m.id, ht.name, at.name, m.match_date, m.stage
                FROM matches m
                JOIN teams ht ON ht.id = m.home_team_id
                JOIN teams at ON at.id = m.away_team_id
                WHERE m.match_date > NOW()
                  AND m.status = 'SCHEDULED'
                ORDER BY m.match_date ASC
                LIMIT 1
                """
            logger.info("Executing palpite query: %s", sql.strip())
            cur.execute(sql)
            match_row = cur.fetchone()
            logger.info("Query result: %s", match_row)

            cur.execute("SELECT NOW(), current_setting('TIMEZONE')")
            db_now, db_tz = cur.fetchone()
            logger.info("DB NOW()=%s, TIMEZONE=%s", db_now, db_tz)

            if not match_row:
                cur.close()
                await update.message.reply_text("Nenhum jogo disponível para palpite.")
                return

            match_id, home_name, away_name, match_date, stage = match_row

            cur.execute(
                """
                SELECT guessed_home_score, guessed_away_score
                FROM guesses
                WHERE user_id = (SELECT id FROM users WHERE telegram_id = %s)
                  AND match_id = %s
                """,
                (update.effective_user.id, match_id),
            )
            existing = cur.fetchone()

            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("DB error in /palpite")
        await update.message.reply_text("Erro ao buscar jogo para palpite. Tente novamente.")
        return

    if existing:
        g_home, g_away = existing
        await update.message.reply_text(
            f"Você já enviou seu palpite para esse jogo: {g_home} x {g_away}"
        )
        return

    context.user_data["match_id"] = match_id
    context.user_data["home_name"] = home_name
    context.user_data["away_name"] = away_name

    date_str = match_date.strftime("%d/%m/%Y %H:%M")

    msg = (
        f"⚽ {home_name} vs {away_name}\n"
        f"📅 {date_str}\n\n"
        f"Envie seu palpite no formato:\n"
        f"placar [gols_home] [gols_away]\n\n"
        f"Exemplo: placar 2 1"
    )
    await update.message.reply_text(msg)


async def palpite_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    match_id = context.user_data.get("match_id")
    if not match_id:
        await update.message.reply_text("Use /palpite primeiro para escolher o jogo.")
        return

    parts = update.message.text.strip().split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await update.message.reply_text("Formato inválido. Use: placar 2 1")
        return

    home_score = int(parts[1])
    away_score = int(parts[2])

    try:
        conn = psycopg2.connect(DB_URL)
        try:
            cur = conn.cursor()

            cur.execute(
                """
                INSERT INTO users (telegram_id, username)
                VALUES (%s, %s)
                ON CONFLICT (telegram_id) DO UPDATE SET username = EXCLUDED.username
                RETURNING id
                """,
                (update.effective_user.id, update.effective_user.username or ""),
            )
            user_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO guesses (user_id, match_id, guessed_home_score, guessed_away_score)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, match_id) DO NOTHING
                """,
                (user_id, match_id, home_score, away_score),
            )

            if cur.rowcount == 0:
                conn.rollback()
                cur.close()
                await update.message.reply_text("Você já enviou seu palpite para esse jogo.")
                return

            conn.commit()
            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("DB error registering guess")
        await update.message.reply_text("Erro ao registrar palpite. Tente novamente.")
        return

    home_name = context.user_data.pop("home_name", "?")
    away_name = context.user_data.pop("away_name", "?")
    context.user_data.pop("match_id", None)

    await update.message.reply_text(
        f"✅ Palpite registrado: {home_name} {home_score} x {away_score} {away_name}"
    )
