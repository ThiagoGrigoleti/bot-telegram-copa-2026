"""Dashboard público de acurácia — Copa 2026 Bot.

Deploy no Streamlit Cloud: defina DATABASE_URL em st.secrets["DATABASE_URL"]
(Settings -> Secrets). O código tenta st.secrets primeiro e cai para
os.getenv (DATABASE_URL / DATABASE_PUBLIC_URL) como fallback local.
"""

import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import psycopg2
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

st.set_page_config(layout="wide", page_title="Copa 2026 Bot", page_icon="⚽")

BASELINE_ACCURACY = 45.0
DB_KEYS = ("DATABASE_URL", "DATABASE_PUBLIC_URL")
PREDICTION_COLUMNS = [
    "match_id",
    "home",
    "away",
    "match_date",
    "status",
    "home_score",
    "away_score",
    "prob_home",
    "prob_draw",
    "prob_away",
]

PREDICTIONS_QUERY = """
    SELECT DISTINCT ON (m.id)
        m.id,
        ht.name,
        at.name,
        m.match_date,
        m.status,
        m.home_score,
        m.away_score,
        p.prob_home,
        p.prob_draw,
        p.prob_away
    FROM predictions p
    JOIN matches m ON m.id = p.match_id
    JOIN teams ht ON ht.id = m.home_team_id
    JOIN teams at ON at.id = m.away_team_id
    ORDER BY m.id, p.created_at DESC
"""

VALUE_BETS_QUERY = "SELECT COUNT(*) FROM predictions WHERE is_value_bet = TRUE"


def _resolve_db_url():
    for key in DB_KEYS:
        try:
            value = st.secrets[key]
        except Exception:
            value = None
        if value:
            return value
    for key in DB_KEYS:
        value = os.getenv(key)
        if value:
            return value
    return None


@st.cache_data(ttl=300)
def load_predictions():
    url = _resolve_db_url()
    if not url:
        return pd.DataFrame(columns=PREDICTION_COLUMNS), "missing_url"
    try:
        conn = psycopg2.connect(url)
        try:
            cur = conn.cursor()
            cur.execute(PREDICTIONS_QUERY)
            rows = cur.fetchall()
            cur.close()
        finally:
            conn.close()
    except Exception as exc:
        return pd.DataFrame(columns=PREDICTION_COLUMNS), str(exc)
    df = pd.DataFrame(rows, columns=PREDICTION_COLUMNS)
    df["match_date"] = pd.to_datetime(df["match_date"], errors="coerce")
    return df, None


@st.cache_data(ttl=300)
def load_value_bets_count():
    url = _resolve_db_url()
    if not url:
        return 0
    try:
        conn = psycopg2.connect(url)
        try:
            cur = conn.cursor()
            cur.execute(VALUE_BETS_QUERY)
            result = cur.fetchone()
            cur.close()
            return int(result[0]) if result else 0
        finally:
            conn.close()
    except Exception:
        return 0


def _predicted_outcome(row):
    probs = {"home": row["prob_home"], "draw": row["prob_draw"], "away": row["prob_away"]}
    return max(probs, key=probs.get)


def _actual_outcome(row):
    if row["status"] != "FINISHED":
        return None
    if pd.isna(row["home_score"]) or pd.isna(row["away_score"]):
        return None
    home_score = row["home_score"]
    away_score = row["away_score"]
    if home_score > away_score:
        return "home"
    if home_score < away_score:
        return "away"
    return "draw"


def _brier_score(row):
    actual = row["actual"]
    if actual is None:
        return None
    outcomes = ("home", "draw", "away")
    probs = (row["prob_home"], row["prob_draw"], row["prob_away"])
    return sum((prob - (1.0 if outcome == actual else 0.0)) ** 2 for outcome, prob in zip(outcomes, probs))


def enrich(df):
    if df.empty:
        return df
    enriched = df.copy()
    enriched["predicted"] = enriched.apply(_predicted_outcome, axis=1)
    enriched["actual"] = enriched.apply(_actual_outcome, axis=1)
    enriched["is_finished"] = enriched["actual"].notna()
    enriched["is_correct"] = enriched.apply(
        lambda row: None if row["actual"] is None else row["predicted"] == row["actual"], axis=1
    )
    enriched["brier"] = enriched.apply(_brier_score, axis=1)
    return enriched


def _favorite_label(row):
    if row["predicted"] == "home":
        return row["home"]
    if row["predicted"] == "away":
        return row["away"]
    return "Empate"


def _result_label(row):
    if not row["is_finished"]:
        return "Aguardando"
    return f"{int(row['home_score'])} x {int(row['away_score'])}"


