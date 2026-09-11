# Bloqueio WEAK PUT — corte de volume ruim

Documento criado em **2026-08-14**. Código já está no repositório de trabalho
(`/root/Backend`); **produção ainda não recebe deploy** até autorização
explícita.

Complementa [`ESTRATEGIA.md`](./ESTRATEGIA.md) (pipeline clássico +
substitution 2026-08-13).

## 1. Objetivo

Parar de operar **WEAK + PUT** (venda em setup fraco, em geral liberada pelo
frequency recovery para “preencher o ciclo”). **WEAK + CALL** continua como
fallback de frequência.

Não é cota aleatória de operações: é filtro de qualidade. CONTINUATION PUT
não é este bloqueio (continua a regra antiga `WEAK_CONTINUATION_PUT` só nos
pares tóxicos).

## 2. Evidência (677 ops pós-atualização 13/08 17:34 BRT)

| Recorte | Ops | WIN x LOSS | WR |
|---|---|---|---|
| Placar real | 677 | 348 x 329 | 51,4% |
| WEAK PUT | 265 (39%) | 113 x 152 | **42,6%** |
| WEAK CALL | 194 (29%) | 111 x 83 | **57,2%** |
| Sem WEAK PUT (hipótese) | 412 | 235 x 177 | **57,0%** |
| Sem WEAK PUT e sem WEAK CALL | 218 | 124 x 94 | 56,9% |

Antes da substitution (mesma duração ~23h, 730 ops): WEAK PUT já era ruim
(212 ops, 92 x 120, **43,4%**). A atualização de 13/08 melhorou CONTINUATION
e WEAK CALL; **não tirou** o WEAK PUT.

Empate com payout ~86% ≈ **53,8%**. WEAK PUT fica abaixo; WEAK CALL fica
acima.

Todos os ativos do recorte eram **OTC** (mercado aberto = 0 ops).

## 3. Comportamento

| Contexto | Antes (produção atual) | Código interno 14/08 |
|---|---|---|
| WEAK CALL no recovery | Pode operar (fallback) | **Pode operar** |
| WEAK PUT no recovery | Pode operar (fallback) | **Não opera** (`WEAK_PUT`) |
| CONTINUATION PUT pares tóxicos | Já bloqueado | Igual (`WEAK_CONTINUATION_PUT`) |
| CONTINUATION PUT GBPUSD etc. | Permitido se passar filtros | Igual |

`FREQUENCY_RECOVERY` **não** coloca `WEAK_PUT` em `FREQUENCY_RECOVERY_SOFT_BLOCKS`.
O portão `candidate_meets_cycle_threshold` rejeita WEAK PUT mesmo se o
candidato vier com `trade_allowed=True`.

## 4. Arquivos

| Arquivo | Papel |
|---|---|
| `backend/signal_engine.py` | Flag `WEAK_PUT_HARD_BLOCK`, helper `is_weak_put_setup`, filtro em `_apply_quality_filters` |
| `backend/main.py` | `CRITICAL_TRADE_BLOCKS`, `RECOVERY_NON_RELAXABLE_TRADE_BLOCKS`, portão do ciclo, `classify_no_opportunity_reason` |
| `tests/test_accuracy_ranking_substitution.py` | `WeakPutHardBlockTests` |
| `tests/test_loss_pattern_blocks.py` | Garante que recovery não afrouxa `WEAK_PUT` |

## 5. Flag

```python
WEAK_PUT_HARD_BLOCK = False  # signal_engine.py — off 2026-08-16
```

Desligar (`False`) volta a permitir WEAK PUT no recovery (comportamento
13–14/08). **Estado atual em produção: False**, porque os cortes de 15/08
pioraram o WR (32–45% vs ~51%).

## 6. Deploy

**No ar até 15/08; desligado 16/08** (volta substitution). Snapshot:

`/root/backups/2026-08-14_2039-BRT/`

Quando autorizar: `/opt/elcapo/scripts/deploy-backend.sh` (ou o fluxo
documentado em [`DEPLOY_VPS.md`](./DEPLOY_VPS.md)).

## 7. O que não entra nesta rodada

- Horários UTC/BRT (pedido do cliente: não cortar ainda)
- Corte de WEAK CALL com vela forte
- Mercado aberto (não havia amostra)
- Mudança de confiança mínima / perfil

Desde **2026-08-15** vela fraca e DOJI também são hard block. Ver
[`CORTES_QUALIDADE.md`](./CORTES_QUALIDADE.md).

