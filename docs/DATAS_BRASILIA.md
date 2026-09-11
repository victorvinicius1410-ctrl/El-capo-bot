# Datas civis em Brasília (meia-noite a meia-noite)

Todas as janelas de **dia**, **hoje**, **histórico**, **placar diário**,
**stop win/loss** e **KPIs** do El Capo usam o fuso
`America/Sao_Paulo` (Brasília, UTC−3). O Brasil não tem horário de verão
desde 2019; o offset é fixo.

Atualizado em **2026-08-18**.

## Por que existe

O servidor grava timestamps em **UTC**. Sem conversão, o dia vira à
meia-noite UTC = **21:00 em Brasília**.

Consequência até este ajuste:

| Horário (Brasília) | Dia UTC | O que o sistema fazia |
|---|---|---|
| 00:00–20:59 | mesmo dia | “Hoje” coincidia com o calendário brasileiro |
| 21:00–23:59 | **dia seguinte** | Placar, stop e filtro “Hoje” zeravam cedo demais. Operações das 21h–23h59 iam para o dia UTC seguinte |

O usuário vê e opera em horário de Brasília. O dia civil fecha **00:00 → 00:00
de Brasília**, não de UTC.

Moeda das telas de operação/histórico continua **BRL** (real); este
documento cobre só o recorte de datas.

## Regra

1. Persistência continua em UTC (ISO-8601).
2. “Hoje” = `00:00:00` até `23:59:59.999` em `America/Sao_Paulo`.
3. `days=N` na API = últimos **N dias civis inclusivos** em Brasília,
   começando na meia-noite do primeiro dia (não uma janela rolante de N×24h).
4. Exibição no painel (`pt-BR`) sempre com `timeZone: America/Sao_Paulo`.

Exemplos com `days=1` (“Hoje”) às **22:30 BRT de 18/08**:

- Início: `2026-08-18T00:00:00-03:00` = `2026-08-18T03:00:00Z`
- Fim: agora (ainda 18/08 em Brasília)
- Uma operação às 22:10 BRT (`01:10 UTC do dia 19`) **entra** no dia 18.

`days=7`: meia-noite de 6 dias atrás até agora (7 dias civis incluindo hoje).

## Onde vale

| Superfície | Comportamento |
|---|---|
| Filtro Meta Ads (dashboard, histórico, admin) | Presets e calendário em dias civis de Brasília |
| Tabela diária do dashboard | Agrupa por `YYYY-MM-DD` de Brasília |
| `GET /robot/history?days=` e `GET /robot/stats?days=` | Cutoff = meia-noite Brasília do 1º dia |
| Placar / `management_totals` / stop win-loss do dia | Só operações cujo `finished_at` cai no dia civil atual de Brasília |
| Admin dashboard e financeiro | Período trailing até hoje, meia-noite Brasília |
| Gráficos diários do financeiro | Bucket por data civil de Brasília |
| Shift+O (`datetime-local`) | Relógio e ISO interpretados como Brasília |

**Não muda:** sessão forex (domingo 22:00 UTC → sexta 22:00 UTC — regra da
corretora, ver [`MERCADO_ABERTO.md`](./MERCADO_ABERTO.md)); TTL de cache;
expiração de trial/access_days (duração em horas corridas).

## Código

Backend — `backend/brasilia_time.py`

- `BRASILIA_TZ` / `brasilia_today()` / `to_brasilia_date()`
- `start_of_brasilia_day()` / `end_of_brasilia_day()`
- `is_brasilia_today()` / `is_on_brasilia_day()`
- `history_cutoff(days)` / `history_cutoff_iso(days)`

Frontend — `src/lib/brasiliaTime.ts`

- `BRASILIA_TIMEZONE`
- `brasiliaDateParts` / `fromBrasiliaDate` / `brasiliaDateKey`
- `formatBrasiliaDate` / `formatBrasiliaDateTime`

Consumidores: `dateRange.ts`, `dashboardDailyStats.ts`,
`DashboardDateFilter.tsx`, `robot_persistence.py`, `auto_trader.py`,
`main.py` (`build_management_summary`, merge do histórico),
`admin_dashboard_service.py`, `admin_router.py`, `finance_service.py`.

## Testes

```bash
cd /opt/elcapo/backend
PYTHONPATH=. python3 -m unittest tests.test_brasilia_time -v

cd /opt/elcapo/frontend
node --experimental-strip-types --test \
  src/lib/brasiliaTime.test.ts \
  src/lib/dateRange.test.ts \
  src/lib/marketingDemoSettings.test.ts
```

Caso-chave: instante `2026-08-19T01:30:00Z` (22:30 de 18/08 em Brasília)
ainda é o dia **18**.

## Histórico

- **2026-08-18** — Dia civil passa a ser meia-noite a meia-noite de Brasília
  em filtros, histórico, placar diário, stop e KPIs admin. Antes o recorte
  usava UTC (`datetime.now(timezone.utc).date()` e `startOfDay` no fuso
  local/SSR).
