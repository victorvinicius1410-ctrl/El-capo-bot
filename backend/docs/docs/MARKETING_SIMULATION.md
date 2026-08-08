# Conta marketing — visual idêntico ao cliente

Contas com `account_type=marketing` e `marketing_mode=simulation` usam **a mesma
tela, as mesmas configurações e o mesmo fluxo** de um cliente normal.

## O que muda (somente isto)

1. **Shift+O**: painel oculto para editar/excluir histórico e gerar operações
   manuais (valor, ativo, direção, resultado) ou gerar o placar completo de
   uma vez. O **payout é consultado automaticamente** na Bullex por ativo.
2. **Taxa de acertividade** (`marketing_win_rate`, admin): usada **somente**
   no Shift+O quando o resultado da operação manual está em **AUTO**. Não
   altera o placar das operações ao vivo.

## Fluxo idêntico ao cliente

- Conectar conta BullEx (obrigatório para iniciar e para consultar payout).
- Botão **Iniciar Operação** abre o mesmo pop:
  timeframe **1m / 5m / 15m**, mercado, valor, stop win/loss (por valor ou por
  operações), gale. Ver `STOP_WIN_LOSS.md`.
- Ciclos de análise **contínuos** (a cada vela do timeframe):
  - M1 → monitora a cada **1 min** (expira 1m)
  - M5 → monitora a cada **5 min** (expira 5m)
  - M15 → monitora a cada **15 min** (expira 15m)
  - Compra só nos **0–5s** do início da vela. Ver `ANALISE_CONTINUA.md`.
- Overlay, narrador, dashboard, histórico e corretora usam os **mesmos**
  endpoints (`/robot/*`, `/bullex/*`).
- Tela **Configurações → Conta Corretora** é idêntica (logo Bullex, métricas,
  CTA). Ver [`CONFIGURACOES.md`](./CONFIGURACOES.md).
- Tela **Histórico**: ver [`HISTORICO.md`](./HISTORICO.md).

Não existe robô visual paralelo nem configs separadas de ciclo para marketing.

### Iniciar Operação com placar do Shift+O

O Shift+O grava o placar/histórico no mesmo estado do robô
(`sync_marketing_display_to_robot` → wins/profit + `robot_trade_history`).
Isso fazia o `POST /robot/start` responder **403 `STOP_WIN_HIT` /
`STOP_LOSS_HIT`** (via `daily_stop_reason` / placar), enquanto Configurações
também “não iniciava”.

**Comportamento (2026-08-07):** em sessão `account_type=marketing` +
`marketing_mode=simulation`, o start **zera automaticamente o placar da
sessão** (`reset_score`, com `stop_reset_at`) quando o stop bloquearia e
segue o fluxo normal. Log: `[MARKETING_AUTO_RESET_SCORE_ON_START]`.

Além disso, o start limpa o backoff de sessão **antes** de consultar
status/account (evita falso `BULLEX_NOT_CONNECTED`) e o estado em memória
ligado não é mais sobrescrito com `enabled=False` na rehidratação.

**2026-08-07 (noite):** o front **não bloqueia** Iniciar Operação por
`connected:false` do poll (comum após stop + backoff). Overlay, diálogo e
Configurações → Robô sempre disparam o fluxo; o gateway evita devolver
`connected:false` inventado em backoff sem cache (`BACKOFF_BYPASS_NO_CACHE`).
Ver `ROBO_E_SUPORTE.md`.

**2026-08-07 (noite+ — pop-up “Confirmar e iniciar” sem efeito):** com a
Bullex em `SESSION_NOT_FOUND`, o `memory_account_fallback` mantinha a UI
“conectada” sem saldo. O `POST /robot/start` devolvia
`INSUFFICIENT_BALANCE` com `balance=None` (falso “faça um depósito”) e a
operação não ligava. Agora:

1. Reconecta com credenciais salvas **antes** do fallback de memória.
2. Memory fallback no start **só** vale com saldo REAL positivo.
3. Saldo desconhecido → `409 BULLEX_NOT_CONNECTED` (pedir reconexão), nunca
   `INSUFFICIENT_BALANCE`.
4. Botão **Confirmar e iniciar** no `StartOperationDialog` reforçado
   (`stopPropagation`, validação de `enabled`, mensagens amigáveis).

Logs: `[ROBOT_START_BLOCKED_BALANCE_UNKNOWN]`,
`[REAL_BALANCE_START_MEMORY_SKIPPED]`, `[ROBOT_START_BLOCKED_ACCOUNT_CONTRACT]`.

Cliente normal continua exigindo **Reiniciar placar**
(`409 RESET_CYCLE_REQUIRED`).

## Operações ao vivo → resultado real no placar

Quando a pessoa **inicia operação** e a ordem fecha na Bullex,
`finish_monitored_trade` → `apply_marketing_result_override`:

1. Mantém o **WIN/LOSS real** e o **lucro real** da corretora.
2. Soma esses valores no placar do robô (wins/losses/profit), igual a um
   cliente normal — inclusive se o placar já tiver sido montado no Shift+O.
