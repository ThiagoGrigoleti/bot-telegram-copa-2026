from telegram import Update
from telegram.ext import ContextTypes


async def palpite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Em breve: faça seu palpite.")
