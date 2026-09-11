# Admin dashboard cache warmer

Background warmer que mantém o cache em memória do `GET /admin/dashboard`
quente para tenants que já visitaram o painel. Atualizado em **2026-08-07**.

## Objetivo

Eliminar latência de 4–13 s na 1ª carga “mornings” / revisita após idle curto
do dashboard admin. Com o warmer ativo, o hit típico fica **<1 s**.

## Componentes

| Peça | Arquivo | Papel |
|------|---------|--------|
| Cache TTL 75 s | `backend/admin_dashboard_cache.py` | Leitura/escrita/invalidação por `company_id:days` |
| Warmer 30 s | `backend/admin_dashboard_warm.py` | Loop async que recompute 7d e 30d |
| Compute compartilhado | `backend/admin_router.py` → `compute_admin_dashboard_payload` | Mesmo pipeline do endpoint HTTP |
| Lifecycle | `backend/main.py` | `start()` no startup (junto ao WS hub); `aclose()` no shutdown |
| Registro | `GET /admin/dashboard` | `dashboard_warmer.register_company(company_id)` em todo hit |

## Contrato `AdminDashboardWarmer`

```python
warmer = AdminDashboardWarmer(
    compute,                    # async (company_id, days) -> dict
    interval_seconds=30.0,
    days=(7, 30),
)

warmer.register_company(company_id)  # no-op se vazio
warmer.start()                       # idempotente; cria asyncio.Task
await warmer.warm_once()             # um ciclo (preferido em testes)
await warmer.aclose()                # cancela a task
```

Comportamento:

1. Só aquece empresas **registradas** (não varre todos os tenants do banco).
2. Em cada ciclo, para cada empresa × `{7, 30}`, chama `compute` e
   `write_admin_dashboard_cache`.
3. Falha em um par empresa/período é logada
   (`[ADMIN_DASHBOARD_WARM_FAILED]`) e **não** aborta o ciclo.
4. Relógio (`now_monotonic`) e `sleep` são injetáveis para testes sem sleep real.

## Endpoint `GET /admin/dashboard`

1. Extrai `company_id` da sessão admin (nunca do body).
2. `register_company(company_id)` — agenda warm futuro.
3. `read_admin_dashboard_cache` → hit retorna imediatamente.
4. Miss → log `[ADMIN_DASHBOARD_CACHE_MISS] company_id=… days=…`, compute,
   `write_admin_dashboard_cache`, registra de novo, responde.

Invalidação em mutações de cliente (`_invalidate_admin_list_caches`) **permanece**:
approve/CRUD limpa o cache do tenant; o warmer reescreve no próximo ciclo (~30 s)
ou o próximo GET recomputa.

## Isolamento multi-tenant (Lei 03)

- Chave de cache e warm sempre usam `company_id` da sessão / registro.
- `compute_admin_dashboard_payload` monta um `AdminActor` com o `company_id`
  alvo e consulta o repositório escopado por empresa.
- Não há parâmetro `company_id` aceito do frontend no warmer.

## Observabilidade

| Log | Quando |
|-----|--------|
| `[ADMIN_DASHBOARD_WARMER_STARTED]` | `start()` |
| `[ADMIN_DASHBOARD_WARMER_STOPPED]` | `aclose()` |
| `[ADMIN_DASHBOARD_CACHE_MISS]` | GET sem cache fresco |
| `[ADMIN_DASHBOARD_WARM_FAILED]` | Exceção em um compute do ciclo |
| `[ADMIN_DASHBOARD_WARM_LOOP_ERROR]` | Exceção inesperada no loop |

## Testes

```bash
cd /opt/elcapo/backend
PYTHONPATH=. /root/Backend/.venv/bin/python -m unittest \
  tests.test_admin_dashboard_warm \
  tests.test_admin_dashboard_cache -v
```

Cobertura do warmer:

- `register_company` (ignora blank)
- `warm_once` grava 7d e 30d no cache
- expire do TTL do cache após warm
- loop: `start` → warm → `sleep(30)` injetável → `aclose`
- falha de um tenant não bloqueia outro
