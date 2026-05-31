import os
import psycopg2
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id SERIAL PRIMARY KEY,
    fifa_code VARCHAR(10) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    elo_rating FLOAT NOT NULL DEFAULT 1500.0,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS matches (
    id SERIAL PRIMARY KEY,
    external_id VARCHAR(50) UNIQUE,
    home_team_id INT REFERENCES teams(id),
    away_team_id INT REFERENCES teams(id),
    match_date TIMESTAMP NOT NULL,
    stage VARCHAR(50),
    home_score INT,
    away_score INT,
    status VARCHAR(20) DEFAULT 'SCHEDULED',
    competition VARCHAR(100),
    results_processed BOOLEAN DEFAULT FALSE,
    result_posted BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS predictions (
    id SERIAL PRIMARY KEY,
    match_id INT REFERENCES matches(id),
    prob_home FLOAT NOT NULL,
    prob_draw FLOAT NOT NULL,
    prob_away FLOAT NOT NULL,
    edge_home FLOAT,
    edge_draw FLOAT,
    edge_away FLOAT,
    is_value_bet BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(match_id)
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username VARCHAR(100),
    points INT DEFAULT 0,
    is_vip BOOLEAN DEFAULT FALSE,
    favorite_team_id INT REFERENCES teams(id),
    joined_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS guesses (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    match_id INT REFERENCES matches(id),
    guessed_home_score INT NOT NULL,
    guessed_away_score INT NOT NULL,
    points_earned INT DEFAULT 0,
    UNIQUE(user_id, match_id)
);

CREATE TABLE IF NOT EXISTS daily_missions (
    id SERIAL PRIMARY KEY,
    match_id INT REFERENCES matches(id),
    question TEXT NOT NULL,
    correct_answer TEXT,
    bonus_points INT DEFAULT 5
);

CREATE TABLE IF NOT EXISTS mission_answers (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    mission_id INT REFERENCES daily_missions(id),
    answer TEXT NOT NULL,
    is_correct BOOLEAN,
    UNIQUE(user_id, mission_id)
);

ALTER TABLE predictions ADD COLUMN IF NOT EXISTS edge_home FLOAT;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS edge_draw FLOAT;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS edge_away FLOAT;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS is_value_bet BOOLEAN DEFAULT FALSE;

ALTER TABLE users ADD COLUMN IF NOT EXISTS favorite_team_id INT REFERENCES teams(id);

ALTER TABLE matches ADD COLUMN IF NOT EXISTS result_posted BOOLEAN DEFAULT FALSE;

ALTER TABLE users ADD COLUMN IF NOT EXISTS source VARCHAR(50) DEFAULT 'organic';

DELETE FROM predictions a USING predictions b
WHERE a.match_id = b.match_id AND a.id < b.id;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'predictions_match_id_key'
    ) THEN
        ALTER TABLE predictions ADD CONSTRAINT predictions_match_id_key UNIQUE (match_id);
    END IF;
END $$;
"""


def init_db():
    url = os.environ["DATABASE_URL"]
    try:
        conn = psycopg2.connect(url)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(SCHEMA)
        cur.close()
        conn.close()
        print("✅ Schema criado")
    except Exception as e:
        print(f"❌ Erro: {e}")
        raise


if __name__ == "__main__":
    init_db()
