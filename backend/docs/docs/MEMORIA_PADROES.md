# Memória de padrões — El Capo

Documento criado em **2026-07-31**. Atualizado em **2026-08-04** (anti-loss
estrutural complementar). Complementa a estratégia clássica com um portão
estatístico que aprende quais contextos geram mais WIN ou LOSS.

Desde 2026-08-04, alguns padrões tóxicos (ex.: CONTINUATION+PUT em EURUSD/
AUDUSD/USDCAD/USDCHF, CONTINUATION contra as 3 velas) também são **hard blocks
estruturais** em `signal_engine` / `CRITICAL_TRADE_BLOCKS` — independentes da
amostra da memória. Ver [`ESTRATEGIA.md`](./ESTRATEGIA.md) §"Política anti-loss".

## 1. Objetivo

Depois que a estratégia clássica aprova um setup, a memória consulta o
**caderno global do sistema** (todas as contas) no mesmo contexto:

```
ativo × hora UTC × setup × direção × timeframe
```

Setup vem de `strategy_setup` / `price_action_setup` / `strategy_setups` /
`metrics.price_action_setup` (histórico clássico guarda o setup em
`analysis_json.metrics`).

- Se o contexto tem histórico **fraco** (muitas amostras e win rate baixo) →
  **não opera** (`PATTERN_MEMORY_WEAK`).
- Se a amostra é pequena ou o padrão é saudável → segue o fluxo normal
  (**fail-open**).

A memória **não** substitui EMA/RSI/price action — só veta padrões que já
provaram ser ruins no histórico agregado.

### Caderno global (padrão desde 2026-08-03)

| Caderno | Papel |
|---|---|
| **Global** (`robot_pattern_memory_global`) | Portão de entrada + aprendizado compartilhado |
| **Pessoal** (`robot_pattern_memory` por `user_id`) | Arquivo/auditoria; alimenta a unificação |

Na ativação (e via SQL), todos os cadernos pessoais são **somados** num único
caderno global — o histórico não se perde. Cada nova operação atualiza os dois
(global + arquivo da conta).

## 2. Regras

| Parâmetro | Env | Padrão | Papel |
|---|---|---|---|
| Liga/desliga | `PATTERN_MEMORY_ENABLED` | `true` | Feature flag |
| Caderno global | `PATTERN_MEMORY_GLOBAL` | `true` | `true` = todas as contas no mesmo caderno |
| Amostra mínima | `PATTERN_MEMORY_MIN_SAMPLES` | `12` | Só bloqueia com ≥ N ops no bucket |
| WR máximo fraco | `PATTERN_MEMORY_MAX_WEAK_WIN_RATE` | `52.0` | Bloqueia se WR &lt; 52% (abaixo do empate ~53% com payout 88%) |
| Janela | `PATTERN_MEMORY_LOOKBACK_DAYS` | `90` | Hidratação a partir do histórico |

Outras regras:

- **Gale ignorado** — só a entrada original entra na memória.
- **Escrita pessoal** — `user_id` sempre da sessão (nunca do body); o arquivo
  pessoal continua isolado por conta.
- **Leitura do portão** — com `PATTERN_MEMORY_GLOBAL=true`, consulta o agregado
  de **todas** as contas.
- **DRAW** não conta (só WIN/LOSS).
- Sem `user_id` no portão (testes legados) → memória não interfere.

## 3. Arquivos

| Arquivo | Papel |
|---|---|
| `backend/pattern_memory.py` | Service + stores (Supabase/SQLite/RAM) + unificação |
| `backend/main.py` | Portão `candidate_meets_cycle_threshold`, gravação em `finish_monitored_trade`, `GET /robot/pattern-memory` |
| `backend/migration_pattern_memory.sql` | Tabela pessoal + RLS + backfill (v3) |
| `backend/migration_pattern_memory_global.sql` | Tabela global + unificação dos cadernos (v4) |
| `tests/test_pattern_memory.py` | Unit + integração do portão + caderno global |
| `docs/MEMORIA_PADROES.md` | Este documento |

## 4. Fluxo

```
analyze_signal (estratégia clássica)
        │
        ▼
candidate_meets_cycle_threshold
        │
        ├─ trade_allowed / CRITICAL_TRADE_BLOCKS / payout / confidence
        │
        └─ pattern_memory.apply_to_candidate(user_id, candidate)
                 │
                 ├─ (global) consulta robot_pattern_memory_global / RAM
                 ├─ samples < min → ALLOW (INSUFFICIENT_SAMPLE)
                 ├─ win_rate < 52% → BLOCK (PATTERN_MEMORY_WEAK)
                 └─ senão → ALLOW (PATTERN_OK)

finish_monitored_trade (WIN/LOSS, sem gale)
        │
        └─ pattern_memory.record_outcome
                 ├─ arquivo pessoal (user_id)
                 └─ caderno global (soma do sistema)

startup / SQL v4
        │
        └─ unify_all_into_global → SUM(cadernos pessoais) → global
```

