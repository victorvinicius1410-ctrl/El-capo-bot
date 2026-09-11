# Auditoria de WR — 15/08/2026 (pós-deploy dos cortes)

Documento criado em **2026-08-15 ~13:10 BRT**. Só análise. Sem mudança de
código nesta rodada.

## 1. Placar

| Janela | Ops (sem gale) | WIN×LOSS | WR | Únicas |
|---|---|---|---|---|
| 13/08 17:34 → 15/08 03:00 BRT (antes do deploy da manhã) | 905 | 453×452 | **50,1%** | 49,0% |
| **15/08 03:00 → ~13:01 BRT (depois do deploy)** | **128** | **42×86** | **32,8%** | **36,6%** (71 lances) |
| Empate payout ~86% | — | — | ~53,8% | — |

32 contas. Quase tudo **M1 OTC** (sábado: mercado aberto fechado).

Os cortes **estão ligados**: WEAK PUT **0**, vela &lt;45% **0**, PUT chase **0**,
PUT corpo &lt;60% **0**. O robô **não** voltou ao lixo de venda. O WR caiu
**mesmo assim**, porque o volume que sobrou é quase só **CALL** num dia
ruim para compra.

## 2. De onde vêm os LOSS (86)

| Fatia | Ops | WR | LOSS | Papel |
|---|---|---|---|---|
| **CONTINUATION CALL** | 72 | **29,2%** | **51** | Maior fábrica de LOSS |
| **WEAK CALL** | 55 | **36,4%** | **35** | 2º (antes no recorte 13–14/08 estava ~51–63%) |
| PUT (qualquer) | 1 | 100% | 0 | Volume de venda zerado |

### Pares (mais LOSS)

| Par | n | WR | LOSS |
|---|---|---|---|
| **EURGBP-OTC** | 27 | **22,2%** | 21 |
| AUDJPY-OTC | 34 | 41,2% | 20 |
| **USDCAD-OTC** | 24 | **25,0%** | 18 |
| **EURUSD-OTC** | 13 | **15,4%** | 11 |
| USDCHF-OTC | 11 | 27,3% | 8 |
| GBPUSD-OTC | 14 | 50,0% | 7 |

Piores combos: EURGBP CONTINUATION CALL 28,6%; USDCAD WEAK CALL 23%;
USDCHF CONTINUATION CALL 20%; EURUSD CONTINUATION CALL **0%** (6/6 LOSS);
EURGBP WEAK CALL **0%** (6/6).

GBPUSD WEAK CALL 83% (n=6) — único bolsão bom, amostra pequena.

### Horário BRT (sábado)

| Hora | n | WR |
|---|---|---|
| 08h | 24 | **12,5%** |
| 09h | 11 | **9,1%** |
| 12h | 20 | 25% |
| 11h | 36 | 41,7% (ainda abaixo do empate) |
| 06–12 | 87 | 32% |
| 12–13 | 22 | 23% |

Madrugada 0–6 (logo após o deploy): 47% — menos ruim.

### Padrão das 3 velas (só CALL)

Todas as três sequências perdem: RED-GREEN-GREEN 34%; **GREEN-GREEN-GREEN
30%** (perseguir alta — espelho do PUT chase que cortamos); GREEN-RED-GREEN 30%.

Corpo **60–80%**: 40 ops, **12,5%** (pior fatia técnica). Corpo ≥80%: 41,6%
(ainda abaixo do empate).

RSI CALL **60–70**: 23% (56 ops). RSI 55–60: 41%.

Repetir o **mesmo par+direção em ≤3 min**: 13 ops, **15%** WR (11 LOSS) —
o caso EURGBP 03:00 WIN / 03:02 LOSS.

## 3. Por que não melhorou

O 59% da hipótese dos cortes era **o mesmo histórico de 13–14/08 com linhas
riscadas**. Não era previsão do **sábado OTC**.

Nesse recorte antigo, CONTINUATION CALL fazia **54%** e WEAK CALL **~51%**.
Hoje os **mesmos rótulos** fazem 29% e 36%. O mercado (sábado, sessão OTC)
não está pagando compra de continuação. Como o ranking **sempre prefere
CALL** e o PUT de qualidade quase não aparece, o robô ficou **100% no lado
que está errado hoje**.

Filtros novos cumpriram o papel (não vazou WEAK PUT). Não protegem contra
**CALL de continuação em tendência que já andou 3 verdes / RSI 60–70**.

## 4. O que **não** fazer nesta auditoria

- Religar WEAK PUT (não é a fonte do LOSS de hoje).
- Cortar horário sem pedido (08–09h foi tóxico **neste** sábado).
- Tratar 128 linhas / 71 únicas como lei permanente.

Candidatos **implementados 15/08 tarde** e **desligados 16/08** (WR piorou):

1. Ranking **sem lado**: melhor setup (CONTINUATION > WEAK), CALL ou PUT.
2. **CALL_CHASE**: CONTINUATION + 3 verdes — não opera (espelho do PUT chase).
3. **REPEAT_ENTRY**: após ordem aceita, o mesmo par fica de fora **1 vela**.

Produção em 16/08: substitution 13–14/08 (sem esses cortes).

