import os
import logging
import psycopg2
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

load_dotenv(Path(__file__).resolve().parents[3] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

DB_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")


def _get_user_id(cur, telegram_id):
    cur.execute("SELECT id FROM users WHERE telegram_id = %s", (telegram_id,))
    row = cur.fetchone()
    return row[0] if row else None


def _get_todays_mission(cur):
    cur.execute(
        """
        SELECT dm.id, dm.match_id, dm.question, dm.correct_answer, dm.bonus_points,
               ht.name, at.name, m.match_date
        FROM daily_missions dm
        JOIN matches m ON dm.match_id = m.id
        JOIN teams ht ON m.home_team_id = ht.id
        JOIN teams at ON m.away_team_id = at.id
        WHERE m.match_date::date = CURRENT_DATE
           OR m.match_date::date = CURRENT_DATE + 1
        ORDER BY m.match_date ASC
        LIMIT 1
        """
    )
    return cur.fetchone()


def _get_user_answer(cur, user_id, mission_id):
    cur.execute(
        "SELECT is_correct FROM mission_answers WHERE user_id = %s AND mission_id = %s",
        (user_id, mission_id),
    )
    return cur.fetchone()


async def missao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id

    try:
        conn = psycopg2.connect(DB_URL)
        try:
            cur = conn.cursor()

            user_id = _get_user_id(cur, telegram_id)
            if not user_id:
                await update.message.reply_text("Use /start primeiro para se registrar.")
                return

            mission = _get_todays_mission(cur)
            if not mission:
                await update.message.reply_text("Nenhuma missão disponível hoje. Volte amanhã!")
                return

            mission_id, match_id, question, correct_answer, bonus_points, home_name, away_name, match_date = mission

            existing = _get_user_answer(cur, user_id, mission_id)
            if existing:
                is_correct = existing[0]
                if is_correct:
                    await update.message.reply_text(
                        f"✅ Você já completou a missão de hoje! +{bonus_points} pts garantidos."
                    )
                else:
                    await update.message.reply_text("❌ Você já tentou hoje. Tente amanhã!")
                return

            date_str = match_date.strftime("%d/%m/%Y")
            text = (
                f"🎯 Missão do dia — {home_name} vs {away_name}\n"
                f"📅 {date_str}\n\n"
                f"{question}\n\n"
                f"⭐ Acerte e ganhe {bonus_points} pontos extras!"
            )

            keyboard = [
                [InlineKeyboardButton(home_name, callback_data=f"mission_{mission_id}_0")],
                [InlineKeyboardButton("Empate", callback_data=f"mission_{mission_id}_1")],
                [InlineKeyboardButton(away_name, callback_data=f"mission_{mission_id}_2")],
            ]

            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("missao: DB error for telegram_id=%s", telegram_id)
        await update.message.reply_text("Erro ao buscar missão. Tente novamente.")
        return

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def missao_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    telegram_id = query.from_user.id

    try:
        parts = query.data.split("_")
        mission_id = int(parts[1])
        option_idx = int(parts[2])
    except (IndexError, ValueError):
        logger.error("missao_callback: invalid callback_data=%s", query.data)
        return

    try:
        conn = psycopg2.connect(DB_URL)
        try:
            cur = conn.cursor()

            user_id = _get_user_id(cur, telegram_id)
            if not user_id:
                await query.edit_message_text("Use /start primeiro para se registrar.")
                return

            existing = _get_user_answer(cur, user_id, mission_id)
            if existing:
                await query.edit_message_text("Você já respondeu essa missão.")
                return

            cur.execute(
                """
                SELECT dm.correct_answer, dm.bonus_points, ht.name, at.name
                FROM daily_missions dm
                JOIN matches m ON dm.match_id = m.id
                JOIN teams ht ON m.home_team_id = ht.id
                JOIN teams at ON m.away_team_id = at.id
                WHERE dm.id = %s
                """,
                (mission_id,),
            )
            row = cur.fetchone()
            if not row:
                await query.edit_message_text("Missão não encontrada.")
                return

            correct_answer, bonus_points, home_name, away_name = row
            options = {0: home_name, 1: "Empate", 2: away_name}
            answer_text = options.get(option_idx, "")

            correct_map = {home_name: home_name, away_name: away_name, "Draw": "Empate", "Empate": "Empate"}
            normalized_correct = correct_map.get(correct_answer, correct_answer)
            is_correct = answer_text == normalized_correct

            cur.execute(
                """
                INSERT INTO mission_answers (user_id, mission_id, answer, is_correct)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, mission_id) DO NOTHING
                """,
                (user_id, mission_id, answer_text, is_correct),
            )

            if is_correct:
                cur.execute(
                    "UPDATE users SET points = points + %s WHERE id = %s",
                    (bonus_points, user_id),
                )
                reply = (
                    f"✅ Correto! +{bonus_points} pontos!\n\n"
                    f"🎯 Quer apostar no jogo de hoje?\n"
                    f"👉 /vip"
                )
            else:
                display_correct = normalized_correct
                reply = (
                    f"❌ Não foi dessa vez! A resposta era {display_correct}\n\n"
                    f"Acompanhe o jogo e tente amanhã!"
                )

            conn.commit()
            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("missao_callback: error for telegram_id=%s", telegram_id)
        await query.edit_message_text("Erro ao processar resposta. Tente novamente.")
        return

    await query.edit_message_text(reply)
