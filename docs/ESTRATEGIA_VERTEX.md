# Estratégia Vertex

Porte do indicador `Reversion` entregue pelo dono em 2026-09-09, escrito na
linguagem de indicadores da corretora. Código em `backend/vertex_strategy.py`,
integrado por `apply_vertex_override` em `backend/signal_engine.py`.

## O cálculo

É o **Value Chart** clássico — distância do fechamento até a média do
meio-preço, dividida por uma unidade de volatilidade própria — mais duas
confirmações leves, com peso 0,15 cada para não dominarem a escala base:

```
mba     = SMA((high + low) / 2, length)
lrange  = unidade de volatilidade × 0,2
vclose  = (close - mba) / lrange
momDev  = (close - close[momLength]) / lrange
maDev   = (SMA(close, maFast) - SMA(close, maSlow)) / lrange
vertex  = vclose + 0,15 × momDev + 0,15 × maDev
```

`lrange` tem dois branches no original, escolhidos por `length > 7`. Com o
padrão `length = 5` vale o branch curto (média de 5 ranges corrigidos). O branch
longo está implementado igual, **inclusive a assimetria de índice** de
`prev(highest(high,varp), -varp+1)`, que é do script original e foi mantida de
propósito para o valor bater com o gráfico do dono.

`tests/test_vertex_strategy.py` recalcula a fórmula de forma independente e
compara com o módulo, com 9 casas de precisão.

## A leitura

Reversão à média, com os `extTop`/`extBot` do indicador:

| condição | entrada |
|---|---|
| `vertex >= +12` | **PUT** — preço esticado para cima |
| `vertex <= -12` | **CALL** — preço esticado para baixo |
| entre os níveis | não opera (`VERTEX_FORA_DO_EXTREMO`) |

## Frequência medida

Sobre **14.520 leituras de 15.000 velas M1 reais** da BullEx (EURUSD-OTC,
GBPUSD-OTC, AUDCAD-OTC), coletadas em 2026-09-09:

| métrica | valor |
|---|---|
| disparos (`\|vertex\| >= 12`) | 538 — **3,705%** |
| estimativa por ativo/dia | ~53 sinais |
| `\|vertex\|` mediano no disparo | 13,5 |
| `\|vertex\|` máximo | 32,5 |

Volume não é problema: com 10 ativos varridos há muito mais sinal do que ciclo
para consumir. Isso descarta o risco que travou a REV-Z no sistema 02, onde a
estratégia ligada dava **zero** operações e nada podia ser medido.

## O que NÃO se sabe

**Nenhum backtest de acerto foi rodado.** O que está medido é a fidelidade do
cálculo e a frequência de disparo — nada permite afirmar taxa de acerto,
vantagem ou retorno. A semelhança de família com a REV-Z não transfere
resultado: a unidade de volatilidade, a média de referência e o gatilho são
outros.

Vale especialmente a ressalva de OTC. Em 164.000 velas sintéticas a série se
comporta como passeio aleatório em todas as famílias testadas, e a BullEx
praticamente só vende OTC. `VERTEX_ALLOW_OTC` vem ligado para a estratégia poder
operar de fato — operar em OTC é apostar na forma do indicador, não numa
vantagem medida.

**O próximo passo honesto é um backtest de acerto sobre as velas já coletadas.**

## Suporte e resistência continua valendo

A Vertex é reversão e pode apontar CALL com o preço colado na resistência.
Nesse caso a entrada é recusada com `VERTEX_CONTRA_O_NIVEL`: "nunca contra o
nível" vale para todas as estratégias, não só para o motor clássico. Dentro da
região a favor a Vertex **não** precisa de rejeição confirmada — o extremo do
indicador é a tese dela. Ver `ESTRATEGIA_SUPORTE_RESISTENCIA.md`.

## Backtest de acerto — a estratégia é nula

Rodado em 2026-09-09 sobre **225.010 velas M1 reais** da BullEx. Entrada no
fechamento da vela, resultado na seguinte, com a regra de respeito a S/R
aplicada como em produção.

| cenário | n | acerto | empate exige | retorno/op |
|---|---|---|---|---|
| OTC · piso 55 | 3.035 | **50,25%** | 53,48% | **−6,03%** |
| OTC · piso 75 | 304 | 46,67% | 53,48% | −12,73% |
| Aberto · piso 55 | 4.967 | 52,55% | 54,05% | −2,78% |
| Aberto · piso 75 | 170 | 54,26% | 54,05% | +0,38% |

**Nenhum corte de `|vertex|` sobrevive ao holdout.** Testando 10 pares abertos
por 15,3 dias (7.422 operações), com 5 pares de ajuste e 5 de holdout, a coluna
do holdout nunca alcança o empate — em nenhum corte de 12 a 24 — e o ajuste
supera o holdout nas oito linhas. É a assinatura de seleção, não de vantagem.
O melhor corte aparente, `|vertex| >= 19`, dá 56,03% no geral e **52,52% no
holdout**; a cauda alta quebra (`>= 24` dá 44,00%).

⚠️ Numa primeira passagem, com um terço dos dados, apareceu um gradiente
monotônico de 52% a 62%. **Ele se desfez com a amostra completa.** É o motivo
de separar holdout antes de acreditar em gradiente.

