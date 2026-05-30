import os
import logging
import psycopg2
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ContextTypes

load_dotenv(Path(__file__).resolve().parents[3] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

DB_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")

_WELCOME = (
    "⚽ Copa 2026 Bot\n\n"
    "Predicões com modelo ML próprio.\n\n"
    "Comandos:\n"
    "/jogo — predição do próximo jogo\n"
    "/palpite — faça seu palpite no bolão\n"
    "/ranking — classificação do bolão\n"
    "/favorito — alertas do seu time\n"
    "/simular — simule a classificação dos grupos\n"
    "/stats — performance do modelo\n"
    "/vip — acesso à liga VIP"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    telegram_id = user.id
    username = user.username or ""
    is_vip = bool(context.args)

    try:
        conn = psycopg2.connect(DB_URL)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO users (telegram_id, username, is_vip)
                VALUES (%s, %s, %s)
                ON CONFLICT (telegram_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    is_vip = GREATEST(users.is_vip, EXCLUDED.is_vip)
                """,
                (telegram_id, username, is_vip),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("DB upsert failed for telegram_id=%s", telegram_id)

    await update.message.reply_text(_WELCOME)
