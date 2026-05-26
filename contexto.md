# CLAUDE.md — Copa Bot 2026

Arquivo de contexto persistente para Claude Code.
Leia inteiro antes de qualquer ação no projeto.

---

## O que é esse projeto

Bot do Telegram para a Copa do Mundo 2026 com dois objetivos simultâneos:

1. **Negócio**: monetização via afiliação iGaming CPA. Cada usuário que se cadastra numa casa de apostas pelo link do canal gera entre R$539–R$600 de comissão (CPA €100). Meta realista: 10–30 conversões durante o torneio.

2. **Diferencial técnico**: ao contrário de canais genéricos de palpite, esse bot possui um modelo de ML próprio que gera probabilidades calibradas por jogo e detecta value bets — onde o modelo diverge das odds do mercado em mais de 5%. Isso cria autoridade baseada em dados, não em opinião.

A Copa começa em 11 de junho de 2026. O prazo de entrega é imovível.

---

## Stack

| Camada | Tecnologia |
|---|---|
| Bot | `python-telegram-bot` v20 (async obrigatório) |
| ML | `scikit-learn` (XGBoost ou LogisticRegression) |
| Banco | PostgreSQL no Railway |
| ORM/Query | `psycopg2` direto ou `SQLAlchemy` core (sem ORM pesado) |
| Jobs | `APScheduler` (já usado no Railway do Classroom Bot) |
| Dados de futebol | `football-data.org` API (plano gratuito) |
| Odds | `The Odds API` (plano gratuito, fallback pago) |
| Dashboard | `Streamlit` (deploy no Streamlit Cloud, gratuito) |
| Infraestrutura | Railway (já configurado com PostgreSQL e deploy via GitHub) |

---

## Arquitetura do sistema

```
football-data.org API
        ↓
[data_collector.py] — coleta e persiste dados históricos e ao vivo
        ↓
PostgreSQL (Railway)
        ↓
[elo_engine.py] — calcula ELO atualizado das 32 seleções
[feature_builder.py] — monta feature matrix por jogo
[model.py] — XGBoost/LogReg, output: prob(home_win, draw, away_win)
        ↓
[value_bet_detector.py] — compara prob_modelo vs prob_implícita_odd
        ↓
[scheduler.py] — APScheduler, roda 24h antes de cada jogo
        ↓
[bot.py] — python-telegram-bot v20 async, publica no canal
        ↓
[dashboard.py] — Streamlit, rastreia acurácia pública em tempo real
```

---

## Estrutura de pastas esperada

```
copa-bot-2026/
├── CLAUDE.md                  ← este arquivo
├── README.md
├── requirements.txt
├── .env                       ← nunca commitar
├── railway.toml
│
├── src/
│   ├── data/
│   │   ├── collector.py       ← fetch da football-data.org API
│   │   └── odds_fetcher.py    ← fetch da The Odds API
│   │
│   ├── model/
│   │   ├── elo_engine.py      ← algoritmo ELO com K-factor por torneio
│   │   ├── feature_builder.py ← forma recente, H2H, contexto de fase
│   │   ├── trainer.py         ← treino e serialização do modelo
│   │   └── predictor.py       ← carrega modelo, gera probabilidades
│   │
│   ├── betting/
│   │   └── value_detector.py  ← edge = prob_modelo - prob_implícita
│   │
│   ├── bot/
│   │   ├── bot.py             ← Application entry point (v20 async)
│   │   ├── handlers/
│   │   │   ├── start.py
│   │   │   ├── palpite.py
│   │   │   ├── ranking.py
│   │   │   ├── missao.py
│   │   │   └── jogo.py
│   │   └── scheduler.py       ← APScheduler jobs
│   │
│   ├── bolao/
│   │   ├── scoring.py         ← lógica de pontuação dinâmica
│   │   └── league.py          ← liga VIP para convertidos via afiliado
│   │
│   └── dashboard/
│       └── app.py             ← Streamlit dashboard de acurácia
│
├── db/
│   └── migrations/            ← scripts SQL de criação de schema
│
└── tests/
    ├── test_elo.py
    ├── test_model.py
    └── test_scoring.py
```