**A Vertex sozinha não prevê a vela seguinte.** O porte está correto — o teste de
fidelidade bate em 9 casas decimais.

## Mas a Vertex CONFIRMADA pelo motor clássico é outra coisa

A tabela acima mede o indicador cru. O pipeline real não é esse: o
`apply_strategy_guard` recalcula os filtros do motor clássico sobre a mesma vela
e **desconta as penalidades do score**, que é o que o portão do ciclo compara
com o piso. Com o piso em 55 e a confiança da Vertex entre 55 e 75, o efeito é
uma escala móvel — quanto mais forte o disparo, mais desaprovação clássica ele
tolera:

| confiança (|vertex|) | penalidade tolerada |
|---|---|
| 56 (~12,4) | 1 ponto — na prática, exige aprovação limpa |
| 65 (~16) | 10 pontos |
| 75 (≥20) | 20 pontos |

Medido em 51.010 velas M1 OTC (6 pares, 6,9 dias), rodando o motor clássico
sobre cada disparo:

| recorte | n | acerto | retorno/op | ops/dia |
|---|---|---|---|---|
| todos os disparos (dispensando filtros) | 1.731 | 50,55% | −5,41% | 249,3 |
| **aprovados limpos pelo clássico** | **306** | **55,08%** | **+2,99%** | **44,1** |
| com 1 filtro barrado | 423 | 49,76% | −6,80% | 60,9 |
| com 2+ filtros barrados | 1.002 | 49,50% | −7,39% | 144,3 |

**Gradiente monotônico e na direção certa:** quanto mais o motor clássico
aprova, melhor o resultado. E no holdout o recorte limpo dá **56,28%** contra
52,83% no ajuste — o holdout melhor que o ajuste é o oposto da assinatura de
seleção.

**Por isso os filtros clássicos NÃO devem ser dispensados para a Vertex.** A
combinação "Vertex propõe, clássico confirma" é a única configuração medida
acima do empate. Dispensá-los, como o modo LIVE faz, devolveria os 50,55%.

Ressalvas: n=306 é amostra modesta, o ajuste (52,83%) fica abaixo do empate de
53,48%, e o recorte foi encontrado olhando os dados. **Precisa de validação para
a frente antes de virar afirmação.**

## Escala de confiança e o rescale obrigatório

`VERTEX_CONFIDENCE_FLOOR = 55`, `VERTEX_CONFIDENCE_MAX = 75`. A confiança cresce
com o quanto o indicador passou do nível extremo e satura 8 pontos além dele.

**Quanto o piso desce depende do mercado**, por decisão do dono em 2026-09-09,
tomada com os números acima à vista:

| mercado | piso aplicado | efeito prático |
|---|---|---|
| **OTC** | `VERTEX_CONFIDENCE_FLOOR` (55) | todo disparo `\|vertex\| >= 12` vira ordem |
| **aberto** | `VERTEX_CONFIDENCE_MAX` (75) | só `\|vertex\| >= 20`, que satura a confiança |

`VERTEX_OTC_FLOOR=false` devolve o teto de 75 também em OTC, sem redeploy.

O mínimo do painel é calibrado para a escala 0–100 do motor clássico e o padrão
é **80**. Comparar 80 contra um teto de 75 barra 100% dos sinais, sem erro
nenhum no log — o robô só "aparece parado". Esta armadilha já custou uma rodada
inteira de diagnóstico em três estratégias diferentes (REV-Z, SR-R e modo LIVE),
então o rescale existe nos **dois** portões:

- `apply_strategy_guard` → `[VERTEX_MIN_CONFIDENCE_RESCALED]`
- `candidate_meets_cycle_threshold` → via `vertex_min_confidence`

`vertex_min_confidence` nunca **aumenta** o piso: quem configurou menos de 75
continua com o seu.

## Configuração

| variável | padrão | efeito |
|---|---|---|
| `VERTEX_ENABLED` | `false` | liga a estratégia; sobrepõe o motor clássico |
| `VERTEX_ALLOW_OTC` | `true` | permite ativo sintético |
| `VERTEX_LENGTH` | `5` | base da escala (`> 7` troca o branch de `lrange`) |
| `VERTEX_MOM_LENGTH` | `14` | período do momentum |
| `VERTEX_MA_FAST` | `9` | média rápida |
| `VERTEX_MA_SLOW` | `21` | média lenta |
| `VERTEX_EXT_TOP` | `12` | nível extremo superior |
| `VERTEX_EXT_BOT` | `-12` | nível extremo inferior |

`VERTEX_ENABLED=false` devolve o motor clássico por inteiro, sem redeploy.

## Motivos de recusa

| motivo | quando |
|---|---|
| `VERTEX_FORA_DO_EXTREMO` | `\|vertex\|` não passou do nível |
| `VERTEX_VELAS_INSUFICIENTES` | série menor que `VERTEX_MIN_CANDLES` |
| `VERTEX_SEM_VOLATILIDADE` | `lrange` zero (série travada) |
| `VERTEX_CONTRA_O_NIVEL` | direção conflita com suporte/resistência |
| `VERTEX_OTC_RECUSADO` | ativo `-OTC` com `VERTEX_ALLOW_OTC=false` |
