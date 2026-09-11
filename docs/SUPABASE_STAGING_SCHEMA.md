# Supabase Staging — como duplicar o schema do El Capo

O Supabase **não tem botão “duplicar projeto”** com schema completo para
outro projeto. O caminho oficial do El Capo é rodar o SQL de bootstrap
(+ migrations posteriores) no **SQL Editor** do projeto novo.

Isso cria **estrutura** (tabelas, RLS, funções, seeds). **Não copia** usuários
nem histórico de produção — e é exatamente o que queremos no staging.

## Arquivo principal (use este)

| Ordem | Arquivo | O que faz |
|------:|---------|-----------|
| **1** | [`sql/STAGING_SCHEMA_ALL_IN_ONE.sql`](./sql/STAGING_SCHEMA_ALL_IN_ONE.sql) | Bootstrap completo + memória de padrões + índice de leads + estratégias nomeadas |

Caminho absoluto na VPS:

```text
/opt/elcapo2/docs/sql/STAGING_SCHEMA_ALL_IN_ONE.sql
```

(também existe a cópia canônica de produção em
`/opt/elcapo/backend/backend/el_capo_full_bootstrap.sql` — o all-in-one já a inclui.)

## Passo a passo no Supabase staging

1. Crie o projeto em [supabase.com](https://supabase.com) (ex.: `elcapo-staging`).
2. Abra **SQL Editor** → **New query**.
3. Cole o conteúdo de `STAGING_SCHEMA_ALL_IN_ONE.sql` (ou faça upload / copie da VPS):

```bash
# Na VPS — copiar para a área de transferência local via scp, ou:
wc -l /opt/elcapo2/docs/sql/STAGING_SCHEMA_ALL_IN_ONE.sql
```

4. Clique **Run** (pode demorar alguns segundos).
5. Se der erro no meio, **não** misture com o banco de produção. Em projeto
   novo, o script é idempotente (`if not exists` / `create or replace`) — pode
   rodar de novo após corrigir o trecho que falhou.
6. Crie o admin de teste (SQL Editor):

```sql
select public.bootstrap_admin_user(
  'admin@elcapo2.shop',
  'SenhaForte123',
  'Admin Staging',
  true
);
```

   Senha: mínimo 8 caracteres, com maiúscula, minúscula e número.

7. Em **Project Settings → API**, copie:
   - Project URL → `SUPABASE_URL`
   - `service_role` (secret) → `SUPABASE_SERVICE_ROLE_KEY`
8. Cole em `/opt/elcapo2/backend/.env` e rode:

```bash
/opt/elcapo2/scripts/deploy-backend.sh
```

## Se preferir rodar em partes (mesma ordem)

| # | Arquivo em `/opt/elcapo2/docs/sql/` | Origem |
|---|-------------------------------------|--------|
| 01 | `01_el_capo_full_bootstrap.sql` | Schema base (robô, multi-tenant, billing, webhooks, emails, RLS, seeds) |
| 02 | `02_pending_leads_partial_index.sql` | Índice da fila Pedidos (`approval_status=pending`) |
| 03 | `03_migration_pattern_memory.sql` | Tabela `robot_pattern_memory` |
| 04 | `04_migration_pattern_memory_global.sql` | Tabela `robot_pattern_memory_global` |
| 05 | `05_migration_named_strategies_analysis.sql` | Colunas `strategy_key` / resumo no histórico |

Já **incluídos no bootstrap** (não precisa rodar de novo):

- Credenciais Bullex salvas (`encrypted_password`)
- `broker_order_id` no espelho marketing
- `approval_status` em `user_access_profiles`
- Tabelas `email_templates` / `email_deliveries`

**Não rode** `migration_real_only_robot.sql` em staging vazio — só atualiza
linhas existentes para modo REAL (útil em banco antigo, não em projeto novo).

## O que NÃO fazer

- Não exporte/importe o dump de **produção** para staging (traz dados reais
  de clientes — viola isolamento LEI 13).
- Não use a `SUPABASE_SERVICE_ROLE_KEY` de produção no `.env` do staging.
- Não rode o all-in-one no projeto de produção (já está aplicado lá).

## Clonar admin + marketing (sem clientes)

Script: `/opt/elcapo2/scripts/clone_admin_marketing_users.py`

```bash
python3 /opt/elcapo2/scripts/clone_admin_marketing_users.py
```

- Copia **só** `is_admin=true` e `account_type=marketing` do El Capo 01 → staging.
- Mantém o **mesmo UUID**, e-mail, perfil e role admin.
- **Senha original:** não recupera. Bcrypt é one-way; a Admin API **não** expõe
  `auth.users.encrypted_password`. Senha temporária atual das contas clonadas:
  `Teste123` (também em `/opt/elcapo2/.staging-cloned-users-password`).

### Se quiser a senha original de fato

Precisa da **Database password** do projeto de produção (Supabase →
Settings → Database) para ler o hash e gravar no staging via SQL. Aí o login
continua com a senha antiga **sem** descriptografar (só copia o hash bcrypt).


```sql
select table_name
from information_schema.tables
where table_schema = 'public'
  and table_name in (
    'users', 'robot_states', 'robot_settings', 'robot_trade_history',
    'companies', 'user_access_profiles', 'billing_plans',
    'email_templates', 'robot_pattern_memory', 'robot_pattern_memory_global'
  )
order by 1;
```

Esperado: as 10 tabelas listadas.

Health do gateway staging (depois de preencher o `.env`):

```bash
curl -sS http://127.0.0.1:8081/health
```

## Relação com o ambiente

Detalhes do stack `elcapo2.shop`: [`STAGING_ELCAPO2.md`](./STAGING_ELCAPO2.md).
Auth / admin: [`AUTENTICACAO.md`](./AUTENTICACAO.md) (RPC `bootstrap_admin_user`).
