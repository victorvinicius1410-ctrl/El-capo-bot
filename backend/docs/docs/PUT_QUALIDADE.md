# PUT de qualidade (sem WEAK) — padrões dos WINs

Documento criado em **2026-08-15**. Complementa [`WEAK_PUT_BLOCK.md`](./WEAK_PUT_BLOCK.md)
e [`CORTES_QUALIDADE.md`](./CORTES_QUALIDADE.md). **Código em produção** desde
2026-08-15 (deploy backend + painel).

## 1. Pergunta

Depois de cortar WEAK PUT, a maior parte das entradas vira CALL. Quais
padrões aparecem nos **PUT que ganharam** (vendas), **excluindo WEAK**, para
subir volume de venda **sem** voltar ao lixo (~43% WR)?

## 2. Recorte

- Fonte: `robot_trade_history` (Supabase), sem gale.
- Janela: **13/08 17:34 BRT** (robô no ar pós-substitution) até **15/08 ~02:26 BRT**.
- 903 ops liquidadas. PUT total **403** (188×215, **46,7%**).
- Destas, **335** eram WEAK PUT (fora deste estudo).
- Restam **68 PUT não-WEAK**: 37 WIN × 31 LOSS → **54,4%** nas linhas.
- Únicas (ativo+direção+TF+minuto BRT): **41** lances, 21×20 → **51,2%**.
  ~40% das linhas são o mesmo lance em outra conta — o WR “bonito” das 68
  infla um pouco.

Empate com payout ~86% ≈ **53,8%**. As 68 linhas passam por pouco; o único
já fica no empate. Amostra **pequena**. Tratar combinação com n&lt;20 como
hipótese, não lei.

## 3. O que os WINs têm em comum (100% deles)

Todos os **37** WINs (e também todos os 31 LOSS) deste recorte são o **mesmo
esqueleto**:

| Campo | Valor nos 68 PUT não-WEAK |
|---|---|
| Setup | **CONTINUATION** (REVERSAL PUT = **0**. S/R PUT = **0**) |
| Ativo | **GBPUSD-OTC** (100%) |
| Timeframe | M1 (65/68); M5 só 3 |
| EMA | EMA9 **abaixo** da EMA21 |
| Últimas 3 / 5 velas | direção **DOWN** |
| Vela atual | **DOWN** |
| Volatilidade | NORMAL |
| Perto de suporte/resistência | não |
| Confiança | quase tudo 95–100 (não separa WIN de LOSS) |

Por que só GBPUSD: o hard block `WEAK_CONTINUATION_PUT_ASSETS` já **proíbe**
CONTINUATION+PUT em EURUSD, AUDUSD, USDCAD, USDCHF, EURGBP, AUDJPY. O que
sobra de venda “de verdade” no OTC listado é sobretudo **GBPUSD**. Cortar
WEAK PUT não cria venda em outros pares — só tira o volume ruim.

RSI mediano no WIN e no LOSS é quase igual (~41,3). Confiança alta **não**
prediz WIN.

## 4. Padrões que **diferenciam** WIN de LOSS (dentro desse esqueleto)

Comparar só a lista de WINs mentiria: o LOSS tem o mesmo esqueleto. O que
muda é o **detalhe da sequência e o corpo**.

### 4.1 Sequência das 3 velas (o melhor sinal desta auditoria)

| 3 velas (mais antiga → atual) | Ops | WIN×LOSS | WR |
|---|---|---|---|
| **GREEN-RED-RED** (puxada verde, depois 2 vermelhas) | 17 | 12×5 | **70,6%** |
| RED-GREEN-RED | 27 | 14×13 | 51,9% |
| **RED-RED-RED** (já vinha caindo 3 vezes) | 24 | 11×13 | **45,8%** |

Leitura simples: venda **depois de um respiro** (um verde no meio/início,
depois duas vermelhas) acerta. Venda **atrasada** (três vermelhas seguidas)
é perseguir o movimento e perde. Os 11 WINs de 3 vermelhas existem, mas o
grupo inteiro fica **abaixo do empate**.

### 4.2 Corpo da vela atual (força da venda)

| Corpo / range | Ops | WR |
|---|---|---|
| 60–80% | 32 | **59,4%** |
| ≥ 80% | 30 | 56,7% |
| 45–60% | 6 | **16,7%** (n pequeno) |

Nos WINs: 19 com 60–80%, 17 com ≥80%, **1** só na faixa 45–60%.
Para PUT, **45% não basta**; o grupo que parece vantagem começa em **~60%**.

### 4.3 RSI (não “sobrevendido fundo”)

