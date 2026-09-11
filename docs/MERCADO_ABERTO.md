# Mercado aberto (forex) no El Capo

Documento criado em **2026-08-15**. Complementa [`ESTRATEGIA.md`](./ESTRATEGIA.md)
§7 e [`CONFIGURACOES.md`](./CONFIGURACOES.md).

## 1. Problema

Usuários escolheram **Mercado aberto** e o robô **não operou**. Na auditoria
pós-13/08 o histórico teve **0 operações** em pares sem `-OTC` (tudo OTC).

Causas empilhadas:

1. **Ambos (BOTH)** era só etiqueta: a varredura **sempre** ia para OTC, mesmo
   com forex aberto (timeout antigo de ~20 ativos em fila).
2. **Mercado aberto + M1**: o canal de execução do M1 é **turbo**. Nos pares
   reais (EURUSD, GBPUSD, …) o turbo muitas vezes está **fechado**; o robô
   marcava `ACTIVE_CLOSED` em todos e o ciclo virava “não operou”.
3. Fora da sessão forex (sábado / sexta após 22:00 UTC) OPEN cai para OTC —
   esperado — mas de segunda a sexta o item 2 ainda travava quem escolhia só
   Aberto.

## 2. Comportamento atual

| Escolha | Sessão forex | O que o robô varre |
|---|---|---|
| OTC | qualquer | 10 pares `*-OTC` |
| **Ambos** | **aberta** | **10 OTC + 10 abertos** (OTC primeiro) |
| Ambos | fechada | só OTC |
| Mercado aberto | aberta | 10 pares sem `-OTC` |
| Mercado aberto | aberta, **turbo/binary fechado em todos** | **fallback OTC** no ciclo (`reason=execution_channel_closed_on_all_open_assets`) |
| Mercado aberto | aberta, **payout ausente/timeout** após 3 ciclos sem entrada | **fallback OTC** no ciclo (`reason=payout_unavailable_on_open_assets`) |
| Mercado aberto | fechada | OTC (cadeado na UI) |

Cache compartilhado de candles/payouts (`SHARED_MARKET_*`) permite varredura
de 20 ativos sem o timeout de 2026-07 que motivou BOTH→só-OTC.

**2026-08-18 tarde:** com candles lentos, 20 ativos × ~5s ainda estoura
`ROBOT_CYCLE_TIMEOUT_SECONDS=110`. O scan agora para no primeiro CALL/PUT
aprovado (`ANALYSIS_EARLY_STOP`) ou aos 85s (`ANALYSIS_SCAN_BUDGET`). Em
BOTH o cursor rotativo pode começar no bloco OPEN; se esses pares
timeoutarem, o orçamento acaba antes dos OTC. Preferir **OTC** no painel
se o overlay ficar só em “Buscando melhor oportunidade” com Ambos.

**2026-08-18 ~23:40 UTC (conta marketing `victorvinicius@gmail.com`):**
mesmo sintoma com `market_mode=BOTH` — worker ligado, `[BEST_CANDIDATE]`
alto, then `[NO_TRADE]` por 40+ min, enquanto outras contas em OTC
enviavam ordem no mesmo ativo. Ajuste: `market_mode` persistido para
**OTC** e worker religado. Detalhe em `ROBO_E_SUPORTE.md`
(incidente 23:40 UTC).

**2026-08-19 ~19:45 UTC (mesma conta, `market_mode=OPEN`):** forex aberto,
worker ligado o dia todo, overlay em “Buscando melhor oportunidade”,
**248** ciclos sem entrada. Candidatos com confiança 86–96 vinham com
`payout=None` + `PAYOUT_UNAVAILABLE`. O fallback de canal **não disparava**
porque o cache de payout estava vazio (`cached_asset_open_for_active` →
`None`, `known=0`). A frota em OTC comprava no mesmo horário. Correção:
após 3 ciclos consecutivos sem entrada com `PAYOUT_UNAVAILABLE` (ou cache
totalmente vazio), o ciclo OPEN varre OTC. Log com
`reason=payout_unavailable_on_open_assets`. Testes em
`tests/test_market_mode_assets.py`.

## 3. Arquivos

| Arquivo | Papel |
|---|---|
| `backend/main.py` | `effective_market_mode`, `resolve_analysis_assets`, `open_market_fallback_otc_reason` |
| `tests/test_market_mode_assets.py` | BOTH+aberto, fallback OPEN→OTC |
| `Frontend/src/lib/robotSettings.ts` | Texto da opção Ambos |

## 4. Log

```
[OPEN_MARKET_NO_CHANNEL_FALLBACK_OTC] user_id=… timeframe=M1
  reason=execution_channel_closed_on_all_open_assets
```

Significa: o usuário pediu aberto, a sessão forex está aberta, mas a corretora
não está vendendo o canal daquele timeframe nos pares reais — o ciclo opera
OTC para não zerar o dia.

```
[OPEN_MARKET_NO_CHANNEL_FALLBACK_OTC] user_id=… timeframe=M1
  reason=payout_unavailable_on_open_assets
```

Significa: o aberto está na sessão, mas `/payouts` timeoutou ou voltou vazio
por 3+ ciclos (`OPEN_MARKET_PAYOUT_FALLBACK_AFTER_CYCLES`). Sem payout o
portão conservative (`min_payout=80`) recusa tudo. O ciclo passa a varrer
OTC. O modo salvo no painel continua **Aberto**; só a fila daquele ciclo
muda. Se o payout do aberto voltar, os próximos ciclos tentam OPEN de novo.
