from telegram import Update
from telegram.ext import ContextTypes


async def vip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Em breve: acesso à liga VIP.")
