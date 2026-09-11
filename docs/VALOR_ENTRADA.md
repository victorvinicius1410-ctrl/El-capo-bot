# Valor de entrada por moeda do saldo

Atualizado em **2026-08-17**.

## Objetivo

O valor das operações do robô usa a **mesma moeda do saldo** da conta
conectada na BullEx. Não há conversão BRL↔USD: se o saldo está em dólar,
entrada, gale e checagens de mínimo ficam em dólar.

## Limites

Há **somente mínimo**. Não existe teto de valor de entrada.

| Moeda do saldo | Mínimo | Máximo | Default (conta nova) |
|---|---|---|---|
| **BRL** | R$ 5,00 | sem limite | R$ 5,00 |
| **USD** | US$ 1,00 | sem limite | US$ 5,00 (pode baixar até US$ 1) |

Qualquer outro código de moeda conhecido é tratado como BRL.

`ROBOT_REAL_MAX_ENTRY` **não** limita mais a entrada.

## Bug corrigido em 2026-08-17 (lead: US$ 1 virava US$ 5)

Sintoma: a pessoa digitava **1** com saldo em dólar e a operação saía com
**5 dólares**.

Causas (as duas aconteciam juntas):

1. **Frontend** — `normalizeRobotSettings` / `pickPresentRobotSettings`
   usavam `ENTRY_VALUE_MIN = 5` para qualquer moeda. Ao confirmar o
   diálogo ou ao editar no overlay, `setRobotSettingsForUser` subia 1→5
   em silêncio. O campo "pulava" para 5 e a ordem ia com 5.
2. **Backend** — `POST /robot/config` e a partida da ordem comparavam com
   `MIN_REAL_ENTRY = 5` fixo, sem olhar a moeda da conta. US$ 1 era
   recusado (`ENTRY_VALUE_TOO_LOW`) ou nem chegava a ser persistido.

Correção:

- Piso absoluto no store do painel: **1** (USD). O mínimo de R$ 5 só vale
  quando a moeda **conhecida** é BRL.
- Moeda ainda não carregada **não** assume BRL no clamp (isso também
  subia US$ 1 para 5).
- Backend usa `min_real_entry_for_currency` na config, no ready-check e
  no envio da ordem (incluindo gale).

## Onde vale

- Diálogo **Iniciar operação**
- Painel **Configurações → Robô**
- Overlay (campo de entrada)
- `POST /robot/config` (rejeita só `ENTRY_VALUE_TOO_LOW`)
- Partida do robô (bloqueia só abaixo do mínimo da moeda)
- Envio da ordem real, inclusive gale (não pode ficar abaixo do mínimo)

A moeda **nunca** vem do body do frontend. O backend lê `currency` da
conta persistida (`get_user_account_snapshot` / sessão autenticada).

## Frontend

- `entryLimitsForCurrency`, `clampEntryValueForCurrency`,
  `entryValueHelperText` em `frontend/src/lib/robotSettings.ts`
- Campos `MoneyInput` usam `currency` da conta (`R$` ou `$`)
- Valor abaixo do mínimo **da moeda conhecida** sobe para esse mínimo;
  acima permanece
- `normalizeRobotSettings` **não** pode subir US$ 1 para 5

## Backend

- `normalize_account_currency`
- `min_real_entry_for_currency`
- `resolve_user_account_currency`
- Constantes: `MIN_REAL_ENTRY_BRL` = 5, `MIN_REAL_ENTRY_USD` = 1

## Testes

- `backend/tests/test_entry_currency_limits.py`
- `frontend/src/lib/robotSettings.test.ts`

## Histórico

- **2026-08-17** — Lead com saldo em USD digitava 1 e a operação ia com 5.
  Clamp do painel e mínimo fixo do backend ignoravam a moeda. US$ 1 volta
  a ser aceito e persistido.
- **2026-08-16** — Removido o teto. Só mínimo: R$ 5 (BRL) e US$ 1 (USD).
- **2026-08-15** — Operações na moeda do saldo. (Teto R$ 5 / US$ 1, revertido
  em 16/08 porque o pedido era só o mínimo.)
