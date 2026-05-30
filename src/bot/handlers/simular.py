import os
import sys
import random
import asyncio
import logging
from pathlib import Path
import psycopg2
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
)

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "model"))
from predictor import predict_match

load_dotenv(Path(__file__).resolve().parents[3] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

DB_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")

CHOOSE_GROUP, SET_RESULTS, SHOW_TABLE = range(3)

GROUPS = list("ABCDEFGHIJKL")
COMPETITION = "FIFA World Cup 2026"
POINTS_WIN = 3
POINTS_DRAW = 1
MATCHES_PER_GROUP = 6
ADVANCE_SLOTS = 2
MC_SIMS = 3000


def _group_keyboard() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(f"Grupo {g}", callback_data=f"sim_group:{g}") for g in GROUPS]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    return InlineKeyboardMarkup(rows)


def _results_keyboard(matches: list, results: dict) -> InlineKeyboardMarkup:
    rows = []
    for i, m in enumerate(matches):
        chosen = results.get(i)
        home_label = ("✅ " if chosen == "H" else "") + m["home"]
        draw_label = ("✅ " if chosen == "D" else "") + "🤝 Empate"
        away_label = ("✅ " if chosen == "A" else "") + m["away"]
        rows.append([
            InlineKeyboardButton(home_label, callback_data=f"sim_res:{i}:H"),
            InlineKeyboardButton(draw_label, callback_data=f"sim_res:{i}:D"),
            InlineKeyboardButton(away_label, callback_data=f"sim_res:{i}:A"),
        ])
    return InlineKeyboardMarkup(rows)


def _results_text(group: str, matches: list, results: dict) -> str:
    lines = [f"⚽ Grupo {group} — Defina os 6 jogos", "Toque no resultado de cada partida:", ""]
    for i, m in enumerate(matches):
        mark = "✅" if i in results else "▫️"
        lines.append(f"{mark} {i + 1}. {m['home']} x {m['away']}")
    answered = len(results)
    lines.append("")
    lines.append(f"Definidos: {answered}/{MATCHES_PER_GROUP}")
    return "\n".join(lines)