---

## Schema do banco de dados

### `teams`
```sql
CREATE TABLE teams (
    id SERIAL PRIMARY KEY,
    fifa_code VARCHAR(3) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    elo_rating FLOAT NOT NULL DEFAULT 1500.0,
    updated_at TIMESTAMP DEFAULT NOW()
);
```

### `matches`
```sql
CREATE TABLE matches (
    id SERIAL PRIMARY KEY,
    external_id VARCHAR(50) UNIQUE,
    home_team_id INT REFERENCES teams(id),
    away_team_id INT REFERENCES teams(id),
    match_date TIMESTAMP NOT NULL,
    stage VARCHAR(50),           -- 'GROUP', 'R16', 'QF', 'SF', 'FINAL'
    home_score INT,
    away_score INT,
    status VARCHAR(20) DEFAULT 'SCHEDULED'  -- SCHEDULED, LIVE, FINISHED
);
```

### `predictions`
```sql
CREATE TABLE predictions (
    id SERIAL PRIMARY KEY,
    match_id INT REFERENCES matches(id),
    prob_home FLOAT NOT NULL,
    prob_draw FLOAT NOT NULL,
    prob_away FLOAT NOT NULL,
    edge_home FLOAT,            
    edge_draw FLOAT,
    edge_away FLOAT,
    is_value_bet BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### `users` (bolão)
```sql
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username VARCHAR(100),
    points INT DEFAULT 0,
    is_vip BOOLEAN DEFAULT FALSE,
    joined_at TIMESTAMP DEFAULT NOW()
);
```

### `guesses` (palpites do bolão)
```sql
CREATE TABLE guesses (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    match_id INT REFERENCES matches(id),
    guessed_home_score INT NOT NULL,
    guessed_away_score INT NOT NULL,
    points_earned INT DEFAULT 0,
    UNIQUE(user_id, match_id)
);
```

### `daily_missions`
```sql
CREATE TABLE daily_missions (
    id SERIAL PRIMARY KEY,
    match_id INT REFERENCES matches(id),
    question TEXT NOT NULL,
    correct_answer TEXT,
    bonus_points INT DEFAULT 5
);
```

### `mission_answers`
```sql
CREATE TABLE mission_answers (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    mission_id INT REFERENCES daily_missions(id),
    answer TEXT NOT NULL,
    is_correct BOOLEAN,
    UNIQUE(user_id, mission_id)
);
```

---

## Lógica do modelo ML

### ELO Engine
- K-factor por tipo de partida:
  - Amistoso: `K = 20`
  - Eliminatória Copa / Nations League: `K = 40`
  - Copa do Mundo fase de grupos: `K = 50`
  - Copa do Mundo mata-mata: `K = 60`
- Sem home advantage (Copa é em campo neutro nos 3 países-sede)
- ELO inicial: usar ratings históricos da FIFA como seed, não 1500 flat

### Features do modelo
- ELO atual dos dois times
- Delta ELO (home - away)
- Forma recente: win rate nos últimos 10 jogos de cada time
- Head-to-head: win rate nos últimos 5 encontros diretos
- Fase do torneio (encoded: grupos=0, mata-mata=1)
- Dias de descanso desde o último jogo

### Output
- `prob_home`, `prob_draw`, `prob_away` — devem somar 1.0
- Métrica de validação: **Brier Score** (calibração), não acurácia bruta
- Treino: Copa 2018 + 2022 + Eliminatórias
- Validação: amistosos e Nations League 2023–2025

### Value Bet Detection
```python
# Edge mínimo para reportar como value bet
VALUE_BET_THRESHOLD = 0.05

def detect_value(prob_model: float, odd_decimal: float) -> float:
    prob_implied = 1 / odd_decimal
    return prob_model - prob_implied
