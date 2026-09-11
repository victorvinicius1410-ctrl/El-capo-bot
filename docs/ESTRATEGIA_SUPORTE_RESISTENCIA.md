# Política de suporte e resistência

Revisada em **2026-09-09**, depois de uma auditoria do motor de ponta a ponta.
Código em `backend/sr_respect.py`.

## O que estava errado

Três defeitos, todos medidos, nenhum visível no log:

### 1. Não era suporte e resistência

`_support_resistance_context` chamava de suporte o menor `min` das 23 velas
anteriores e de resistência o maior `max`. Em M1 isso é o extremo de uma janela
de 23 minutos: não contava toques, não validava o nível, não olhava pivô.

Medido em 50.700 transições de vela: o valor do "suporte" mudava em **25,7%**
das velas, a resistência em **25,5%**. Um nível que se move a cada quatro
minutos não é um nível.

### 2. A zona cobria metade do gráfico

A tolerância era `max(range × 0,12, ATR × 0,65)` para **cada lado**. Medido:
**55,0%** das leituras caíam dentro dela.

### 3. Contradição fatal — o setup de S/R era impossível de operar

`_price_action_setup` só devolvia `"SUPPORT_RESISTANCE"` quando o preço estava
colado no nível (`signal_engine.py:1819`). Quatro linhas de filtro adiante,
`check("SR_ZONE", not in_support_resistance_zone)` barrava exatamente essa
condição — e como *hard block*, que nem o recovery relaxava.

Medido sobre 8.000 leituras: **383 setups `SUPPORT_RESISTANCE`, 383
bloqueados. 100%** — a implicação é lógica, não estatística.

Consequências silenciosas:

- o setup valia **15 pontos de score**, que eram somados, e a entrada morria na
  mesma passagem;
- o filtro `SUPPORT_RESISTANCE` só podia ser satisfeito pela via `CONTINUATION`
   — o nome prometia o oposto do que ele fazia;
- `LEVEL_REJECTION` exigia rejeição confirmada *dentro* da zona já vetada, e
  nunca decidia nada sozinho.

O motor não respeitava a região: ele **fugia** dela, e a única leitura de S/R
que sabia fazer estava permanentemente desativada por outro filtro do mesmo
arquivo.

### 4. O modo LIVE dispensava a região

`LIVE_NON_WAIVABLE` continha só bloqueios de execução. `SR_ZONE`,
`LEVEL_CONFLICT` e `LEVEL_REJECTION` haviam sido deixados de fora em 07/09,
porque matavam 138 de 138 liberações.

Prova em produção, conta de marketing com o modo ligado:

```
20:52:24 [LIVE_DEMO_RELEASE] NZDUSD-OTC PUT barrados=LEVEL_CONFLICT,LEVEL_REJECTION,SR_ZONE
20:53:06 [ORDER_ACCEPTED]    NZDUSD-OTC PUT order_id=14247169254
```

`LEVEL_CONFLICT` num PUT significa uma coisa só: **preço colado no suporte e o
robô vendeu.** Em 48 h foram 4 dessas em 6 liberações, e as 6 viraram ordem
real.

## A política atual

### A região

Níveis de verdade, com a mesma construção da SR-R (reaproveitada diretamente,
para não existirem duas definições de nível no projeto):

- pivôs de raio 2 sobre as últimas **120 velas fechadas**;
- agrupados em níveis por **0,5 ATR**;
- exigindo **2+ toques** — 1 toque é um repique, 2 é um nível;
- suporte = nível mais forte abaixo do preço; resistência = o mais forte acima;
  empate de toques desempata pelo mais próximo.

A meia-largura da região é a mesma 0,5 ATR — contra os ~12% do range de antes.

Quando não há velas ou pivôs suficientes, cai no extremo da janela e marca
`source = "WINDOW"`. Medido em 14.520 leituras reais: a fonte foi `PIVOT` em
**100%** dos casos com 160 velas disponíveis.

### O respeito

Quatro decisões, nesta ordem:

| situação | decisão |
|---|---|
| **contra o nível** (CALL na resistência, PUT no suporte) | recusado **sempre** |
| dentro da região, a favor, **com rejeição confirmada** | liberado |
| dentro da região, a favor, sem rejeição | recusado |
| rejeição válida mas **sem espaço** até o nível oposto | recusado |
| fora de qualquer região | liberado |

A rejeição confirmada usa os mesmos limiares do `_level_rejection_confirmed`
clássico (corpo ≥ 0,35, pavio a favor ≥ 0,20, pavio oposto ≤ 0,40), de
propósito: a mudança já troca a origem do nível e o veto, e mexer nos três ao
mesmo tempo tornaria o efeito impossível de atribuir.

O **espaço** é a condição que a versão antiga não tinha como checar, porque não
conhecia o nível oposto: rejeição no suporte com a resistência a menos de 1,5
ATR não tem para onde ir dentro da vela.

### Efeito medido

Sobre 14.520 leituras de velas M1 reais (3 ativos, 2026-09-09):

| resultado | leituras | % |
|---|---|---|
| fora da região — livre | 6.938 | 47,8% |
| rejeição no nível — **liberado** | 666 | 4,6% |
| contra o nível — bloqueado | 4.067 | 28,0% |
| na região sem rejeição — bloqueado | 2.606 | 17,9% |
| sem espaço — bloqueado | 243 | 1,7% |

O veto cego antigo bloqueava 54,8% das leituras; a regra nova bloqueia 47,6%.
Não é um afrouxamento: é uma **redistribuição**. A maior parte do que passou a
ser liberada estava "na zona" só porque a zona antiga cobria metade do gráfico,
e agora 28,0% das leituras são bloqueadas especificamente por serem entradas
**contra** o nível — que é o que o dono mandou parar.