3. Espelha a operação em `marketing_simulated_trades` (`source=marketing_demo`,
   schema atual) para o painel Shift+O permanecer alinhado. O `order_id`
   Bullex **não** vira `synthetic_sequence` (IDs > 2^31-1 estouravam INTEGER
   e geravam `MARKETING_HISTORY_SYNC_FAILED` / HTTP 400): ele é guardado na
   coluna `broker_order_id`
   (`backend/migration_marketing_broker_order_id.sql`).

O `broker_order_id` é o que liga o espelho à operação real. A sincronização
usa esse identificador no histórico do robô, e a exclusão por `order_id`
encontra o espelho por ele. Sem o vínculo, a operação ao vivo aparecia
duplicada no Histórico e voltava depois de excluída. O insert e a listagem
toleram o banco sem a coluna (o vínculo fica nulo até a migration rodar).

A taxa `marketing_win_rate` **não** força mais WIN/LOSS nas operações ao vivo.

```text
broker_result = WIN | LOSS (Bullex)
placar.wins / placar.losses / placar.profit += resultado real
Shift+O history ← espelho da mesma operação
```

## Shift+O

1. Conta marketing autenticada → **Shift+O**.
2. Painel flutuante alto (`h ≈ 94vh`, até 920px; `100dvh` em mobile) com
   **abas** e scroll interno na aba ativa (sem backdrop).
3. Abas fixas no topo do painel:
   - **Manual** — criar operação e salvar valores
   - **Placar** — gerar histórico pelo placar desejado
   - **Histórico (N)** — listar, editar (lápis) e **excluir (lixeira)**
4. Em telas baixas/estreitas a aba **Histórico** deixa a lixeira sempre
   acessível (antes as ações ficavam no fim de um scroll longo atrás dos
   formulários). Atalhos na Manual/Placar: “Ver histórico / Ir para histórico”.
5. Após **Nova operação** ou **Gerar histórico do placar**, o painel abre a
   aba Histórico automaticamente.
6. **Esc** ou novo **Shift+O** fecha; a operação da tela continua.
7. Atalho ignorado com foco em `input`/`textarea`/`select`/`contentEditable`.
8. Após create/edit/delete/generate, o backend sincroniza placar e histórico do
   robô (`sync_marketing_display_to_robot`).
9. Com o painel aberto, a página **Histórico** (`/history`) ganha coluna
   **Ações** sticky à esquerda (lixeira) — visível sem rolar a tabela toda.

### Operação manual

| Campo | Comportamento |
|-------|----------------|
| Valor | Entrada da operação |
| Payout | **Automático** via `GET /bullex/payouts?active=` |
| Ativo | Seletor OTC |
| Direção | AUTO / CALL / PUT |
| Resultado | AUTO (taxa `marketing_win_rate`), WIN ou LOSS forçado |
| Horário | **Agora** (padrão: instante do clique em Nova operação) ou **Personalizar data e hora** (`datetime-local` no fuso do navegador, enviado como ISO8601) |

Com horário personalizado, o `created_at` da operação (e o espelho no
histórico `/robot`) usa a data/hora escolhida. Sem personalização, o backend
grava o instante atual em UTC — comportamento anterior mantido.

### Gerar placar automático

1. Informe **Wins**, **Loss** e o **Valor de entrada** (único para todas as
   operações).
2. Escolha **Período** (M1/M5/M15) — define a janela de horários.
3. Escolha o **ativo** (ou Aleatório).
4. O **payout** é consultado na Bullex por ativo (sem campo manual). Se a
   corretora não responder, usa payout típico OTC (**85%**) para não bloquear
   a geração.
5. Clique em **Gerar histórico do placar**.

Ativos do seletor/pool aleatório: apenas **forex OTC** permitidos no robô
binário (ex.: EURUSD-OTC). Cripto (BTC/ETH) não entra — não há payout digital
nesse catálogo e quebrava a geração.

O backend **substitui** o histórico, embaralha WIN/LOSS, atribui `created_at`
espaçados pela cadência do período com jitter leve (±20%) e sincroniza o robô.
Limite: 100 operações por geração.

Depois de gerar o placar no Shift+O, novas operações ao vivo **somam** o
resultado real em cima desse placar (não substituem o histórico editável até
um novo “Gerar” ou edição manual).

## Arquivos principais

- Frontend: `AppShell.tsx`, `MarketingControlPanel.tsx`, `marketingHotkey.ts`,
  `marketingPanelContext.tsx`, `marketingSimulation.ts`,
  `marketingDemoSettings.ts`, `history.tsx`
- Backend: `main.py` (`apply_marketing_result_override`,
  `resolve_marketing_asset_payout`), `marketing_simulation_service.py`,
  `admin_router.py`
- Testes: `tests/test_marketing_real_operation_results.py`,
  `tests/test_marketing_simulation_management.py`

## Autenticação e isolamento

Endpoints `/marketing-simulation/*` exigem sessão marketing. `company_id` e
`user_id` vêm só da sessão.

