import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from handlers.start import start
from handlers.jogo import jogo
from handlers.ranking import ranking
from handlers.palpite import palpite, palpite_text
from handlers.vip import vip
from handlers.favorito import favorito
from handlers.stats import stats
from handlers.simular import build_simular_conversation
from scheduler import setup_scheduler
from db.init_db import init_db

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    init_db()
    token = os.getenv("TELEGRAM_API_TOKEN")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("jogo", jogo))
    app.add_handler(CommandHandler("ranking", ranking))
    app.add_handler(CommandHandler("palpite", palpite))
    app.add_handler(CommandHandler("vip", vip))
    app.add_handler(CommandHandler("favorito", favorito))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(build_simular_conversation())
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(?i)^placar\b"), palpite_text))
    setup_scheduler(app)
    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
