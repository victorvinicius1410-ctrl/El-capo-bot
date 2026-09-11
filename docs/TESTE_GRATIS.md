# Teste grátis — duração, expiração e painéis

Documentação do fluxo de **teste grátis** (trial): como o admin define dias,
como o backend persiste `expires_at` e como admin/usuário veem o tempo restante.

Atualizado em **2026-08-27**.

## Resumo

| Camada | Campo / regra |
|--------|----------------|
| Banco | `user_access_profiles.expires_at` (ISO UTC) — **não** existe coluna `trial_days` |
| Admin cria/edita | `trial_days` no body → backend calcula `expires_at = now + N dias` |
| Admin aprova lead | `access_days` no body → mesmo cálculo + `plan_name = "Acesso liberado (N dias)"` |
| Usuário logado | `GET /me/access` → `expires_at` + `account_type=trial` |

**Importante:** duração de trial **não** tem relação com filtros de relatório
“Últimos 30 dias” do dashboard admin (ver [`FILTRO_DATAS.md`](./FILTRO_DATAS.md)).

## Bug corrigido: 3 dias aparecendo como 4 (2026-08-27)

### Sintoma

Admin libera **3 dias** de teste; no painel (**Dias restantes** / campo ao editar)
aparecia **4**.

### Causa

`remainingDaysFromExpiresAt` usava ``Math.ceil(ms / dia)``. Com skew de
relógio (browser alguns segundos atrás do servidor) o restante ficava
ligeiramente **acima** de 3,0 dias (`3d + 5s`) e o `ceil` virava **4**.

### Correção

Arredondamento com ``Math.round`` (inteiro mais próximo). Trial recém-criado
de 3 dias continua mostrando **3**; o countdown do banner (`formatTrialRemaining`)
já usava `Math.floor` nos dias e não era afetado por esse off-by-one.

## Fluxos administrativos

### Aprovar pedido (lead)

1. Admin → **Acessos → Pedidos** → **Aprovar acesso**
2. Informa **Dias de acesso** (1–365)
3. `POST /admin/clients/{id}/approve` com `{ "access_days": N }`
4. Backend:
   - `account_type = trial`
   - `expires_at = now + N dias`
   - `plan_name = "Acesso liberado (N dias)"`

### Criar cliente trial

1. Admin → **Acessos → Teste grátis** (ou qualquer aba) → **Novo cliente**
2. Tipo **Teste grátis** + campo **Tempo de teste grátis (dias)**
3. `POST /admin/clients` com `{ "trial_days": N, "account_type": "trial", ... }`
4. Backend:
   - `expires_at = now + N dias`
   - `plan_name = "Teste grátis (N dias)"`

### Editar cliente trial

1. Ao abrir **Editar**, o formulário **hidrata** o campo de dias a partir de
   `expires_at` (dias restantes arredondados para cima), não um default fixo.
2. `PATCH /admin/clients/{id}` envia `trial_days` **somente se o admin alterou**
   o campo — evita resetar a expiração ao salvar nome/e-mail sem querer.
3. Quando `trial_days` é enviado, `expires_at` é recalculado a partir de **agora**.

## O que cada painel exibe

### Admin → aba **Teste grátis**

Cada card mostra, além dos dados do cliente:

- **Dias restantes** — calculado de `expires_at` com `Math.round` (evita 3→4 por skew)
- **Expira em** — data/hora em Brasília
- **Plano** — ex.: `Teste grátis (3 dias)` ou `Acesso liberado (7 dias)`

### Usuário → banner “Período grátis ativo”

- Fonte: `expires_at` de `/me/access`
- Formato: **`N dias, HH:MM:SS`** (ex.: `3 dias, 05:12:00`)
- Antes o banner mostrava só horas totais (`72:00:00` para 3 dias ou
  `720:00:00` para 30 dias), o que gerava confusão (“parece 30 dias”).

### Usuário → `/welcome-trial`

- Exibe dias reais derivados de `expires_at` (fallback local `TRIAL_DAYS=3` só
  se a API ainda não respondeu).

## API (contratos)

```http
POST /admin/clients
{ "trial_days": 3, "account_type": "trial", ... }

POST /admin/clients/{id}/approve
{ "access_days": 3 }

GET /admin/clients?segment=trial
→ items[].expires_at, plan_name, ...

GET /me/access
→ expires_at, account_type
```

Resposta de cliente (`_client_view`):

```json
{
  "expires_at": "2026-08-29T15:00:00+00:00",
  "plan_name": "Teste grátis (3 dias)",
  "account_type": "trial"
}
```

## Código

| Arquivo | Papel |
|---------|--------|
| `backend/backend/admin_service.py` | `_apply_access_configuration`, `approve_lead` |
| `backend/backend/admin_router.py` | REST `/admin/clients`, `_client_view` |
| `frontend/src/lib/trial.ts` | `remainingDaysFromExpiresAt`, `formatTrialRemaining` |
| `frontend/src/routes/_authenticated/admin.clientes.tsx` | Form admin + cards da aba trial |
| `frontend/src/components/AppShell.tsx` | `TrialBanner` |
| `frontend/src/routes/_authenticated/welcome-trial.tsx` | Boas-vindas com dias reais |

## Testes

Backend:

```bash
cd /opt/elcapo/backend
PYTHONPATH=backend python3 -m unittest tests.test_admin_management tests.test_lead_registration -v
```

Frontend:

```bash
cd /opt/elcapo/frontend
node --test src/lib/trial.test.ts
```

## Deploy

```bash
/opt/elcapo/scripts/deploy-backend.sh
cd /opt/elcapo/frontend && npm run build
/opt/elcapo/scripts/publish-frontend.sh
```

## Verificação manual

1. Aprovar lead com **3 dias** → aba **Teste grátis** deve mostrar ~3 dias restantes.
2. Editar o mesmo cliente **sem mudar dias** → expiração não deve pular para 7 ou 30.
3. Login como usuário trial → banner deve mostrar `3 dias, …` (não `720:00:00`).
4. `/welcome-trial` deve mencionar **3 dias**, não valor fixo incorreto.

## Segurança e multi-tenant

- `company_id` sempre da sessão admin (Lei 03).
- `trial_days` / `access_days` validados entre 1 e 365 no backend.
- Nenhuma duração de trial é inferida de planos pagos “Mensal” (30 dias de
  billing admin é só para `promote_user_to_admin`, não para trial de cliente).

## Documentos relacionados

- [`REGISTRO_APROVACAO.md`](./REGISTRO_APROVACAO.md) — fila de pedidos e approve
- [`DATAS_BRASILIA.md`](./DATAS_BRASILIA.md) — trial usa horas corridas UTC, não dia civil BRT
- [`FILTRO_DATAS.md`](./FILTRO_DATAS.md) — filtro “30 dias” ≠ duração de trial