## 5. SQL

### 5.1 Pessoal (v3 — se ainda não rodou)

> **Atenção:** `public.users.id` é `text`, não `uuid`. Use
> `/root/migration_pattern_memory.sql`.

Arquivo: [`migration_pattern_memory.sql`](../Backend/backend/migration_pattern_memory.sql)

### 5.2 Global + unificação (v4 — obrigatório para o caderno único)

Arquivo: [`migration_pattern_memory_global.sql`](../Backend/backend/migration_pattern_memory_global.sql)
(cópias em `/root/migration_pattern_memory_global.sql` e `docs/`).

1. Abra o **SQL Editor** do Supabase do projeto da VPS
   (`https://dfxmasxpqpujxahwzpqn.supabase.co`).
2. Cole e execute `migration_pattern_memory_global.sql`.
3. Reinicie o `backend-gateway` (`/opt/elcapo/scripts/deploy-backend.sh`).

O gateway também tenta unificar no startup (`unify_all_into_global`). Sem a
tabela global o robô **continua operando**: usa fallback
`robot_pattern_memory` com `user_id='__global__'` (usuário sintético) e/ou
agrega em RAM a partir dos cadernos pessoais.

### Tabela `robot_pattern_memory_global`

| Coluna | Tipo | Descrição |
|---|---|---|
| `pattern_key` | text PK | Chave canônica |
| `active` / `direction` / `setup` / `timeframe` / `hour_utc` | — | Dimensões |
| `wins` / `losses` / `profit` | — | Agregados de **todas** as contas |

### Tabela `robot_pattern_memory` (arquivo pessoal)

Mantida como está (unique `(user_id, pattern_key)`). Não é apagada na
unificação.

## 6. API

`GET /robot/pattern-memory` (sessão autenticada):

```json
{
  "ok": true,
  "data": {
    "enabled": true,
    "scope": "global",
    "min_samples_to_block": 12,
    "max_weak_win_rate": 52.0,
    "patterns": [
      {
        "pattern_key": "USDCAD-OTC|17|CONTINUATION|CALL|M1",
        "wins": 10,
        "losses": 2,
        "samples": 12,
        "win_rate": 83.33,
        "profit": 1200.0
      }
    ],
    "weak_patterns": [],
    "total_patterns": 1,
    "weak_count": 0
  }
}
```

Com `scope: "global"` a lista é o caderno unificado do sistema.

## 7. Logs

| Tag | Quando |
|---|---|
| `[PATTERN_MEMORY_RECORDED]` | Resultado gravado (inclui `scope=global\|personal`) |
| `[PATTERN_MEMORY_BLOCKED]` | Entrada vetada |
| `[PATTERN_MEMORY_REBUILT]` | Hidratação a partir do histórico |
| `[PATTERN_MEMORY_GLOBAL_UNIFIED]` | Unificação dos cadernos pessoais |
| `[PATTERN_MEMORY_GLOBAL_LOADED]` | Caderno global carregado do Supabase |
| `[PATTERN_MEMORY_GLOBAL_TABLE_MISSING]` | Falta rodar a migration v4 |
| `[PATTERN_MEMORY_LOAD_FAILED]` | Tabela pessoal ausente / erro PostgREST |
| `[PATTERN_MEMORY_PERSIST_UPSERT_FAILED]` | Upsert pessoal falhou (RAM segue válida) |
| `[PATTERN_MEMORY_GLOBAL_UPSERT_FAILED]` | Upsert global falhou (RAM segue válida) |

## 8. Testes

```bash
cd /root/Backend && .venv/bin/python -m pytest tests/test_pattern_memory.py tests/test_entry_quality_gate.py -q
```

Cobertura global: unificação de linhas, aprendizado compartilhado entre
contas, merge SQL/store e arquivo pessoal paralelo.

## 9. Desligar / voltar ao caderno por conta

```bash
# desliga a memória por completo
PATTERN_MEMORY_ENABLED=false

# ou volta ao modo antigo (só o histórico da própria conta no portão)
PATTERN_MEMORY_GLOBAL=false
```

Reinicie o gateway. O portão clássico permanece intacto.
