# Filtro de datas (estilo Meta Ads)

Componente único de período no layout do **Gerenciador de Anúncios** da Meta.

Atualizado em **2026-08-18**.

## Onde aparece

| Tela | Arquivo | `maxDays` | Padrão |
|------|---------|-----------|--------|
| Dashboard do cliente | `routes/_authenticated/dashboard.tsx` | 90 | Últimos 7 dias |
| Histórico | `routes/_authenticated/history.tsx` | 90 | Hoje |
| Admin dashboard | `routes/_authenticated/admin.dashboard.tsx` | 365 | Últimos 30 dias |
| Admin financeiro | `routes/_authenticated/admin.financeiro.tsx` | 365 | Últimos 30 dias |

O histórico **não** usa mais os três botões (Hoje / 7 dias / 30 dias).

Aprovação de cliente (`access_days`) e trial **não** são filtro de relatório —
continuam campos numéricos de validade.

## UI

Arquivo: `src/components/DashboardDateFilter.tsx`  
Estilos: `src/styles.css` (classes `.meta-date-*` e `.meta-cal-*`).

1. Botão com ícone de calendário e o intervalo em pt-BR
   (`15 de jul. de 2026 – 14 de ago. de 2026`).
2. Painel flutuante:
   - esquerda: presets com rádio (Hoje, Ontem, 7/14/28/30 dias, esta semana,
     semana passada, este mês, mês passado, este trimestre, máximo, personalizado);
   - direita: dois meses, intervalo pintado em azul Meta (`#0866ff`);
   - rodapé: horário de Brasília, **Cancelar** e **Atualizar**.
3. Só aplica ao clicar em **Atualizar** (como no Ads Manager).

## Lógica de período

`src/lib/dateRange.ts` + `src/lib/brasiliaTime.ts`

O dia civil é **meia-noite a meia-noite em Brasília** (`America/Sao_Paulo`),
não UTC. Detalhe em [`DATAS_BRASILIA.md`](./DATAS_BRASILIA.md).

- Semana começa na **segunda** (calendário de Brasília).
- Datas futuras (em Brasília) ficam desabilitadas.
- `maxDays` limita o início (90 no robô / 365 no admin).
- A API continua recebendo `?days=` **trailing até hoje em Brasília** a partir
  da data inicial (`trailingDaysUntilToday`). `days=1` começa na meia-noite
  de hoje; não é uma janela rolante de 24 horas.
- No dashboard e no histórico do cliente, a lista e os KPIs são **filtrados
  no cliente** pelo intervalo real (ex.: “Ontem” ou “Mês passado” não misturam
  o dia de hoje).
- No admin (dashboard e financeiro) os KPIs usam o `days` trailing até hoje,
  também com meia-noite de Brasília. Presets que terminam no passado (ontem,
  mês passado) incluem dias extras até hoje na agregação do servidor.
- No **admin dashboard** (`/admin/dashboard`), win rate e resultados por hora
  vêm do backend (`operations`, `hourly_results`) — ver
  [`ADMIN_DASHBOARD.md`](./ADMIN_DASHBOARD.md).

## Testes

```bash
cd /opt/elcapo/frontend
node --experimental-strip-types --test src/lib/dateRange.test.ts src/lib/brasiliaTime.test.ts
```

## Arquivos

- `src/components/DashboardDateFilter.tsx`
- `src/lib/dateRange.ts`
- `src/lib/dateRange.test.ts`
- `src/lib/brasiliaTime.ts`
- `src/lib/dashboardDailyStats.ts` (`filterHistoryByRange`, `computeRobotStatsFromItems`)
- [`DATAS_BRASILIA.md`](./DATAS_BRASILIA.md)
