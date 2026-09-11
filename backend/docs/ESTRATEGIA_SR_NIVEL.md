# S/R como sinal: entrada a favor do nível (11/09/2026)

Pedido do dono, com estas palavras: *"se está em uma região de suporte ou
resistência é para o El Capo pegar operação, não cancelar operação, mas ele
pega a favor da estratégia: uma região de resistência vai pegar operação para
venda, uma região de suporte vai pegar para compra"*, e *"encostou na
resistência = venda; encostou no suporte = compra, mas sempre comprando ou
vendendo no início da vela, e se estiver próximo pode operar, não precisa
grudar"*.

O que motivou: em 11/09, com o S/R só como filtro, **66 de 101 entradas
anunciadas foram canceladas na hora da compra** (5h20 de produção), porque a
vela que fechava durante a espera levava o preço até um topo ou fundo.

## Os dois papéis do suporte e da resistência

| papel | onde vive | o que faz |
|---|---|---|
| filtro | `sr_respect.evaluate_respect` | nenhuma estratégia entra contra o nível |
| sinal | `sr_level_trade.find_level_trade` | perto do nível, a entrada é a favor dele |

As outras estratégias seguem intactas: longe de qualquer nível quem decide é o
motor clássico (e a REV-Z no mercado aberto, que esta regra não toca). O pavio
continua sendo só filtro — nunca gera entrada.

## A regra

Níveis montados **só com velas fechadas**: pivôs de raio 2 com 2+ toques em 120
velas, pivôs de 1 toque em 60 velas, e a máxima/mínima das últimas 30.

- preço a até `SR_LEVEL_PROXIMITY_ATR` (0,5 ATR) de uma resistência → **PUT**;
  de um suporte → **CALL**;
- **a última vela não pode ter passado da linha**: se ela fechou do outro lado
  do nível, virou rompimento e não se opera (`SR_LEVEL_BREAK_ATR=0`, decisão do
  dono em 11/09 23h, depois de 9 perdas de um único sinal assim). Furar com o
  pavio e fechar de volta continua valendo: é rejeição;
- **no máximo `SR_LEVEL_MAX_PER_HOUR` (3) entradas de nível por conta por
  hora** (`SR_LEVEL_HOURLY_LIMIT`). O contador vive no processo do
  robot-runtime, é por conta e olha os últimos 60 minutos. Vale nos dois
  portões E no disparo: no teto, o nível não troca mais a direção — entrar como
  a análise queria seria entrar contra o nível, então não há entrada na vela;
- suporte e resistência a distâncias parecidas (`SR_LEVEL_SQUEEZE_ATR`) → preço
  espremido, não opera;
- confiança 85, +5 com 2+ toques, +5 a menos de 0,15 ATR do nível. É a escala
  0–100 do motor clássico **de propósito**: escala própria obrigaria a rebaixar
  o piso nos dois portões, que é a armadilha que já calou REV-Z, SR-R e Vertex.

### Pavio na entrada de nível (`level_wick_ok`)

Direcional, porque no nível o pavio a favor é a confirmação:

- pavio de cima na resistência (venda) ou de baixo no suporte (compra) → libera;
- pavio **contra** a entrada ≥ 40% do range → barra;
- vela indecisa, com pavio ≥ 30% dos dois lados → barra;
- vela com range < 0,3 ATR não conta (é ruído de tick).

## Onde entra no código

1. `signal_engine.apply_level_trade` — por último entre as estratégias, depois
   do clássico, REV-Z e Vertex. Não roda no mercado aberto.
2. `main.apply_level_guard` — portão de estratégia: só payout, piso do painel,
   cooldowns, entrada repetida e o pavio do nível. Os filtros de qualidade do
   clássico ficam de fora (ele segue a vela; aqui é reversão).
3. `main.candidate_meets_cycle_threshold` — o SEGUNDO portão, com o mesmo
   desvio. Faltar num dos dois deixa a estratégia muda, sem erro no log.
4. `main.revalidate_level_before_entry` — no disparo, com a vela recém-fechada:
   se há nível perto, a entrada sai **a favor dele, trocando a direção** se a
   análise apontava o outro lado (`[SR_LEVEL_ENTRY_FLIP]`). Sem nível e com
   candidato de nível, cancela (`NIVEL_SUMIU_NA_ENTRADA`).

## Anúncio: só quando a ordem sai

O painel e a narração não anunciam mais a entrada durante a espera — antes
diziam "melhor ativo encontrado" com direção e contagem, e 65% disso era
cancelado depois. Agora mostram "El Capo está analisando o mercado —
confirmando na virada da vela", e a entrada (com a análise) é falada quando a
ordem é aceita. Cancelamento de filtro virou ciclo sem oportunidade, não
"entrada rejeitada" (`[ENTRY_CANCELLED_BY_FILTER]`).

## Medição — leia antes de prometer resultado

Backtest em **21 pares OTC, ~60 dias de velas M1 reais** (361.094 minutos
avaliados, entrada na abertura da vela seguinte, expiração de 1 minuto):

| "perto do nível" | minutos com entrada (por par) | acerto |
|---|---|---|
| 0,50 ATR (regra atual, sem passar da linha) | 40,9% | **49,94%** |
| 0,25 ATR (idem) | 25,6% | **50,03%** |
| 0,50 ATR aceitando passar 0,15 ATR da linha | 44,1% | 49,94% |
| 0,25 ATR aceitando passar 0,15 ATR da linha | 31,4% | 50,07% |
| 0,15 ATR | 24,7% | 50,06% |
| 0,15 ATR, só 2+ toques | 12,0% | 49,66% |
| 0,10 ATR, só 2+ toques | 8,4% | 49,69% |

Por origem do nível (0,25 ATR): topo visível 50,5%, máxima recente 50,0%,
fundo visível 49,6%, mínima recente 49,3%, pivô de 2 toques 49,1%.

**Empate com payout de 87% é 53,5%.** Nenhum recorte chega perto: é moeda, como
todo o resto do OTC (ver `docs/` e a varredura de 31/08). Restringir o nível
**não melhora o acerto, só reduz o volume** — e o volume é o que define a perda
em dinheiro (−6,5% do valor apostado por entrada, na média).

"Passou da linha" tira ~7% das entradas e não muda o acerto — serve para não
comprar no meio do rompimento, que foi o padrão das 9 perdas de um sinal só.

Decisão do dono em 11/09, depois de ver os primeiros 12 minutos no ar (15
ordens, 13 perdas, 6 sinais distintos, 2 contas no stop loss): **teto de 3
entradas de nível por conta por hora**. Com 18 contas ligadas isso limita o
sinal de nível a ~54 ordens/hora no sistema, e cada conta a 3.

## Reversibilidade

- `SR_LEVEL_TRADE=false` — desliga o sinal de nível (o filtro continua).
- `SR_LEVEL_PROXIMITY_ATR`, `SR_LEVEL_BREAK_ATR`, `SR_LEVEL_SQUEEZE_ATR`,
  `SR_LEVEL_WICK_AGAINST_RATIO`, `SR_LEVEL_WICK_TWO_SIDED_RATIO` — limites.
- `WICK_FILTER=false` — desliga também o pavio do nível.

Testes: `tests/test_sr_level_trade.py` (31),
`tests/test_entry_filter_cancel_message.py` (3),
`frontend/src/lib/robotPresentation.anuncio.test.ts` (5).