## Contratos REST (painel Shift+O)

- `POST /marketing-simulation/trades` — gera trade (`201`); body opcional:
  `amount`, `payout`, `asset`, `direction`, `result`, `created_at` (ISO8601;
  se omitido, usa o instante atual)
- `POST /marketing-simulation/generate-history` — substitui o histórico (`201`);
  body: `wins`, `losses`, `amount` (valor de entrada), `period` (`M1`|`M5`|`M15`),
  `asset` opcional; `payout` opcional (se omitido, consulta Bullex)
- `GET /marketing-simulation/history` — lista (`200`)
- `GET /marketing-simulation/stats` — placar (`200`)
- `PATCH /marketing-simulation/trades/{trade_id}` — edita (`200`)
- `DELETE /marketing-simulation/trades/{trade_id}` — exclui (`204`)

  O `{trade_id}` aceita UUID de `marketing_simulated_trades` **ou** o
  `order_id` da Bullex presente em `/robot/history`. IDs não-UUID não consultam
  a coluna UUID do Supabase (evita 400/500): a busca usa `broker_order_id` e,
  sem espelho correspondente, remove a operação das três fontes do histórico
  do robô (`robot_trade_history`, `robot_trades` e a memória do `auto_trader`)
  e ajusta o placar. Ver `HISTORICO.md` §“As três fontes do histórico”.
  Se a operação já não existir, responde `204` (idempotente) — não devolve
  mais `SIMULATED_TRADE_NOT_FOUND` nesse fluxo.

## Testes

```bash
cd /opt/elcapo/backend
docker compose -p elcapooneline run --rm --no-deps -v /root/Backend:/workspace -w /workspace \
  backend python -m unittest tests.test_marketing_real_operation_results \
  tests.test_marketing_simulation_management

cd /root/Frontend
npm test
```

## Histórico

- **2026-08-07** — `POST /robot/start` em conta marketing auto-zera o placar
  (`reset_score` / `stop_reset_at`) quando Stop Win/Loss (placar ou histórico
  do Shift+O) bloquearia o start — corrige 403 `STOP_*_HIT`. Teste:
  `test_marketing_start_auto_resets_score_when_stop_would_block`.
- **2026-08-07 (noite+)** — Pop-up Confirmar e iniciar: saldo desconhecido
  (`SESSION_NOT_FOUND` + memory sem balance) deixa de virar falso
  `INSUFFICIENT_BALANCE`; pede reconexão Bullex. Testes:
  `test_start_rejects_stale_memory_fallback_without_balance`,
  `test_real_robot_start_still_blocks_when_reconnect_fails`.
- **2026-08-03** — Painel Shift+O em abas (Manual / Placar / Histórico) para a
  lixeira ficar acessível em telas menores; após criar/gerar abre a aba
  Histórico. Em `/history`, coluna Ações sticky à esquerda.
- **2026-07-29** — Espelho da operação ao vivo guarda `broker_order_id`
  (migration `migration_marketing_broker_order_id.sql`): fim da duplicação
  order_id × UUID no Histórico e da operação excluída que voltava no F5.
  Sync e exclusão passam a alinhar `robot_trade_history`, `robot_trades` e a
  memória do `auto_trader`.
- **2026-07-25 (noite)** — Operação manual no Shift+O permite escolher
  data/hora ou manter **Agora** (instante da criação). API aceita
  `created_at` opcional em `POST /marketing-simulation/trades`.
- **2026-07-25 (tarde)** — `save_simulated_trade` valida UUIDs, tipa payload,
  faz retry em conflito de `synthetic_sequence` e `_request` inclui o body
  do PostgREST no erro (diagnóstico de `MARKETING_HISTORY_SYNC_FAILED`).
- **2026-07-25** — Corrige exclusão com fantasma em memória (`SIMULATED_TRADE_NOT_FOUND`
  no re-clique): `delete_marketing_robot_history_item` limpa `auto_trader` +
  DELETE idempotente. Espelho ao vivo aloca `synthetic_sequence` seguro
  (sem overflow do order_id Bullex).
- **2026-07-24** — Exclusão no Histórico com order_id Bullex: fallback
  `robot_trade_history` + validação UUID no PostgREST (corrige “Failed to fetch”).
- **2026-07-23 (tarde)** — Operações ao vivo usam WIN/LOSS e lucro **reais** no
  placar; `marketing_win_rate` só no Shift+O (resultado AUTO). Espelho em
  `marketing_simulated_trades` com resultado real da corretora.
- **2026-07-23 (noite+)** — Corrige falha ao gerar histórico: remove crypto
  (ETH/BTC) do pool; fallback de payout 85% se Bullex falhar.
- **2026-07-23 (noite)** — Placar automático usa um único valor de entrada
  (em vez de Valor WIN/LOSS separados).
- **2026-07-23 (tarde)** — Payout automático via Bullex; painel maior com scroll;
  label Loss; horários na janela do período; exclusão no `/history` com Shift+O.
- **2026-07-23** — Seletor de ativo; resultado WIN/LOSS; geração por placar.
