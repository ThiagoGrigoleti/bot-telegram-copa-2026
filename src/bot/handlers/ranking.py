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


async def ranking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        conn = psycopg2.connect(DB_URL)
        try:
            cur = conn.cursor()

            cur.execute(
                """
                SELECT username, points
                FROM users
                ORDER BY points DESC, joined_at ASC
                LIMIT 10
                """
            )
            top_rows = cur.fetchall()

            cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) + 1 FROM users u2 WHERE u2.points > u.points),
                    u.points
                FROM users u
                WHERE u.telegram_id = %s
                """,
                (update.effective_user.id,),
            )
            user_row = cur.fetchone()

            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("DB error in /ranking")
        await update.message.reply_text("Erro ao buscar ranking. Tente novamente.")
        return

    if not top_rows:
        await update.message.reply_text("Nenhum jogador no ranking ainda.")
        return

    lines = ["\U0001f3c6 Ranking Copa 2026\n"]
    for i, (username, points) in enumerate(top_rows, start=1):
        display_name = username if username else "Jogador"
        lines.append(f"{i}. {display_name} — {points} pts")

    if user_row:
        user_rank, user_points = user_row
        lines.append(f"\nSua posição: #{user_rank} ({user_points} pts)")
    else:
        lines.append("\nVocê ainda não está no ranking. Use /start para se cadastrar.")

    await update.message.reply_text("\n".join(lines))
