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
(`trade_allowed`, `CRITICAL_TRADE_BLOCKS`, `min_confidence`, `min_payout`) e,
desde 2026-07-29, pelo bloqueio `SR_ZONE` — **nenhuma** entrada com o preço na
zona de suporte/resistência, sem isenção por setup. Ver `ESTRATEGIA.md`. O que
a cadência contínua mudou é **quando** a varredura acontece, não **o que**
aprova a entrada.

Frequência esperada de entradas (backtest 2026-07-29, confiança ≥ 80): ~30 por
1000 velas em M1 e ~37 por 1000 velas em M5 **por ativo**. Como o robô opera um
ativo por vez, intervalos de dezenas de minutos sem entrada são normais — em M5
mais ainda. Ver `BACKTEST_VALIDACAO.md` e `ROBO_E_SUPORTE.md` §4.

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
| M1 | 60s | segundos **5–20** da vela | segundos **0–8** |
| M5 | 300s | segundos **5–20** da vela | segundos **0–8** |
| M15 | 900s | segundos **5–20** da vela | segundos **0–8** |

Fluxo típico dentro de uma vela:

1. Worker varre ativos (candles do TF da operação).
2. Se acha padrão aprovado → trava `pending_signal`.
3. Espera a abertura da **próxima** vela (0–8s) para enviar a ordem.
4. Ordem expira no próprio timeframe (M1/M5/M15).
5. Após o resultado (e display curto), volta a monitorar.

### Relógio da janela (âncora Bullex — 2026-08-21)

A compra **só** pode sair nos segundos 0–8 do início da vela (relógio da
corretora). O worker estima o relógio entre polls com:

`estimate = server_time_âncora + (agora − server_time_sampled_at)`

**Bug corrigido (relato: ordem ~45s antes do fechamento):** cada
`refresh_entry_window` em cache gravava o `server_time` *estimado* de volta
no estado **sem** renovar a âncora. O poll seguinte somava de novo o
elapsed desde o `connection_checked_at` antigo → drift composto → a janela
0–8 abria cedo no meio da vela.

Correção:

| Peça | Papel |
|---|---|
| `server_time_sampled_at` | Instantâneo da última amostra **absoluta** (Bullex/VPS) |
| `persist_server_clock=False` | Path estimado: atualiza só `current_candle_seconds` / flags, **não** a âncora |
| `persist_server_clock=True` | GET `/sessions/status` ou VPS absoluto / pós-scan monotônico |
| `SERVER_CLOCK_RESAMPLE_SECONDS=45` | Força novo GET de `server_time` se a âncora passou de 45s |

Teste de regressão:
`tests.test_auto_trader.AutoTraderStateTests.test_cached_entry_window_refresh_does_not_compound_clock_drift`.

> **Não estique `result_display_until` (5s).** Ele bloqueia `prepare_cycle`
> (`auto_trader.py`) e `result_display_expired` (`main.py`): o reset
> pós-resultado é o que libera a próxima varredura. Em 2026-07-28 esse valor
> subiu para 12s para dar tempo à narração do placar; a análise passou a
> terminar no fim da janela 5–20s, a compra perdeu a janela curta
> (`[ENTRY_WINDOW_MISSED]`) e o acerto caiu de 55,6% para 31,8%. Revertido em
> 2026-07-29 — narração agora usa o canal `result_voice`
> (`NARRACAO_ROBO.md` §4c). Em 2026-08-04 a compra foi para **0–8s** (não
> alongar o display) para absorver latência de refresh/canal sem voltar ao
> bug do display 12s.
>
> O overlay pode mostrar WIN/LOSS + ativo por **até 60s** (`RESULT_OVERLAY_DISPLAY_MS`)
> **sem** alterar os 5s de `result_display_until`. Ver `OVERLAY_ROBO.md` §8.

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
| `ENTRY_WINDOWS` | `main.py` | Compra só em 0–8s |

## 5. Campo `cycle_minutes` (compatibilidade)

O campo continua existindo no estado, SQLite e API para não quebrar o
painel. Significado atual:

- **Duração da vela/ordem** do timeframe (1 / 5 / 15), **não** o antigo
  intervalo entre análises.
