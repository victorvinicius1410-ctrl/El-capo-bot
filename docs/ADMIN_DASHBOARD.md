# Dashboard administrativo

Painel em `/admin/dashboard` com KPIs do tenant, **win rate consolidado do El Capo**,
**resultados por horário (24h Brasília)** e rankings de clientes/ativos no período
selecionado (filtro de dias).

Atualizado em **2026-08-24**.

## Fonte de dados

| Camada | Arquivo | Papel |
|--------|---------|--------|
| Agregação | `backend/admin_dashboard_service.py` | Consolida clientes, lucro real, ativos, win rate e horários |
| API | `GET /admin/dashboard?days=` | Cache em memória + warmer; ver [`ADMIN_DASHBOARD_WARM.md`](./ADMIN_DASHBOARD_WARM.md) |
| UI | `frontend/src/routes/_authenticated/admin.dashboard.tsx` | Cards, tabela horária e listas |

O `company_id` vem da sessão autenticada, nunca do body do frontend.

## Filtro de datas

Mesmo seletor Meta Ads das outras telas (`DashboardDateFilter`, `maxDays=365`).
A API recebe `?days=` até hoje, com cutoff na **meia-noite de Brasília**.
Ver [`FILTRO_DATAS.md`](./FILTRO_DATAS.md) e
[`DATAS_BRASILIA.md`](./DATAS_BRASILIA.md).

## Operações reais (Win Rate El Capo)

Objeto `operations` no payload — soma operações reais de contas **client** e
**trial** no período. Contas **`marketing`** ficam **fora** quando
`ADMIN_DASHBOARD_EXCLUDE_MARKETING=true` (padrão em produção). No staging El
Capo 2 use `false` para manter marketing nos KPIs.

Demais filtros: ignora `is_simulated` e `source=marketing_demo`, deduplica por
`order_id`.

| Campo | Descrição |
|-------|-----------|
| `total` | Total de operações WIN/LOSS |
| `wins` | Vitórias |
| `losses` | Derrotas |
| `win_rate` | `(wins / total) × 100`, arredondado 2 casas |
| `profit` | Lucro/prejuízo líquido somado |

Na UI: cards **Win Rate El Capo**, Wins, Loss, Operações e Lucro das operações.

## Resultados por horário

Array `hourly_results` com **24 linhas fixas** (00:00–23:00, horário civil de
Brasília).

Cada operação real usa `finished_at` (fallback: `opened_at`, `created_at`,
`sent_at`) convertido para `America/Sao_Paulo`.

| Campo | Descrição |
|-------|-----------|
| `hour` | 0–23 |
| `hour_label` | Ex.: `"09:00"` |
| `operations` | Trades na faixa horária no período |
| `wins` / `losses` | Contagens |
| `win_rate` | Taxa na hora (0 se sem ops) |
| `profit` | Lucro líquido na hora |

A tabela admin inclui barra de **Volume** relativa ao pico de operações do
período (classes CSS `admin-hour-bar` em `styles.css`).

Horas sem operações aparecem esmaecidas com valores `-`.

## Rankings de clientes

`calculate_admin_dashboard` soma o `profit` de operações **reais** concluídas
(`WIN`/`LOSS`). Por padrão em produção exclui contas **marketing** (env
`ADMIN_DASHBOARD_EXCLUDE_MARKETING=true`). Ignora `is_simulated` e
`source=marketing_demo`, e deduplica por `order_id`.

| Lista | Campo | Limite | Critério |
|-------|--------|--------|----------|
| `top_winners` | lucro **positivo** | **10** (`USER_RANKING_LIMIT`) | maior lucro, desempate por nome |
| `top_losers` | lucro **negativo** | **10** (`USER_RANKING_LIMIT`) | menor lucro (mais negativo), desempate por nome |

Se houver menos de 10 clientes no critério, a lista vem menor. Lucro zero
não entra em nenhum dos dois.

Na UI os títulos são **Top 10 ganhadores** e **Top 10 perdedores**. A lista
tem scroll interno (`max-h-[32rem]`) para não esticar o grid.

## Rankings de ativos

| Lista | Limite | Critério |
|-------|--------|----------|
| `most_accurate_assets` | 5 (`ASSET_RANKING_LIMIT`) | maior % WIN, depois mais operações |
| `least_accurate_assets` | 5 | menor % WIN, depois mais operações |

## Cache

Alterar campos do payload (`operations`, `hourly_results`) invalida o cache
implicitamente no próximo recompute (TTL 75 s + warmer 7d/30d). Não é preciso
limpar à mão.

## Testes

```bash
cd /opt/elcapo2/backend
.venv/bin/python -m unittest tests.test_admin_management.AdminDashboardServiceTests -v
```

- `test_dashboard_aggregates_clients_profit_users_and_assets` — KPIs, win rate,
  lucro e faixas horárias (Brasília).
- `test_dashboard_user_rankings_return_top_10` — 12 ganhadores e 12 perdedores
  → exatamente 10 em cada lista.
- `test_dashboard_excludes_marketing_accounts` — contas marketing fora de win rate,
  horários e rankings de lucro.

## Deploy

Backend (gateway) + frontend estático. Ver [`DEPLOY_VPS.md`](./DEPLOY_VPS.md).

```bash
/opt/elcapo2/scripts/deploy-backend.sh
/opt/elcapo2/scripts/publish-frontend.sh
```
