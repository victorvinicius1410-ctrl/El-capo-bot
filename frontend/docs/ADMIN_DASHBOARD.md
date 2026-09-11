# Dashboard administrativo

Painel em `/admin/dashboard`. Rankings de clientes: **top 10 ganhadores** e
**top 10 perdedores** (não mais top 5). Ativos continuam no top 5.

Detalhes de agregação, limites (`USER_RANKING_LIMIT = 10`) e testes:
[`../../docs/ADMIN_DASHBOARD.md`](../../docs/ADMIN_DASHBOARD.md) (espelho em
`/root/docs/ADMIN_DASHBOARD.md`).

## UI

Arquivo: `src/routes/_authenticated/admin.dashboard.tsx`

- Títulos: `Top 10 ganhadores` / `Top 10 perdedores`
- Dados: `dashboard.data.top_winners` e `top_losers` do `GET /admin/dashboard`
- Lista com scroll (`max-h-[32rem]`) para caber 10 linhas no card
