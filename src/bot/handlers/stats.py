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
DASHBOARD_URL = os.getenv("DASHBOARD_URL")


def _actual_outcome(home_score: int, away_score: int) -> int:
    if home_score > away_score:
        return 0
    if home_score == away_score:
        return 1
    return 2


def _predicted_outcome(prob_home: float, prob_draw: float, prob_away: float) -> int:
    probs = (prob_home, prob_draw, prob_away)
    return probs.index(max(probs))


def _collect_stats() -> dict:
    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT p.prob_home, p.prob_draw, p.prob_away, m.home_score, m.away_score
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            WHERE m.status = 'FINISHED'
              AND m.home_score IS NOT NULL
              AND m.away_score IS NOT NULL
            ORDER BY m.match_date ASC
            """
        )
        rows = cur.fetchall()

        total = len(rows)
        hits = 0
        best_streak = 0
        current_streak = 0
        for prob_home, prob_draw, prob_away, home_score, away_score in rows:
            predicted = _predicted_outcome(prob_home, prob_draw, prob_away)
            actual = _actual_outcome(home_score, away_score)
            if predicted == actual:
                hits += 1
                current_streak += 1
                best_streak = max(best_streak, current_streak)
            else:
                current_streak = 0

        cur.execute("SELECT COUNT(*) FROM predictions WHERE is_value_bet = TRUE")
        value_bets = cur.fetchone()[0]

        cur.execute(
            """
            SELECT ht.name, at.name,
                   GREATEST(p.edge_home, p.edge_draw, p.edge_away) AS max_edge
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            JOIN teams ht ON m.home_team_id = ht.id
            JOIN teams at ON m.away_team_id = at.id
            WHERE p.is_value_bet = TRUE
            ORDER BY max_edge DESC
            LIMIT 1
            """
        )
        top_edge = cur.fetchone()

        cur.execute(
            "SELECT source, COUNT(*) FROM users GROUP BY source ORDER BY COUNT(*) DESC"
        )
        sources = cur.fetchall()

        cur.close()
    finally:
        conn.close()

    return {
        "total": total,
        "hits": hits,
        "best_streak": best_streak,
        "value_bets": value_bets,
        "top_edge": top_edge,
        "sources": sources,
    }


def _sources_block(sources: list) -> str:
    if not sources:
        return ""
    lines = ["", "📥 Origens dos usuários:"]
    for source, count in sources:
        lines.append(f"• {source}: {count} usuários")
    return "\n".join(lines)


def _pre_copa_message(sources: list) -> str:
    base = (
        "📊 Performance do Modelo — Pré-Copa 2026\n\n"
        "🧪 Backtesting (dados históricos 2000–2024):\n"
        "🎯 24.944 jogos testados\n"
        "✅ Acurácia: 59.3%\n"
        "📉 Brier Score: 0.1892\n"
        "   Baseline aleatório: 0.333\n\n"
        "🤖 Modelo: XGBoost treinado com 25.157 jogos históricos\n"
        f"🔗 Dashboard: {DASHBOARD_URL}\n\n"
        "Acurácia em tempo real a partir de 11/06"
    )
    return base + _sources_block(sources)


def _live_message(stats: dict) -> str:
    total = stats["total"]
    hits = stats["hits"]
    accuracy = hits / total * 100 if total else 0.0

    lines = [
        "📊 Performance do Modelo — Copa 2026",
        "",
        f"🎯 Jogos previstos: {total}",
        f"✅ Acertos: {hits} ({accuracy:.1f}%)",
        f"📈 Melhor sequência: {stats['best_streak']} acertos seguidos",
        "",
        f"💰 Value bets sinalizados: {stats['value_bets']}",
    ]

    top_edge = stats["top_edge"]
    if top_edge:
        home_name, away_name, max_edge = top_edge
        lines.append(f"🔝 Maior edge: {home_name} vs {away_name} (+{max_edge * 100:.1f}%)")

    lines.append("")
    lines.append(f"🔗 Histórico completo: {DASHBOARD_URL}")

    sources_block = _sources_block(stats["sources"])
    if sources_block:
        lines.append(sources_block)

    return "\n".join(lines)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        data = _collect_stats()
    except Exception:
        logger.exception("DB error in /stats")
        await update.message.reply_text("Erro ao buscar estatísticas. Tente novamente.")
        return

    message = _pre_copa_message(data["sources"]) if data["total"] == 0 else _live_message(data)
    await update.message.reply_text(message, disable_web_page_preview=False)
