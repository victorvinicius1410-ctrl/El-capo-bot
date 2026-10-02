# Gerenciamento Consistente (Algoritmo de Masaniello)

Opção do robô, ligada/desligada como o gale. Com ela ligada o valor de cada
entrada deixa de ser o "Valor por entrada" fixo e passa a ser calculado pelo
Algoritmo de Masaniello a partir de um **capital do ciclo**. O plano também é
o stop: Stop Win, Stop Loss e gale não valem neste modo.

Estado em 02/10/2026: implementado no ramo `gerenciamento-consistente`, só no
sistema 02. Não está em produção.

## O que o cliente configura

| Campo | Regra |
|---|---|
| Capital do ciclo | Dinheiro em risco no ciclo. Mínimo: o que faz a 1ª entrada alcançar o mínimo da corretora (R$ 5 / US$ 1). Não pode passar do saldo ao abrir um ciclo. |
| Perfil | Conservador 10/4, Moderado 10/5, Agressivo 10/6 (operações / acertos), ou Personalizado (`1 <= W < N <= 100`). |

Meta e limite são calculados e mostrados só para leitura, em número e em valor:
`4 acertos = +R$ 10,58` e `7 erros = −R$ 100,00`.

## A conta

`C` capital, `N` operações, `W` acertos, `Q` cotação (payout 80% = 1,80).

- `M[m][w]` (m operações feitas, w acertos): `1` se `w = W`; `Q^(N−m)` se
  precisa acertar todas as restantes; senão `Q·a·b / (a + (Q−1)·b)` com
  `a = M[m+1][w]`, `b = M[m+1][w+1]`.
- Próxima entrada: `capital_atual × (1 − Q·b / (a + (Q−1)·b))`.
- Meta: `C × M[0][0]`. O ciclo acaba em `W` acertos ou em `N − W + 1` erros.

Conferido contra a planilha do dono (capital 100, 38 operações, 14 acertos,
payout 1,82: entradas 0,40 / 0,58 / 0,38 / 0,55). Esses números estão escritos
à mão em `tests/test_masaniello_engine.py`.

| Perfil | Meta (payout 80%) | 1ª entrada | Menor | Maior | P(meta) com 50% de acerto |
|---|---|---|---|---|---|
| Conservador | +10,6% | 6,8% | 0,5% | 61% | ~83% |
| Moderado | +33,3% | 15,4% | 1,3% | 74% | ~62% |
| Agressivo | +92,7% | 27,9% | 4,2% | 107% | ~38% |

**O algoritmo não cria acerto.** Esgotar os erros perde o capital do ciclo
inteiro; com acerto perto de 50% a expectativa é negativa em qualquer perfil.
A explicação do "?" diz isso e não pode passar a prometer lucro.

## Regras

| Situação | Regra |
|---|---|
| Payout da matriz | Fixo por ciclo = `min_payout` (80), o piso que o robô já exige. |
| Capital atual | Pelo lucro REAL devolvido pela corretora, nunca pelo payout cotado. |
| Entrada abaixo do mínimo da corretora | Sobe para o mínimo; a linha fica marcada (`adjusted_to_min`). Com capital pequeno o resultado final pode ficar acima ou abaixo da meta — o formulário avisa e diz a partir de que capital não há ajuste. |
| Capital que sobrou < mínimo | Ciclo encerra como perdido (`BUST` / `NO_CAPITAL`). |
| Empate | Linha "Empate", valor devolvido, não conta. |
| Resultado que não chegou | Não abre outra ordem. Passado o teto (`max(600 s, vela + 300 s)`) o robô para com `MASANIELLO_RESULT_PENDING` e o ciclo fica `ACTIVE`; o resultado atrasado entra na linha quando chegar. Iniciar antes disso encerra o ciclo e abre outro. |
| Fim do ciclo | Robô para e avisa (`STOP_WIN_HIT` / `STOP_LOSS_HIT`, reaproveitados). Só recomeça no Iniciar. |
| Parar e iniciar | Continua o ciclo se o plano é o mesmo; plano diferente = ciclo novo. |
| Reiniciar placar | Zera o placar; não mexe no ciclo. |
| Reiniciar ciclo (`/robot/reset-cycle`) | Apaga o ciclo junto com o Histórico. |
| Desligar o modo | Encerra o ciclo em andamento (`ABANDONED`). |
| Conta marketing | Pode usar (decisão do dono, 02/10): o ciclo só acompanha ordem real com a marca dele, então operação do Shift+O não o distorce. |
| Modo LIVE ligado | Gerenciamento suspenso (`masaniello_active` falso): o LIVE esconde loss e o ciclo não fecharia a conta. O robô volta ao valor fixo e aos stops de sempre; a configuração fica guardada, a chave aparece travada com o aviso e a calculadora some. |