- O frontend exibe “monitora a cada vela · expira em X min”.

## 6. Metas operacionais vs cadência real (2026-08-15)

`MIN_OPERATIONS_PER_HOUR_BY_TIMEFRAME` no código:

| TF | Meta (recovery) | Teto físico (1 op / vela) |
|---|---|---|
| M1 | 12 | 60 |
| M5 | 6 | 12 |
| M15 | 2 | 4 |

A meta **não é garantia**. Medido nas contas com robô ligado ≥30 min
(pós-substitution 13/08–15/08, **antes** dos cortes de qualidade):

| TF | Mediana ops/hora | Média | Faixa típica (p25–p75) |
|---|---|---|---|
| M1 | **~1** | 1,7 | 0,5–3,3 |
| M5 | **~2** | 1,8 | 1,5–2,3 |
| M15 | amostra 1 conta | ~0,7 | — |

Com os cortes ao vivo (WEAK PUT, vela fraca, PUT chase/corpo/pavio), o
histórico **já ocorrido** perderia ~metade das linhas. Na prática o recovery
ainda pode preencher com **WEAK CALL** de vela forte no mesmo ciclo, então a
cadência por conta tende a:

| TF | Tendência por hora (conta ligada o tempo todo) |
|---|---|
| M1 | **1 a 3** (mais perto de 1–2; picos ~3 em hora boa) |
| M5 | **~1** (poucas velas; M5 já era baixo) |
| M15 | **0 a 1** (muitas velas sem setup) |

Não esperar 12 ops/hora no M1: isso era teto de “preencher ciclo com WEAK
PUT”. Esse volume era o que puxava o acerto para baixo.

## 7. Testes

- `tests/test_continuous_market_analysis.py` — cadência contínua,
  backoff operacional, preservação das janelas 0–8s e da expiração,
  integração “padrão achado → espera entrada”.
- Suites relacionadas atualizadas: `test_operation_cycle_strategies`,
  `test_robot_config_operation_settings`, `test_phase36_continuous_cycle`
  (mensagens / `make_signal` com `price_action_setup`).

## 8. Pasta backup

A pasta `/root/backup` **não** deve ser alterada. Esta mudança vive só
em `/root/Backend`, `/root/Frontend`, `/root/docs` e no deploy
`/opt/elcapo`.

## 9. Histórico

- **2026-08-21 (compra ~45s cedo)** — Drift composto no relógio estimado da
  janela de entrada: `update_entry_window` gravava `server_time` estimado
  sem nova âncora. Fix: `server_time_sampled_at` + `persist_server_clock` +
  resample a cada 45s. Ver §3 “Relógio da janela”.
- **2026-08-04 (janela de compra 0–8s)** — Produção perdia setups em ~6,3s
  (`ENTRY_WINDOW_MISSED`). Janela ampliada e poll do worker mais fino;
  `result_display_until` permanece 5s. Ver também `ESTRATEGIA.md`
  (frequência sem afrouxar anti-loss).
- **2026-08-15** — Overlay mostra WIN/LOSS+ativo no máximo 60s (UI), ciclo
  operacional permanece 5s. Ver `OVERLAY_ROBO.md` §8.
- **2026-07-29 (suporte/resistência)** — `SR_ZONE` volta como bloqueio crítico
  sem isenções e a frequência esperada de entradas passa a ser documentada com
  base em backtest. Ver §1 e `BACKTEST_VALIDACAO.md`.
- **2026-07-29** — Reverte `result_display_until` para 5s (12s atrasava a
  varredura na vela e fazia perder a janela de compra). Narração do placar
  migrou para `result_voice`. Ver §3 e `NARRACAO_ROBO.md` §4c.
- **2026-07-24 (narração)** — Overlay sem timer “próxima análise”; frases
  “El Capo está analisando…” / “Identificando uma oportunidade…”. Ver
  `NARRACAO_ROBO.md`.
- **2026-07-24** — Análise contínua por vela; remove cooldown 5/15/45;
  mantém ENTRY_WINDOWS e expiração por timeframe; docs + UI alinhados.
