# Placar como contador único no banco

Pedido do dono (01/10/2026): "uma tabela no banco onde ficam os wins e losses do
placar; cada resultado soma, e o Reiniciar placar zera o contador".

## Por que (o ganho não é desempenho, é ter UMA verdade)

O placar exibido já era um contador (memória do runtime +1 a cada resultado); o
Histórico só é recontado no boot e no "Iniciar". O problema é que o mesmo placar
vive em **5 cópias**: memória do runtime, memória do gateway, snapshot Redis,
`robot_states` e o espelho `robot_trades`. Quase todo defeito de placar desde
agosto foi uma cópia discordando da outra (2x2 → 1x4, placar de ontem voltando,
operação apagada ressuscitando, gateway rebaixando), e cada correção somou uma
regra de reconciliação ("nunca rebaixa", marca de baixa intencional, carimbo de
dia, acumulado das 100 últimas). O contador troca tudo isso por uma linha no
banco alterada só por funções atômicas.

## Estrutura (`backend/migration_placar_contador.sql`)

- `placar`: uma linha por cliente — `wins`, `losses`, `profit` (o que o painel
  mostra), `stop_wins`, `stop_losses`, `stop_ganho`, `stop_perda` (só ordem real,
  para o Stop Win/Loss), `desde` (último Reiniciar), `versao`.
- `placar_lancamentos`: um lançamento por ordem contada, chave
  `(user_id, desde, order_id)` — é o que garante que **a mesma ordem nunca soma
  duas vezes**, mesmo com resultado atrasado, repetido ou chegando por dois
  processos ao mesmo tempo (testado com 30 chamadas simultâneas: contou 1).
- Funções (cada uma é uma transação, com a linha do cliente travada):
  - `placar_lancar` — soma o efeito de uma ordem, se ainda não somou.
  - `placar_apagar` — Shift+O: desfaz exatamente o que a ordem somou.
  - `placar_reiniciar` — "Reiniciar placar": zera e abre período novo.
  - `placar_vitrine` — Shift+O "gerar placar": mexe só na vitrine, nunca no stop.
  - `placar_semear` — carga inicial; não faz nada se o cliente já tem placar.

As regras de negócio continuam no robô (gale conta um ciclo, LOSS oculto do
LIVE, empate não conta, lucro do ciclo com gale): ele manda ao banco os mesmos
números que aplica ao placar. Pontos de lançamento em `AutoTrader`:
`finish_trade`, `trigger_gale` (perna perdida: só dinheiro), `close_abandoned_gale`
(chave `<ordem>#ciclo`, porque a perna da mesma ordem já entrou com o dinheiro) e
`count_late_result`.

## Fases

1. **Sombra** (`PLACAR_CONTADOR=sombra`, este código): o painel continua igual;
   o runtime lança cada resultado no contador, o gateway reinicia/apaga/ajusta a
   vitrine, o boot semeia cada cliente com o placar recém-restaurado. A auditoria
   de 15 em 15 min mostra "contador x placar (sombra)" — sem e-mail nesta fase.
   Critério para seguir: 2–3 dias sem divergência.
2. **Virada**: painel e stop leem do contador (snapshot do runtime carrega a
   linha; gateway lê a linha quando não há snapshot).
3. **Limpeza**: saem a recontagem no "Iniciar", o "nunca rebaixa", a marca de
   baixa intencional, o carimbo `score_day` e o acumulado das 100 operações.

## Como ligar

1. Rodar `backend/migration_placar_contador.sql` no SQL Editor do Supabase
   (produção). Conferir: `select count(*) from public.placar;` deve responder 0.
2. `PLACAR_CONTADOR=sombra` no `.env` do backend e subir gateway + runtime.
3. Conferir no log: `[PLACAR_CONTADOR_LIGADO]` nos dois processos e nenhum
   `[PLACAR_CONTADOR_SEM_TABELA]`. Se aparecer `SEM_TABELA`, a migration não
   rodou: o contador desliga sozinho e nada mais muda.

Sem a migration, ou com `PLACAR_CONTADOR=off` (padrão), o código novo não faz
nada.

## Limites conhecidos da fase 1

- Shift+O apagando uma ordem nos poucos segundos entre o resultado e o
  lançamento chegar ao banco: o apagar não acha o lançamento e o contador fica
  com a ordem. A auditoria mostra; na fase 2 o apagar passa pelo mesmo processo.
- Falha do banco por mais de ~8 s no lançamento: `[PLACAR_CONTADOR_PERDEU]`
  (ERROR, vai ao e-mail do vigia) e a auditoria mostra a divergência.

Testes: `tests/test_placar_contador.py` (funções do banco, escritor e a
invariante contador = placar do robô em vitória, derrota, empate, gale que vence,
gale abandonado, resultado atrasado/repetido, LOSS oculto do LIVE e uma
sequência de 150 ordens). O SQL foi aplicado e exercitado num Postgres 16.
