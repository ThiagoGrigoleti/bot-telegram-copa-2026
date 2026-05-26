CREATE UNIQUE INDEX IF NOT EXISTS uq_matches_date_teams
ON matches (match_date, home_team_id, away_team_id);
