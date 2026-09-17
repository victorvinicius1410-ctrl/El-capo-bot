# Valor de entrada por moeda do saldo

Atualizado em **2026-09-15**.

## Objetivo

O valor das operações do robô usa a **mesma moeda do saldo** da conta
conectada na BullEx. Não há conversão BRL↔USD: se o saldo está em dólar,
entrada, gale e checagens de mínimo ficam em dólar.

## Limites

Há **somente mínimo**. Não existe teto de valor de entrada.

| Moeda do saldo | Mínimo | Máximo | Default (conta nova) |
|---|---|---|---|
| **BRL** | R$ 5,00 | sem limite | R$ 5,00 |
| **USD** | US$ 1,00 | sem limite | US$ 1,00 |

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

## Bug corrigido em 2026-09-15 (conta em real oferecia mínimo R$ 1)

Sintoma: o painel anunciava **"Mínimo R$ 1,00"** numa conta em real. Ao
salvar, o backend devolvia `ENTRY_VALUE_TOO_LOW`.

Causa: o conserto de 17/08 baixou o piso para 1 sempre que a moeda era
desconhecida — e **desconhecida é o estado normal fora da conexão ativa**.
O `/account` manda `currency: null` em toda resposta desconectada
(`main.py`, `currency=None if not connected`), e também na janela entre
abrir o painel e o primeiro poll responder. Pior: `entryValueHelperText`
formatava esse piso com `formatBullExBalance`, que sem moeda cai no
default BRL — daí o "R$ 1,00", que não é mínimo de nenhuma das duas
moedas.

O backend nunca teve esse furo: `resolve_user_account_currency` lê a
moeda persistida/memorizada e cobra R$ 5 da conta BRL mesmo desconectada.
Os dois lados divergiam exatamente no caso "sem snapshot": frontend
assumia 1, backend assumia 5.

Correção:

- **Memória da moeda no painel** (`lib/accountCurrencyMemo.ts`, aplicada
  em `useBullExAccountQuery`): moeda conhecida é gravada por usuário; o
  snapshot sem moeda passa a ser preenchido com a última conhecida.
  Snapshot ao vivo sempre vence a memória, então trocar de conta troca a
  moeda. É o espelho do que o backend já fazia.
- **Piso × mínimo separados**: `ENTRY_VALUE_ABSOLUTE_MIN` (= 1) é só piso
  de armazenamento do store global, que não conhece a conta.
  `EntryValueLimits.currencyKnown` diz se há moeda de verdade.
- **A interface não inventa mínimo**: sem moeda conhecida o texto vira
  "Mínimo conforme a moeda da conta conectada" — nenhum número, nenhum
  símbolo.
- **`normalizeRobotSettings` / `pickPresentRobotSettings` aceitam moeda**:
  com BRL, 1 volta a subir para 5; sem moeda, segue o piso 1 (é o que
  impede o bug de 17/08 de voltar).

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
- `ENTRY_VALUE_ABSOLUTE_MIN` é piso de armazenamento, **não** é mínimo de
  conta nenhuma — nunca exibir esse número para o usuário
- `frontend/src/lib/accountCurrencyMemo.ts` guarda a última moeda
  conhecida por usuário; `useBullExAccountQuery` preenche o snapshot
  desconectado com ela
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
- `frontend/src/lib/accountCurrencyMemo.test.ts`

## Histórico

- **2026-09-15** — Conta em real oferecia mínimo R$ 1 porque a moeda some
  do `/account` quando a conta não está conectada. Painel passa a lembrar
  a moeda, e sem moeda não anuncia mínimo nenhum.
- **2026-08-17** — Lead com saldo em USD digitava 1 e a operação ia com 5.
  Clamp do painel e mínimo fixo do backend ignoravam a moeda. US$ 1 volta
  a ser aceito e persistido.
- **2026-08-16** — Removido o teto. Só mínimo: R$ 5 (BRL) e US$ 1 (USD).
- **2026-08-15** — Operações na moeda do saldo. (Teto R$ 5 / US$ 1, revertido
  em 16/08 porque o pedido era só o mínimo.)