```

---

## Lógica de pontuação do bolão

### Resultado exato
- Acertar placar exato: **10 pontos**

### Resultado (W/D/L)
- Acertar apenas o vencedor/empate: **3 pontos base**
- Multiplicador dinâmico por incerteza:
  - Se `max(prob_home, prob_draw, prob_away) < 0.55` → resultado correto vale **2x**
  - Racional: acertar um jogo incerto deve valer mais do que acertar um favorito óbvio

### Missão diária
- Responder `/missao` corretamente: **+5 pontos bônus**

### Liga VIP
- Usuários tagueados como `is_vip = TRUE` participam de ranking separado
- Um usuário vira VIP ao registrar via link de afiliado do canal
- O ranking VIP é o canal de CTA mais forte — quem está na liga já depositou

---

## Bot — Comandos e handlers

| Comando | Ação |
|---|---|
| `/start` | Cadastra usuário, explica o canal, apresenta o bolão |
| `/palpite [jogo]` | Abre interface de palpite para o próximo jogo |
| `/ranking` | Mostra top 10 + posição do usuário |
| `/missao` | Entrega pergunta bônus do dia |
| `/jogo` | Mostra predição do modelo para o próximo jogo |
| `/vip` | Explica a liga VIP e exibe link de afiliado contextualizado |
| `/acuracia` | Link para o dashboard Streamlit com histórico do modelo |

### Regras de handler (python-telegram-bot v20)
- Todos os handlers são `async def`
- Usar `ContextTypes.DEFAULT_TYPE` na assinatura
- Nunca bloquear o event loop — toda I/O de banco é async ou via `run_in_executor`
- Erros de API externa: capturar, logar, nunca deixar o bot travar

---

## Jobs do APScheduler

| Job | Frequência | Ação |
|---|---|---|
| `fetch_match_results` | A cada 30 min durante jogos | Atualiza `matches.status` e placar |
| `run_predictions` | 24h antes de cada jogo | Roda modelo, persiste em `predictions` |
| `process_guesses` | Ao detectar `status = FINISHED` | Calcula pontos, atualiza `users.points` |
| `send_daily_prediction` | Diário às 10h BRT | Publica predição do dia no canal |
| `send_ranking_update` | Após processar cada jogo | Notifica top 3 e usuários que subiram posição |

---

## Dashboard Streamlit

URL pública postada no canal — prova social baseada em dados.

**Conteúdo:**
- Tabela: todos os jogos com predição do modelo vs resultado real
- Ícone ✅/❌ por jogo
- Acurácia acumulada (linha do tempo)
- Value bets sinalizados vs retorno simulado
- Brier Score acumulado

**Deploy:** Streamlit Cloud (gratuito), conectado ao mesmo PostgreSQL do Railway via `st.secrets`.

---

## Contexto de negócio (afiliação)

- **Programas cadastrados**: Betano Affiliates, Sportingbet, KTO (aprovação em andamento)
- **Modelo**: CPA — R$539 por novo usuário qualificado e depositante
- **CTA principal**: comando `/vip` + posts contextualizados quando modelo detecta value bet
- **Segmentação**: usuários VIP (convertidos) ficam numa liga separada — isso os mantém engajados após a conversão e facilita RevShare futuro
- **Canal Telegram**: veículo principal de audiência
- **Orçamento de aquisição**: R$400–600 em shoutouts em grupos Telegram de futebol pré-Copa

---

## Regras de código (inegociáveis)

- **Zero comentários no código** — o código deve ser autoexplicativo
- **Sem métodos deprecated** — verificar versão da lib antes de usar qualquer API
- **Error handling robusto em toda I/O externa** — API de futebol, odds, banco
- **Blocos completos** — nunca entregar código parcial ou com `# ... resto do código`
- **Async consistente** — nenhum handler síncrono no bot, nenhum `time.sleep()` em código assíncrono
- **Variáveis de ambiente** — toda credencial vai em `.env`, nunca hardcoded
- **Testes** — funções de scoring e ELO têm cobertura mínima de testes unitários

---

## Variáveis de ambiente necessárias

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHANNEL_ID=
FOOTBALL_DATA_API_KEY=
ODDS_API_KEY=
DATABASE_URL=postgresql://...
BETANO_AFFILIATE_LINK=
SPORTINGBET_AFFILIATE_LINK=
KTO_AFFILIATE_LINK=
```

---
