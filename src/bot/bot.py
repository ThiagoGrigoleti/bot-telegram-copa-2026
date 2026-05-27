import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, CommandHandler

from handlers.start import start
from handlers.jogo import jogo
from handlers.ranking import ranking
from handlers.palpite import palpite
from handlers.vip import vip

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("jogo", jogo))
    app.add_handler(CommandHandler("ranking", ranking))
    app.add_handler(CommandHandler("palpite", palpite))
    app.add_handler(CommandHandler("vip", vip))
    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