def _fetch_group_matches(group: str) -> list:
    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ht.name, at.name, m.match_date
            FROM matches m
            JOIN teams ht ON ht.id = m.home_team_id
            JOIN teams at ON at.id = m.away_team_id
            WHERE m.stage = 'GROUP'
              AND m.external_id LIKE %s
            ORDER BY m.external_id ASC
            """,
            (f"WC26-{group}-%",),
        )
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    return [
        {"home": home, "away": away, "date": match_date.strftime("%Y-%m-%d")}
        for home, away, match_date in rows
    ]


def _ordered_teams(matches: list) -> list:
    teams = []
    for m in matches:
        for name in (m["home"], m["away"]):
            if name not in teams:
                teams.append(name)
    return teams


def _user_points(matches: list, results: dict, teams: list) -> dict:
    points = {t: 0 for t in teams}
    for i, m in enumerate(matches):
        outcome = results[i]
        if outcome == "H":
            points[m["home"]] += POINTS_WIN
        elif outcome == "A":
            points[m["away"]] += POINTS_WIN
        else:
            points[m["home"]] += POINTS_DRAW
            points[m["away"]] += POINTS_DRAW
    return points


def _model_strength(matches: list, preds: list, teams: list) -> dict:
    strength = {t: 0.0 for t in teams}
    for m, p in zip(matches, preds):
        edge = p["prob_home"] - p["prob_away"]
        strength[m["home"]] += edge
        strength[m["away"]] -= edge
    return strength


def _monte_carlo_advance(matches: list, preds: list, teams: list, strength: dict) -> dict:
    counts = {t: 0 for t in teams}
    for _ in range(MC_SIMS):
        sim_points = {t: 0 for t in teams}
        for m, p in zip(matches, preds):
            roll = random.random()
            if roll < p["prob_home"]:
                sim_points[m["home"]] += POINTS_WIN
            elif roll < p["prob_home"] + p["prob_draw"]:
                sim_points[m["home"]] += POINTS_DRAW
                sim_points[m["away"]] += POINTS_DRAW
            else:
                sim_points[m["away"]] += POINTS_WIN
        ranked = sorted(teams, key=lambda t: (-sim_points[t], -strength[t], t))
        for t in ranked[:ADVANCE_SLOTS]:
            counts[t] += 1
    return {t: counts[t] / MC_SIMS * 100 for t in teams}


def _compute_outcome(matches: list, results: dict) -> dict:
    teams = _ordered_teams(matches)
    points = _user_points(matches, results, teams)

    model_ranking = None
    strength = {t: 0.0 for t in teams}
    try:
        preds = [predict_match(m["home"], m["away"], m["date"], COMPETITION) for m in matches]
        strength = _model_strength(matches, preds, teams)
        advance = _monte_carlo_advance(matches, preds, teams, strength)
        model_ranking = sorted(advance.items(), key=lambda x: -x[1])[:ADVANCE_SLOTS]
    except Exception:
        logger.exception("Model forecast failed for group simulation")

    user_ranking = sorted(teams, key=lambda t: (-points[t], -strength[t], t))
    return {
        "points": points,
        "user_ranking": user_ranking,
        "model_ranking": model_ranking,
    }


def _table_text(group: str, outcome: dict) -> str:
    points = outcome["points"]
    user_ranking = outcome["user_ranking"]

    lines = [f"🏆 Grupo {group} — Sua Simulação", ""]
    for idx, team in enumerate(user_ranking):
        status = "avança ✅" if idx < ADVANCE_SLOTS else "eliminado ❌"
        lines.append(f"{idx + 1}. {team} — {points[team]}pts ({status})")

    model_ranking = outcome["model_ranking"]
    if model_ranking:
        lines.append("")
        lines.append("🤖 Modelo prevê:")
        for idx, (team, pct) in enumerate(model_ranking):
            lines.append(f"{idx + 1}. {team} — {pct:.0f}% chance de avançar")

    lines.append("")
    lines.append("Quer apostar no seu cenário?")
    lines.append("👉 /vip para acessar a liga VIP")
    return "\n".join(lines)


async def simular_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("sim_group", None)
    context.user_data.pop("sim_matches", None)
    context.user_data.pop("sim_results", None)
    try:
        await update.message.reply_text(
            "🔮 Simulador de Grupos — Copa 2026\n\nQual grupo você quer simular?",
            reply_markup=_group_keyboard(),
        )
    except Exception:
        logger.exception("Failed to start /simular")
        return ConversationHandler.END
    return CHOOSE_GROUP


async def choose_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        logger.exception("callback answer failed in choose_group")

    group = query.data.split(":", 1)[1]

    try:
        matches = await asyncio.to_thread(_fetch_group_matches, group)
    except Exception:
        logger.exception("DB error fetching group %s", group)
        await query.edit_message_text("Erro ao buscar o grupo. Tente /simular novamente.")
        return ConversationHandler.END

    if len(matches) != MATCHES_PER_GROUP:
        await query.edit_message_text(
            f"Grupo {group} ainda não está disponível. Tente outro grupo com /simular."
        )
        return ConversationHandler.END

    context.user_data["sim_group"] = group
    context.user_data["sim_matches"] = matches
    context.user_data["sim_results"] = {}

    try:
        await query.edit_message_text(
            _results_text(group, matches, {}),
            reply_markup=_results_keyboard(matches, {}),
        )
    except Exception:
        logger.exception("Failed to render results keyboard for group %s", group)
        return ConversationHandler.END
    return SET_RESULTS


async def set_result(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        logger.exception("callback answer failed in set_result")

    group = context.user_data.get("sim_group")
    matches = context.user_data.get("sim_matches")
    results = context.user_data.get("sim_results")

    if not group or not matches or results is None:
        await query.edit_message_text("Sessão expirada. Use /simular para recomeçar.")
        return ConversationHandler.END

    try:
        _, raw_index, outcome = query.data.split(":")
        index = int(raw_index)
    except (ValueError, IndexError):
        logger.exception("Malformed callback data: %s", query.data)
        return SET_RESULTS

    if index < 0 or index >= len(matches) or outcome not in ("H", "D", "A"):
        return SET_RESULTS

    results[index] = outcome

    if len(results) < MATCHES_PER_GROUP:
        try:
            await query.edit_message_text(
                _results_text(group, matches, results),
                reply_markup=_results_keyboard(matches, results),
            )
        except Exception:
            logger.exception("Failed to update results keyboard")
        return SET_RESULTS

    try:
        outcome_data = await asyncio.to_thread(_compute_outcome, matches, dict(results))
    except Exception:
        logger.exception("Failed to compute group outcome")
        await query.edit_message_text("Erro ao calcular a tabela. Tente /simular novamente.")
        return ConversationHandler.END

    again_keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔄 Simular outro grupo", callback_data="sim_again")]]
    )
    try:
        await query.edit_message_text(_table_text(group, outcome_data), reply_markup=again_keyboard)
    except Exception:
        logger.exception("Failed to render final table")
        return ConversationHandler.END
    return SHOW_TABLE


async def simular_again(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        logger.exception("callback answer failed in simular_again")

    context.user_data.pop("sim_group", None)
    context.user_data.pop("sim_matches", None)
    context.user_data.pop("sim_results", None)

    try:
        await query.edit_message_text(
            "🔮 Simulador de Grupos — Copa 2026\n\nQual grupo você quer simular?",
            reply_markup=_group_keyboard(),
        )
    except Exception:
        logger.exception("Failed to restart simulation")
        return ConversationHandler.END
    return CHOOSE_GROUP


async def simular_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("sim_group", None)
    context.user_data.pop("sim_matches", None)
    context.user_data.pop("sim_results", None)
    try:
        await update.message.reply_text("Simulação cancelada. Use /simular quando quiser.")
    except Exception:
        logger.exception("Failed to cancel simulation")
    return ConversationHandler.END


def build_simular_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("simular", simular_start)],
        states={
            CHOOSE_GROUP: [CallbackQueryHandler(choose_group, pattern=r"^sim_group:")],
            SET_RESULTS: [CallbackQueryHandler(set_result, pattern=r"^sim_res:")],
            SHOW_TABLE: [CallbackQueryHandler(simular_again, pattern=r"^sim_again$")],
        },
        fallbacks=[
            CommandHandler("simular", simular_start),
            CommandHandler("cancel", simular_cancel),
        ],
        allow_reentry=True,
    )