| RSI | Ops | WR |
|---|---|---|
| **40–45** | 40 | **60,0%** |
| 30–40 | 19 | 52,6% |
| &lt;30 | 1 WIN | n inútil |
| ≥50 | 0 | CONTINUATION PUT com RSI alto já é bloqueado (`CONTINUATION_DEAD_RSI`) |

WIN típico: RSI **~42**, ainda no lado de baixo mas **não** no fundo. RSI
&lt;40 no agregado é só empate.

### 4.4 Combos (n≥15)

| Combo | n | WR |
|---|---|---|
| CONTINUATION + DOWN + corpo **≥80%** + RSI **40–45** | 23 | **69,6%** |
| CONTINUATION + DOWN + corpo **60–80%** + RSI **30–40** | 15 | **66,7%** |
| CONTINUATION + DOWN + corpo 60–80% + RSI 40–45 | 15 | 46,7% |

O primeiro combo é o candidato mais limpo para **priorizar** PUT no ranking
(não para inventar PUT fraco).

### 4.5 Pavio

Pavio de baixo (contra a venda) **≤15%**: 55 ops, **56,4%**.  
15–30%: 13 ops, **46,2%**.  
Pavio de cima grande não ajudou (15–30% → 48%).

### 4.6 Horário (só contexto)

22h BRT: 22 ops, **68,2%**. Pedido do cliente: **não cortar horário**. Não
usar hora como regra de PUT.

## 5. Por que o mix vira CALL depois do corte

No mesmo recorte:

| Fatia | Ops | WR |
|---|---|---|
| CALL total | 500 | 52,6% |
| PUT total (ainda com WEAK) | 403 | 46,7% |
| PUT WEAK (a retirar) | 335 | 45,1% |
| PUT não-WEAK | 68 | 54,4% |
| CALL não-WEAK | 230 | 54,3% |
| CALL WEAK | 270 | 51,1% |

WEAK era **83%** das vendas. Tirar WEAK PUT deixa ~**68 vendas vs ~500 compras**
neste placar somado. Não é bug da análise: é o recovery preenchendo ciclo com
WEAK PUT + a lista que **já proíbe** CONTINUATION PUT na maioria dos pares.

REVERSAL não apareceu como PUT — **não** dá para “ligar reversão” e esperar
volume de venda neste histórico.

## 6. Como subir PUT **sem** WEAK (implementado 15/08, **off em 16/08**)

Pedido do cliente em 16/08: voltar à substitution 13–14/08. Todas as flags
abaixo estão **False**. O texto desta seção é histórico da tentativa.

Não voltar WEAK PUT. Ranking **não** escolhe lado: o melhor setup do ciclo
vence (CONTINUATION PUT pode ganhar de WEAK CALL). Perseguir 3 velas iguais
é bloqueado nos **dois** lados (`PUT_CHASE` / `CALL_CHASE`). Após uma ordem,
o mesmo par espera **1 vela** (`REPEAT_ENTRY`).

| Regra | Flag | Efeito |
|---|---|---|
| CONTINUATION PUT em **RED-RED-RED** | `PUT_CHASE_HARD_BLOCK` | Não opera (45,8% no recorte) |
| PUT com corpo **&lt; 60%** | `PUT_BODY_HARD_BLOCK` + `PUT_MIN_BODY_RATIO=0.60` | Não opera |
| PUT com pavio de baixo **&gt; 15%** | `PUT_WICK_HARD_BLOCK` | Não opera |
| Escolha do ciclo | `pick_best_candidate` | Se existir **qualquer CALL** aprovada, ela ganha o slot. PUT só quando não há compra |

GREEN-RED-RED + corpo ≥60% + pavio curto **pode** operar quando for a única
direção do ciclo. Recovery **não** afrouxa `PUT_CHASE` / `PUT_BODY` / `PUT_WICK`.

Pares tóxicos de CONTINUATION PUT (`WEAK_CONTINUATION_PUT_ASSETS`) **permanecem**
bloqueados.

## 7. O que esta auditoria **não** autoriza

- Liberar WEAK PUT “só um pouco”.
- Inventar REVERSAL PUT (zero ocorrências).
- Cortar horário para fabricar PUT.
- Confiar em confiança 95–100 como filtro de venda.
- **Subir PUT no ranking para ganhar de CALL** (pedido explícito: não deixar
  de pegar compra).
- Generalizar GREEN-RED-RED para outros pares: o recorte de WINs foi
  **GBPUSD**; os filtros de qualidade valem para todo PUT permitido, sem
  abrir a lista tóxica.
