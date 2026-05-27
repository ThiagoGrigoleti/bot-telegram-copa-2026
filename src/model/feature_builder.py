import os
import logging
import psycopg2
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

DB_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")


def _get_conn():
    return psycopg2.connect(DB_URL)


def _to_naive_dt(d) -> datetime:
    if isinstance(d, str):
        parsed = datetime.fromisoformat(d.replace("Z", "+00:00"))
    else:
        parsed = d
    return parsed.replace(tzinfo=None)


def _team_stats(cur, team_id: int, before_date) -> tuple[float, float, float]:
    cur.execute("""
        SELECT home_team_id, home_score, away_score
        FROM matches
        WHERE status = 'FINISHED'
          AND (home_team_id = %s OR away_team_id = %s)
          AND match_date < %s
          AND home_score IS NOT NULL AND away_score IS NOT NULL
        ORDER BY match_date DESC
        LIMIT 10
    """, (team_id, team_id, before_date))
    rows = cur.fetchall()
    if not rows:
        return 0.5, 0.0, 0.0
    form_total = scored_total = conceded_total = 0.0
    for db_home_id, home_score, away_score in rows:
        if db_home_id == team_id:
            scored, conceded = home_score, away_score
        else:
            scored, conceded = away_score, home_score
        scored_total += scored
        conceded_total += conceded
        if scored > conceded:
            form_total += 1.0
        elif scored == conceded:
            form_total += 0.5
    n = len(rows)
    return form_total / n, scored_total / n, conceded_total / n


def _h2h(cur, home_id: int, away_id: int, before_date) -> float:
    cur.execute("""
        SELECT home_team_id, home_score, away_score
        FROM matches
        WHERE status = 'FINISHED'
          AND ((home_team_id = %s AND away_team_id = %s)
               OR (home_team_id = %s AND away_team_id = %s))
          AND match_date < %s
          AND home_score IS NOT NULL AND away_score IS NOT NULL
        ORDER BY match_date DESC
        LIMIT 10
    """, (home_id, away_id, away_id, home_id, before_date))
    rows = cur.fetchall()
    if not rows:
        return 0.5
    total = 0.0
    for db_home_id, home_score, away_score in rows:
        if db_home_id == home_id:
            if home_score > away_score:
                total += 1.0
            elif home_score == away_score:
                total += 0.5
        else:
            if away_score > home_score:
                total += 1.0
            elif away_score == home_score:
                total += 0.5
    return total / len(rows)


def _days_since_last(cur, team_id: int, before_date) -> float | None:
    cur.execute("""
        SELECT match_date FROM matches
        WHERE status = 'FINISHED'
          AND (home_team_id = %s OR away_team_id = %s)
          AND match_date < %s
        ORDER BY match_date DESC
        LIMIT 1
    """, (team_id, team_id, before_date))
    row = cur.fetchone()
    if not row:
        return None
    return (_to_naive_dt(before_date) - row[0].replace(tzinfo=None)).days


