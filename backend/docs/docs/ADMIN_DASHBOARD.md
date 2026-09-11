# Dashboard administrativo

`calculate_admin_dashboard` em `backend/admin_dashboard_service.py`.

## Limites

| Constante | Valor | Uso |
|-----------|-------|-----|
| `USER_RANKING_LIMIT` | 10 | `top_winners` e `top_losers` |
| `ASSET_RANKING_LIMIT` | 5 | ativos mais/menos assertivos |

Documento completo: [`../../docs/ADMIN_DASHBOARD.md`](../../docs/ADMIN_DASHBOARD.md).

## Testes

```bash
.venv/bin/python -m unittest tests.test_admin_management.AdminDashboardServiceTests -v
```
