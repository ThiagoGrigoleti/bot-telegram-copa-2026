from telegram import Update
from telegram.ext import ContextTypes


async def ranking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Em breve: classificação do bolão.")
