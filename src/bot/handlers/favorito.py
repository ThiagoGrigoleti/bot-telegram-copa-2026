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

ALIASES = {
    "brasil": "brazil",
    "alemanha": "germany",
    "franca": "france",
    "frança": "france",
    "espanha": "spain",
    "holanda": "netherlands",
    "belgica": "belgium",
    "bélgica": "belgium",
    "suica": "switzerland",
    "suíça": "switzerland",
    "dinamarca": "denmark",
    "croacia": "croatia",
    "croácia": "croatia",
    "servia": "serbia",
    "sérvia": "serbia",
    "polonia": "poland",
    "polônia": "poland",
    "marrocos": "morocco",
    "senegal": "senegal",
    "coreia": "korea republic",
    "japao": "japan",
    "japão": "japan",
    "mexico": "mexico",
    "méxico": "mexico",
    "argentina": "argentina",
    "uruguai": "uruguay",
    "colombia": "colombia",
    "colômbia": "colombia",
    "equador": "ecuador",
    "portugal": "portugal",
    "italia": "italy",
    "itália": "italy",
    "inglaterra": "england",
    "eua": "united states",
    "estados unidos": "united states",
}


async def favorito(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "Use: /favorito [nome do time]\n\n"
            "Exemplo: /favorito Brazil"
        )
        return

    arg = " ".join(context.args).strip()
    search_term = ALIASES.get(arg.lower().strip(), arg.strip())

    try:
        conn = psycopg2.connect(DB_URL)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, name FROM teams WHERE LOWER(name) LIKE LOWER(%s) LIMIT 1",
                (f"%{search_term}%",),
            )
            team = cur.fetchone()

            if not team:
                cur.close()
                await update.message.reply_text(
                    f"Time não encontrado: '{arg}'.\n"
                    "Tente em inglês: /favorito Brazil, /favorito France, /favorito Argentina"
                )
                return

            team_id, team_name = team

            cur.execute(
                """
                INSERT INTO users (telegram_id, username, favorite_team_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (telegram_id) DO UPDATE SET
                    favorite_team_id = EXCLUDED.favorite_team_id,
                    username = EXCLUDED.username
                """,
                (update.effective_user.id, update.effective_user.username or "", team_id),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("DB error in /favorito")
        await update.message.reply_text("Erro ao registrar time favorito. Tente novamente.")
        return

    await update.message.reply_text(
        f"✅ Agora você vai receber alertas antes de cada jogo de {team_name}\n\n"
        "Você será notificado 2h antes com a predição do modelo.\n"
        "Use /favorito para trocar de time a qualquer momento."
    )
