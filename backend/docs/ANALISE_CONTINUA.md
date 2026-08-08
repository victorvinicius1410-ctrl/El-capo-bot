# Análise contínua do mercado — El Capo

Documento de referência da cadência contínua da IA técnica do robô.
Criado em **2026-07-24** na mudança que removeu o cooldown legado
(M1 a cada 5 min / M5 a cada 15 min / M15 a cada 45 min).

## 1. Objetivo

A IA **monitora o mercado o tempo todo** (a cada vela do timeframe
escolhido). Quando identifica um padrão das estratégias ativas
(Price Action, Psicologia de velas, Padrões de vela) e o portão de
qualidade aprova o setup, prepara a operação.

A **acertividade** continua protegida pelos mesmos filtros críticos
(`trade_allowed`, `CRITICAL_TRADE_BLOCKS`, `min_confidence`,
`min_payout`, e desde 2026-07-26 também `SR_ZONE` — não operar em
região de suporte/resistência) — ver `ESTRATEGIA.md`. O que mudou é
**quando** a varredura acontece, não **o que** aprova a entrada
(exceto a política SR_ZONE acima).

## 2. O que mudou vs. o modelo antigo

| Aspecto | Antes (legado) | Agora (contínuo) |
|---|---|---|
| Intervalo entre varreduras | M1→5 min, M5→15 min, M15→45 min | **A cada vela** do timeframe |
| Ao ligar o robô | Esperava o ciclo cheio (ex.: 5 min no M1) | Começa a analisar **na hora** |
| Sem oportunidade | Esperava o ciclo cheio de novo | Agenda a **próxima janela de análise** da vela |
| Após WIN/LOSS/DRAW | Esperava o ciclo cheio | Agenda a próxima janela da vela |
| Falha operacional (candles/sessão) | Ciclo cheio | Backoff curto (**30s**) |
| Janela de compra | 0–5s no início da vela | **Igual** (respeitada) |
| Expiração da ordem | 1m / 5m / 15m | **Igual** |

## 3. Regras do timeframe (inalteradas)

Arquivos: `backend/main.py` (`ENTRY_WINDOWS`, `TIMEFRAME_SECONDS`) e
`backend/signal_engine.py` (`ANALYSIS_WINDOW_BOUNDS`).

| Timeframe | Duração / expiração | Janela de análise (scan) | Janela de compra |
|---|---|---|---|
| M1 | 60s | segundos **5–20** da vela | segundos **0–5** |
| M5 | 300s | segundos **5–20** da vela | segundos **0–5** |
| M15 | 900s | segundos **5–20** da vela | segundos **0–5** |

Fluxo típico dentro de uma vela:

1. Worker varre ativos (candles do TF da operação).
2. Se acha padrão aprovado → trava `pending_signal`.
3. Espera a abertura da **próxima** vela (0–5s) para enviar a ordem.
4. Ordem expira no próprio timeframe (M1/M5/M15).
5. Após o resultado (e display curto), volta a monitorar.

## 4. Funções-chave

| Função / constante | Arquivo | Papel |
|---|---|---|
| `seconds_until_next_analysis` | `signal_engine.py` | Calcula espera até a próxima janela (ou backoff operacional) |
| `cycle_minutes_for_timeframe` | `signal_engine.py` | Duração da vela em minutos (1 / 5 / 15) — **não** é mais cooldown ×5 |
| `CYCLE_MINUTES_BY_TIMEFRAME` | `signal_engine.py` | `{M1:1, M5:5, M15:15}` |
| `OPERATIONAL_RETRY_SECONDS` | `signal_engine.py` | `30` — falhas de candles/sessão |
| `AutoTrader._schedule_continuous_wait` | `auto_trader.py` | Aplica `next_cycle_at` na cadência contínua |
| `schedule_next_analysis_session` | `auto_trader.py` | Sem setup → próxima vela (ou backoff) |
| `start` | `auto_trader.py` | `next_cycle_at = agora` (análise imediata) |
| `reset_cycle_after_result` | `auto_trader.py` | Pós-resultado → próxima janela |
| `candidate_meets_cycle_threshold` | `main.py` | Portão final de qualidade (inalterado) |
| `ENTRY_WINDOWS` | `main.py` | Compra só em 0–5s |

## 5. Campo `cycle_minutes` (compatibilidade)

O campo continua existindo no estado, SQLite e API para não quebrar o
painel. Significado atual:

- **Duração da vela/ordem** do timeframe (1 / 5 / 15), **não** o antigo
  intervalo entre análises.
- O frontend exibe “monitora a cada vela · expira em X min”.

## 6. Metas operacionais (referência)

`MIN_OPERATIONS_PER_HOUR_BY_TIMEFRAME`:

| TF | Meta mín. ops/hora |
|---|---|
| M1 | 12 |
| M5 | 6 |
| M15 | 2 |

São metas de capacidade (mais oportunidades por varredura contínua),
não garantias. A qualidade do setup ainda manda.

## 7. Testes

- `tests/test_continuous_market_analysis.py` — cadência contínua,
  backoff operacional, preservação das janelas 0–5s e da expiração,
  integração “padrão achado → espera entrada”.
- Suites relacionadas atualizadas: `test_operation_cycle_strategies`,
  `test_robot_config_operation_settings`, `test_phase36_continuous_cycle`
  (mensagens / `make_signal` com `price_action_setup`).

## 8. Pasta backup

A pasta `/root/backup` **não** deve ser alterada. Esta mudança vive só
em `/root/Backend`, `/root/Frontend`, `/root/docs` e no deploy
`/opt/elcapo`.

## 9. Histórico

- **2026-07-24 (narração)** — Overlay sem timer “próxima análise”; frases
  “El Capo está analisando…” / “Identificando uma oportunidade…”. Ver
  `NARRACAO_ROBO.md`.
- **2026-07-24** — Análise contínua por vela; remove cooldown 5/15/45;
  mantém ENTRY_WINDOWS e expiração por timeframe; docs + UI alinhados.