### Vale para todas as estratégias

"Nunca contra o nível" não é do motor clássico, é do robô:

- **modo LIVE**: `SR_ZONE`, `LEVEL_CONFLICT` e `LEVEL_REJECTION` entraram em
  `LIVE_NON_WAIVABLE`. O modo continua afrouxando qualidade de setup, e só isso;
- **Vertex**: recusa com `VERTEX_CONTRA_O_NIVEL`. Dentro da região a favor não
  precisa de rejeição — o extremo do indicador é a tese dela;
- **estratégias nomeadas**: `sr_zone_exempt` continua como estava.

## Configuração

| variável | padrão | efeito |
|---|---|---|
| `SR_RESPECT` | `true` | liga a região por níveis e a regra de respeito |
| `SR_RESPECT_LOOKBACK` | `120` | velas fechadas para montar os níveis |
| `SR_RESPECT_TOLERANCE_ATR` | `0.5` | meia-largura da região, em ATR |
| `SR_RESPECT_MIN_TOUCHES` | `2` | toques mínimos para o nível valer |
| `SR_RESPECT_MIN_ROOM_ATR` | `1.5` | espaço mínimo até o nível oposto |

`SR_RESPECT=false` devolve o veto cego e o extremo de janela por inteiro, sem
redeploy. **`ROBOT_CANDLE_COUNT` precisa ser ≥ 160** para os 120 de lookback
caberem; com menos, a região funciona com o histórico que houver.

## Motivos no log

`sr_respect_reason` acompanha todo sinal: `OK_FORA_DA_REGIAO`,
`OK_REJEICAO_NO_NIVEL`, `CONTRA_O_NIVEL`, `NA_REGIAO_SEM_REJEICAO`,
`SEM_ESPACO_ATE_O_NIVEL_OPOSTO`, `SEM_DIRECAO_A_AVALIAR`. `sr_zone_source` diz
se o nível veio de `PIVOT` ou do fallback `WINDOW`.

## Nível visível e reconferência fechada (10/09/2026)

**Por quê.** O cliente Sergio (R$ 200 por entrada) perdeu três compras coladas
na máxima do gráfico: CHFJPY-OTC 17:10, EURGBP-OTC 17:33 e 18:36 (UTC), a
0,15–0,38 ATR do topo. A região acima só aceita nível com 2+ toques em 2 horas;
um topo novo não existia e as três saíram com `resistência=None` →
`OK_FORA_DA_REGIAO`. O cliente, olhando o mesmo gráfico, via a resistência.

**O que mudou.**

- **Nível visível** (`sr_respect.visible_levels`): todo topo/fundo de pivô das
  últimas 60 velas fechadas (1 toque basta) e a máxima/mínima das últimas 30
  velas. A entrada é recusada com `NIVEL_VISIVEL_A_FRENTE` quando um deles está
  a menos de `SR_VISIBLE_MIN_ROOM_ATR` do preço **na direção da operação** (CALL
  olha para cima, PUT para baixo). A máxima/mínima nunca "sai da frente": preço
  acima dela conta como distância zero ou negativa. Vale para o clássico, a
  Vertex e a REV-Z, na análise e na reconferência do disparo.
- **Reconferência do disparo fechada**: sem dado para verificar (timeout, sem
  velas, erro) a ordem não sai — `SR_ZONE_SEM_VERIFICACAO`. Antes liberava:
  26 timeouts em 4h, vários seguidos de ordem.
- **Vela recém-fechada entra nos níveis**: no segundo 0 a corretora às vezes
  ainda não abriu a vela nova; a lista terminava na que acabou de fechar e ela
  era tratada como "em formação", ficando fora dos níveis. EURNZD-OTC PUT
  19:19 (R$ 200): assim a reconferência liberou uma venda que era
  `CONTRA_O_NIVEL`. `candles_with_current_candle` acrescenta a vela em formação.
- **Uma busca por ativo e vela**: a reconferência consulta com `endtime` = abertura
  da vela de entrada (relógio da corretora) e `count` próprio
  (`ROBOT_CANDLE_COUNT + 1`). As contas que entram no mesmo ativo no mesmo minuto
  dividem a busca, e a chave não se confunde com a da análise.
- **Veredito gravado**: `analysis_json.sr_respect_reason` (análise) e
  `analysis_json.sr_entry_recheck_reason` (disparo) em cada operação.

**Custo medido** (245 ordens reais de 10/09, remontadas com a regra nova):

| distância mínima | barradas | acerto das barradas | acerto das que operam |
|---|---|---|---|
| 0,15 ATR | 74 (30%) | 54,1% | 51,5% |
| 0,25 ATR | 91 (37%) | 51,6% | 52,6% |
| 0,35 ATR | 117 (48%) | 53,0% | 51,6% |
| **0,5 ATR (padrão)** | **152 (62%)** | 52,0% | 52,7% |

A regra não muda o acerto medido: é coerência com o gráfico que o cliente vê,
decisão de produto do dono ("S/R como prioridade").

| variável | padrão | efeito |
|---|---|---|
| `SR_VISIBLE_LEVELS` | `true` | liga o nível visível |
| `SR_VISIBLE_MIN_ROOM_ATR` | `0.5` | distância mínima até o nível visível |
| `SR_VISIBLE_PIVOT_LOOKBACK` | `60` | velas fechadas para os pivôs de 1 toque |
| `SR_VISIBLE_EXTREME_WINDOW` | `30` | janela da máxima/mínima |
| `SR_ENTRY_RECHECK_FAIL_CLOSED` | `true` | sem dado no disparo, não opera |

Ordem de gale (`is_gale_order`) continua sem reconferência de nível.
