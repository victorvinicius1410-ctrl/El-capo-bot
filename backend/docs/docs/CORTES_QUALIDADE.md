# Cortes de qualidade (WEAK PUT, vela fraca, DOJI)

Documento criado em **2026-08-15**. **Desligados em produção em 2026-08-16.**

No ar no sábado 15/08 o WR ficou **32,8%** (manhã) e **45,2%** (tarde, n=31),
abaixo do **~50–51%** da substitution 13–14/08. O cliente pediu voltar à
estratégia anterior. Flags em `signal_engine.py`:

`WEAK_PUT_HARD_BLOCK` = `CANDLE_WEAK_HARD_BLOCK` = `DOJI_HARD_BLOCK` =
`PUT_BODY_HARD_BLOCK` = `PUT_CHASE_HARD_BLOCK` = `PUT_WICK_HARD_BLOCK` =
`CALL_CHASE_HARD_BLOCK` = `REPEAT_ENTRY_HARD_BLOCK` = **False**.

O código dos filtros permanece (helpers + testes invertidos). Ligar de novo
é só virar as flags — **não** fazer isso sem evidência de dia útil, não sábado.

Backup pré-WEAK PUT: `/root/backups/2026-08-14_2039-BRT/`.


Documento criado em **2026-08-15**. Código no `/root/Backend`. **Sem deploy**
até autorização. Complementa [`ESTRATEGIA.md`](./ESTRATEGIA.md) e
[`WEAK_PUT_BLOCK.md`](./WEAK_PUT_BLOCK.md).

## 1. O que o robô **não** opera (interno)

| # | Situação | Regra | Flag |
|---|---|---|---|
| 1 | **WEAK PUT** | Setup WEAK + direção PUT, todos os ativos | `WEAK_PUT_HARD_BLOCK` |
| 2 | **Vela fraca / sem força** | Corpo da vela **&lt; 45%** do range | `CANDLE_WEAK_HARD_BLOCK` + `CANDLE_MIN_BODY_RATIO=0.45` |
| 3 | **DOJI** | Corpo **≤ 10%** | `DOJI_HARD_BLOCK` + `DOJI_MAX_BODY_RATIO=0.10` |

Recovery **não** afrouxa nenhum dos três (`CANDLE_STRENGTH` e `DOJI_FILTER`
saíram de `FREQUENCY_RECOVERY_SOFT_BLOCKS`).

DOJI já está dentro de “sem força”; o filtro 3 é explícito e pega corpo
miúdo mesmo se a classificação de direção não gravar `DOJI`.

## 2. Evidência (677 ops pós-13/08)

Sem os três cortes: 348 x 329 · **51,4%**.  
Com os três: **370** ops · 220 x 150 · **59,5%** (acima do empate ~53,8%).

Os 8 dojis do recorte já estavam em vela fraca e/ou WEAK PUT — o ganho
numérico vem de (1)+(2). O (3) evita o caso isolado.

WEAK CALL **com vela ≥ 45%** permanece (152 ops · **63,2%** nesse recorte).
É a alavanca de **volume com acerto**.

Horários **não** entram neste corte (pedido do cliente).

## 3. Como manter (ou subir) volume sem voltar ao lixo

Não cortar horário. Não matar WEAK CALL. O robô parado é pior do que um
WEAK CALL de vela forte.

1. **Fallback de frequência = só WEAK CALL + corpo ≥ 45%**  
   Recovery ainda solta `PRICE_ACTION_SETUP`, então o ciclo vazio pode
   preencher com compra fraca **desde que a vela seja forte**. Isso substitui
   o antigo “preencher com WEAK PUT”.
2. **Substitution** continua: se no mesmo scan existir CONTINUATION/REVERSAL,
   WEAK perde o slot.
3. **Não reduzir lista de ativos OTC** neste passo — menos pares = menos
   volume único. Ranking já prefere AUDJPY/USDCHF CALL quando empatam.
4. **M1** segue o timeframe com edge medido; não empurrar M5/M15 para “fazer
   número”.
5. Duplicata entre contas: ~40% das linhas eram o **mesmo lance** em outra
   conta. Volume de **placar somado** cai se olharmos único; volume **por
   usuário** se mantém se cada conta ainda achar WEAK CALL / CONTINUATION
   no ciclo.

Expectativa no recorte 677: de ~677 linhas para ~370 que passam (~45% a
menos no agregado), WR **59,5%**. Por conta o drop é menor se o recovery
passar a preencher CALL forte em vez de PUT fraca (troca qualidade, não
zera ciclo).

PUT que sobra (sem WEAK): filtros `PUT_CHASE` / `PUT_BODY` / `PUT_WICK`.
**CALL nunca perde o ciclo para PUT.** Ver [`PUT_QUALIDADE.md`](./PUT_QUALIDADE.md).
Mercado aberto: [`MERCADO_ABERTO.md`](./MERCADO_ABERTO.md).

## 4. Arquivos

| Arquivo | Papel |
|---|---|
| `backend/signal_engine.py` | Flags, `is_weak_candle_body`, `is_doji_body`, hard blocks |
| `backend/main.py` | `CRITICAL_TRADE_BLOCKS`, portão do ciclo, `apply_strategy_guard` |
| `tests/test_accuracy_ranking_substitution.py` | WEAK PUT + vela fraca + DOJI no recovery/portão |
| `tests/test_loss_pattern_blocks.py` | Recovery não afrouxa os três |

## 5. Deploy

**No ar** (2026-08-15): backend-gateway + robot-runtime + painel.
Backup pré-WEAK PUT: `/root/backups/2026-08-14_2039-BRT/`.