def _build_features_with_cursor(
    cur,
    home_team: str,
    away_team: str,
    match_date,
    competition: str,
    stage: str | None = None,
    home_score: int | None = None,
    away_score: int | None = None,
) -> dict | None:
    cur.execute("SELECT id, elo_rating FROM teams WHERE name = %s", (home_team,))
    row = cur.fetchone()
    if not row:
        return None
    home_id, elo_home = row

    cur.execute("SELECT id, elo_rating FROM teams WHERE name = %s", (away_team,))
    row = cur.fetchone()
    if not row:
        return None
    away_id, elo_away = row

    form_home, avg_scored_home, avg_conceded_home = _team_stats(cur, home_id, match_date)
    form_away, avg_scored_away, avg_conceded_away = _team_stats(cur, away_id, match_date)
    h2h_home_winrate = _h2h(cur, home_id, away_id, match_date)
    days_home = _days_since_last(cur, home_id, match_date)
    days_away = _days_since_last(cur, away_id, match_date)

    is_knockout = 1 if (
        stage is not None
        and "world cup" in competition.lower()
        and "group" not in stage.lower()
    ) else 0

    result = None
    if home_score is not None and away_score is not None:
        if home_score > away_score:
            result = 2
        elif home_score < away_score:
            result = 0
        else:
            result = 1

    return {
        "elo_home": elo_home,
        "elo_away": elo_away,
        "elo_diff": elo_home - elo_away,
        "form_home": form_home,
        "form_away": form_away,
        "h2h_home_winrate": h2h_home_winrate,
        "avg_goals_scored_home": avg_scored_home,
        "avg_goals_scored_away": avg_scored_away,
        "avg_goals_conceded_home": avg_conceded_home,
        "avg_goals_conceded_away": avg_conceded_away,
        "days_since_last_match_home": days_home,
        "days_since_last_match_away": days_away,
        "is_knockout": is_knockout,
        "result": result,
    }


def build_features_for_match(
    home_team: str,
    away_team: str,
    match_date: str,
    competition: str,
    stage: str | None = None,
    home_score: int | None = None,
    away_score: int | None = None,
) -> dict | None:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        features = _build_features_with_cursor(
            cur, home_team, away_team, match_date, competition, stage, home_score, away_score
        )
        cur.close()
        return features
    except Exception:
        logger.exception("build_features_for_match failed: %s vs %s @ %s", home_team, away_team, match_date)
        raise
    finally:
        conn.close()


from collections import defaultdict