def _hit_label(row):
    if not row["is_finished"]:
        return "—"
    return "✅" if row["is_correct"] else "❌"


def _pct(value):
    return f"{value * 100:.1f}%"


def render_header():
    st.title("\U0001f916 Copa 2026 — Modelo de Predição ML")
    st.caption("Acurácia rastreada em tempo real. Sem filtros, sem cherry-picking.")


def render_metrics(enriched, value_bets):
    finished = enriched[enriched["is_finished"]] if not enriched.empty else enriched
    total_predicted = int(len(finished))
    if total_predicted > 0:
        accuracy = finished["is_correct"].mean() * 100
        brier = finished["brier"].mean()
        accuracy_text = f"{accuracy:.1f}%"
        brier_text = f"{brier:.4f}"
        accuracy_delta = f"{accuracy - BASELINE_ACCURACY:+.1f} pp vs aleatório"
    else:
        accuracy_text = "—"
        brier_text = "—"
        accuracy_delta = None

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Jogos previstos (finalizados)", total_predicted)
    col2.metric("Acurácia geral", accuracy_text, delta=accuracy_delta)
    col3.metric("Brier Score médio", brier_text, help="Quanto menor, melhor. 0 = perfeito.")
    col4.metric("Value bets sinalizados", int(value_bets))


def render_table(enriched):
    st.subheader("Jogos & Predições")
    if enriched.empty:
        st.info("Nenhuma predição registrada ainda. A tabela aparece assim que o modelo gerar a primeira predição.")
        return

    table = enriched.sort_values("match_date", ascending=False, na_position="last").copy()
    display = pd.DataFrame(
        {
            "Jogo": table["home"] + " vs " + table["away"],
            "Data": table["match_date"].dt.strftime("%d/%m/%Y %H:%M").fillna("—"),
            "Pred. Casa": table["prob_home"].map(_pct),
            "Pred. Empate": table["prob_draw"].map(_pct),
            "Pred. Fora": table["prob_away"].map(_pct),
            "Favorito": table.apply(_favorite_label, axis=1),
            "Resultado Real": table.apply(_result_label, axis=1),
            "Acerto": table.apply(_hit_label, axis=1),
        }
    )
    st.dataframe(display, use_container_width=True, hide_index=True)


def render_chart(enriched):
    st.subheader("Acurácia Acumulada")
    if enriched.empty or not enriched["is_finished"].any():
        st.info("Gráfico de acurácia aparece quando houver jogos finalizados.")
        return

    finished = enriched[enriched["is_finished"]].sort_values("match_date", na_position="last").copy()
    finished = finished.reset_index(drop=True)
    finished["game_number"] = finished.index + 1
    finished["cumulative_accuracy"] = (
        finished["is_correct"].astype(float).expanding().mean() * 100
    )
    finished["hover"] = (
        finished["home"]
        + " vs "
        + finished["away"]
        + "<br>"
        + finished["match_date"].dt.strftime("%d/%m/%Y").fillna("—")
    )

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=finished["game_number"],
            y=finished["cumulative_accuracy"],
            mode="lines+markers",
            name="Acurácia acumulada",
            line=dict(color="#19c37d", width=3),
            marker=dict(size=7),
            customdata=finished["hover"],
            hovertemplate="%{customdata}<br>Acurácia: %{y:.1f}%<extra></extra>",
        )
    )
    fig.add_hline(
        y=BASELINE_ACCURACY,
        line_dash="dash",
        line_color="#ff6b6b",
        annotation_text="Baseline aleatório (45%)",
        annotation_position="top left",
    )
    fig.update_layout(
        template="plotly_dark",
        height=460,
        margin=dict(l=40, r=40, t=40, b=40),
        xaxis_title="Jogos (ordem cronológica)",
        yaxis_title="Acurácia acumulada (%)",
        yaxis=dict(range=[0, 100]),
        hovermode="x unified",
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)


def main():
    render_header()
    df, error = load_predictions()
    if error == "missing_url":
        st.error("DATABASE_URL não configurada. Defina em st.secrets (Streamlit Cloud) ou variável de ambiente.")
    elif error:
        st.error("Falha ao consultar o banco. Exibindo dashboard vazio.")

    enriched = enrich(df)
    value_bets = load_value_bets_count()

    render_metrics(enriched, value_bets)
    st.divider()
    render_table(enriched)
    st.divider()
    render_chart(enriched)
    st.caption("Atualiza automaticamente a cada 5 minutos.")


if __name__ == "__main__":
    main()
