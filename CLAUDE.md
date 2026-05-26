# CLAUDE.md

## Contexto de negócio
Bot Telegram de predição Copa 2026. Monetização via CPA iGaming (Betano, Sportingbet, KTO).
Usuários que convertem via link afiliado recebem flag `is_vip = TRUE` no banco.

## Stack
- python-telegram-bot v20 async
- PostgreSQL no Railway (mesmo projeto do Classroom Bot)
- APScheduler para jobs
- football-data.org para dados de futebol
- The Odds API para odds de mercado
- Streamlit Cloud para dashboard público

## Regras de código
- Zero comentários no código
- Todos os handlers do bot são async
- Error handling robusto em toda I/O externa
- Nunca hardcodar credenciais — tudo via variáveis de ambiente
- Blocos completos sempre, sem trechos parciais

## Regras de Contexto
- Use o arquivo `repomix-output.xml` para entender a arquitetura global do projeto (modelos preditivos, elo engine e handlers do bot).
- Evite re-ler arquivos que já estão mapeados no output do Repomix.