def build_training_dataset() -> pd.DataFrame:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        
        # 1. Obter mapeamento de times e ELOs
        cur.execute("SELECT id, name, elo_rating FROM teams")
        teams_rows = cur.fetchall()
        team_id_map = {row[1]: row[0] for row in teams_rows}
        team_elo_map = {row[0]: row[2] for row in teams_rows}
        
        # 2. Obter todas as partidas finalizadas ordenadas por data
        cur.execute("""
            SELECT home_team_id, away_team_id, match_date,
                   competition, stage, home_score, away_score
            FROM matches
            WHERE status = 'FINISHED'
              AND home_score IS NOT NULL
              AND away_score IS NOT NULL
            ORDER BY match_date ASC
        """)
        rows = cur.fetchall()
        logger.info("build_training_dataset: fetched %d finished matches", len(rows))

        # 3. Converter para lista de dicionários para processamento rápido
        all_matches = []
        matches_by_team = defaultdict(list)
        
        for home_id, away_id, m_date, comp, stage, home_score, away_score in rows:
            match_data = {
                "home_team_id": home_id,
                "away_team_id": away_id,
                "match_date": m_date,
                "competition": comp or "",
                "stage": stage or "",
                "home_score": home_score,
                "away_score": away_score
            }
            all_matches.append(match_data)
            matches_by_team[home_id].append(match_data)
            matches_by_team[away_id].append(match_data)

        records = []
        for m in all_matches:
            home_id = m["home_team_id"]
            away_id = m["away_team_id"]
            match_date = m["match_date"]
            competition = m["competition"]
            stage = m["stage"]
            home_score = m["home_score"]
            away_score = m["away_score"]
            
            # Recuperar ELOs
            elo_home = team_elo_map.get(home_id, 1500.0)
            elo_away = team_elo_map.get(away_id, 1500.0)

            # Filtrar histórico de partidas dos times antes da data atual do jogo
            history_home = [x for x in matches_by_team[home_id] if x["match_date"] < match_date]
            history_away = [x for x in matches_by_team[away_id] if x["match_date"] < match_date]
            
            # Calcular estatísticas em memória
            # Form & Goals Home
            last_10_home = history_home[-10:]
            if not last_10_home:
                form_home, avg_scored_home, avg_conceded_home = 0.5, 0.0, 0.0
            else:
                form_total = scored_total = conceded_total = 0.0
                for h_m in last_10_home:
                    if h_m["home_team_id"] == home_id:
                        scored, conceded = h_m["home_score"], h_m["away_score"]
                    else:
                        scored, conceded = h_m["away_score"], h_m["home_score"]
                    scored_total += scored
                    conceded_total += conceded
                    if scored > conceded:
                        form_total += 1.0
                    elif scored == conceded:
                        form_total += 0.5
                n = len(last_10_home)
                form_home, avg_scored_home, avg_conceded_home = form_total / n, scored_total / n, conceded_total / n

            # Form & Goals Away
            last_10_away = history_away[-10:]
            if not last_10_away:
                form_away, avg_scored_away, avg_conceded_away = 0.5, 0.0, 0.0
            else:
                form_total = scored_total = conceded_total = 0.0
                for a_m in last_10_away:
                    if a_m["home_team_id"] == away_id:
                        scored, conceded = a_m["home_score"], a_m["away_score"]
                    else:
                        scored, conceded = a_m["away_score"], a_m["home_score"]
                    scored_total += scored
                    conceded_total += conceded
                    if scored > conceded:
                        form_total += 1.0
                    elif scored == conceded:
                        form_total += 0.5
                n = len(last_10_away)
                form_away, avg_scored_away, avg_conceded_away = form_total / n, scored_total / n, conceded_total / n

            # H2H (Confronto Direto) em memória
            h2h_history = [
                x for x in history_home 
                if x["home_team_id"] == away_id or x["away_team_id"] == away_id
            ]
            last_10_h2h = h2h_history[-10:]
            if not last_10_h2h:
                h2h_home_winrate = 0.5
            else:
                total_h2h = 0.0
                for h_m in last_10_h2h:
                    if h_m["home_team_id"] == home_id:
                        if h_m["home_score"] > h_m["away_score"]:
                            total_h2h += 1.0
                        elif h_m["home_score"] == h_m["away_score"]:
                            total_h2h += 0.5
                    else:
                        if h_m["away_score"] > h_m["home_score"]:
                            total_h2h += 1.0
                        elif h_m["away_score"] == h_m["home_score"]:
                            total_h2h += 0.5
                h2h_home_winrate = total_h2h / len(last_10_h2h)

            # Dias desde a última partida
            days_home = None if not history_home else (_to_naive_dt(match_date) - history_home[-1]["match_date"].replace(tzinfo=None)).days
            days_away = None if not history_away else (_to_naive_dt(match_date) - history_away[-1]["match_date"].replace(tzinfo=None)).days

            is_knockout = 1 if (
                stage != ""
                and "world cup" in competition.lower()
                and "group" not in stage.lower()
            ) else 0

            result = None
            if home_score is not None and away_score is not None:
                if home_score > away_score:
                    result = 2
                elif home_score < away_score:
                    result = 0
                else:
                    result = 1

            records.append({
                "elo_home": elo_home,
                "elo_away": elo_away,
                "elo_diff": elo_home - elo_away,
                "form_home": form_home,
                "form_away": form_away,
                "h2h_home_winrate": h2h_home_winrate,
                "avg_goals_scored_home": avg_scored_home,
                "avg_goals_scored_away": avg_scored_away,
                "avg_goals_conceded_home": avg_conceded_home,
                "avg_goals_conceded_away": avg_conceded_away,
                "days_since_last_match_home": days_home,
                "days_since_last_match_away": days_away,
                "is_knockout": is_knockout,
                "result": result,
            })

        cur.close()
    except Exception:
        logger.exception("build_training_dataset failed")
        raise
    finally:
        conn.close()

    df = pd.DataFrame(records).dropna().reset_index(drop=True)
    logger.info("build_training_dataset: %d rows after dropna", len(df))
    return df


if __name__ == "__main__":
    df = build_training_dataset()
    print(f"Dataset: {len(df)} jogos, {len(df.columns)} features")
    print(df.describe())