## Onde está

Backend (`backend/backend/`):

- `masaniello.py` — motor puro (matriz, entrada, ciclo, `pick_freshest_cycle`).
- `auto_trader.py` — campos em `RobotState`; `masaniello_begin_if_needed`,
  `masaniello_next_order`, `_apply_masaniello_result`; o desvio em
  `resolve_robot_stop_reason` (todo ponto que avalia stop passa por ali).
- `main.py` — valor da ordem no laço de ordens; `masaniello_cycle_gate`;
  `validate_masaniello_config`; `POST /robot/masaniello/end-cycle`.
- `robot_runtime_main.py` — o runtime confere o ciclo no `start`.

Painel (`frontend/src/`):

- `lib/masaniello.ts` — gêmeo do motor, para a prévia.
- `lib/masanielloPresentation.ts` — textos e validação do formulário.
- `components/ConsistentManagement.tsx` — chave, campos, prévia, explicação.
- `components/MasanielloCalculator.tsx` — calculadora ao vivo
  (Configurações → Robô e Histórico).

Os dois motores leem os MESMOS vetores:
`backend/tests/fixtures/masaniello_vectors.json`. Mudou a conta em um, muda no
outro.

## Estado e quem escreve

Sem migration. Config e ciclo andam em `robot_states.state_json`
(`masaniello_enabled`, `masaniello_capital`, `masaniello_operations`,
`masaniello_wins`, `masaniello_profile`, `masaniello_cycle`). Cada operação
leva a marca `masaniello {cycle_id, seq, stake_planned, adjusted_to_min,
capital_before}` em `analysis_json`.

Gateway e runtime têm cada um a sua cópia do estado. O progresso do ciclo é
escrito pelo runtime; a cópia que vale é sempre a de maior `rev`
(`pick_freshest_cycle`), aplicada em quatro pontos:

1. Memória do gateway — `sync_masaniello_cycle_on_gateway` (lê o snapshot do
   Redis antes de gravar, publicar ou reconciliar).
2. Gravação — `_com_ciclo_mais_novo_do_banco` confere o banco na thread de
   escrita e nunca troca um ciclo mais novo por um mais velho.
3. `start` no runtime — `_hydrate_user_from_persistence` mantém o ciclo vivo
   quando ele está à frente do banco.
4. Restart — `AutoTrader._repair_masaniello_cycle` completa o ciclo com as
   operações do Histórico que levam a marca dele.

Nenhuma chave do ciclo pode começar com `ai`: `strip_ai_fields` apaga.

## Como conferir se está operando de verdade

Log intermediário não prova. O que prova é o valor que saiu:

- `[MASANIELLO_ORDER] ... stake=` no `robot-runtime`, seguido de
  `ORDER_SEND_SUCCESS`, com o mesmo valor na corretora.
- `[MASANIELLO_RESULT] ... capital=` a cada resultado.
- No Histórico, `amount` da operação = `masaniello.stake_planned` (ou o mínimo
  da corretora, com `adjusted_to_min=true`).

Outros marcadores: `[MASANIELLO_CYCLE_STARTED]`, `[MASANIELLO_CYCLE_NO_CAPITAL]`,
`[MASANIELLO_PAUSED_RESULT_PENDING]`, `[MASANIELLO_PAYOUT_BELOW_REF]`,
`[MASANIELLO_PERSIST_KEPT_DB]`, `[MASANIELLO_CYCLE_REPAIRED]`.

## Testes

```bash
docker run --rm --network none -e PYTHONDONTWRITEBYTECODE=1 \
  -v <árvore>/backend:/app -w /app elcapo2staging-backend \
  python -m unittest tests.test_masaniello_engine tests.test_masaniello_cycle tests.test_masaniello_gateway
```

Painel: `npm test` (inclui `masaniello.test.ts`, `masanielloPresentation.test.ts`
e `masanielloState.test.ts`).

## Limites conhecidos

- Corrida residual entre gateway e runtime na gravação do `state_json`
  (~100 ms): mitigada pelo `rev` e pelo conserto via Histórico, não eliminada.
- Canal digital/turbo pode pagar menos que o payout de referência: o ciclo
  fecha pela contagem e a meta pode ficar um pouco abaixo
  (`[MASANIELLO_PAYOUT_BELOW_REF]`).
- Sem repetição automática de ciclos.
