# Histórico de operações

Documento da tela `/history` (Histórico) do painel El Capo.

## Objetivo

Exibir operações finalizadas e indicadores agregados do robô no mesmo layout
para cliente, trial e marketing.

## Filtros de período

Seletor no estilo do Gerenciador de Anúncios da Meta (`DashboardDateFilter`):
presets + calendário de dois meses + **Atualizar**.

O `days` enviado a `GET /robot/history` é o recuo até hoje (máx. 90). A tabela
e os cards filtram no cliente o intervalo escolhido (hoje, ontem, mês passado,
personalizado).

Detalhes: [`FILTRO_DATAS.md`](./FILTRO_DATAS.md).

## Cards de resumo

Win Rate · Wins · **Loss** · Total Trades · Lucro Total · Profit Factor.

O rótulo de derrotas é **Loss** (não “Losses”).

## Tabela de operações

Colunas: Data, Ativo, Direção, **Estratégia**, **Análise**, Valor, Resultado,
Lucro/Prejuízo, Gale, Gale Step, Conta.

- **Estratégia:** `strategy_name` / `strategy_key` vindos de `analysis_json`
  (ex.: “Retração em Zonas de Suporte e Resistência”).
- **Análise:** botão “Ver análise” abre modal com `strategy_summary`,
  `speech_preview` e `analysis_detail` (explicação completa da entrada).

Telemetria persistida em `robot_trade_history.analysis_json` via
`TRADE_ANALYSIS_FIELDS` (`robot_persistence.py`). SQL opcional para colunas
denormalizadas: `backend/migration_named_strategies_analysis.sql`.

Ver também: [`ESTRATEGIAS_NOMEADAS.md`](./ESTRATEGIAS_NOMEADAS.md).

## Gale (martingale): cada entrada é uma linha própria

Quando o Gale está ativo (`martingale_steps > 1`) e a primeira entrada perde,
o robô lança uma nova ordem (valor multiplicado) na mesma direção. As **duas**
entradas ficam registradas como linhas **separadas** no histórico, cada uma
com o `Resultado` real dela — a primeira sempre `LOSS`, a segunda o resultado
real da corretora (`WIN`/`LOSS`/`DRAW`).

**Importante:** o *badge* de resultado do overlay/placar ao vivo (Visão geral)
reflete só o resultado da **última** entrada do ciclo (`cycle_result`), não o
lucro líquido do ciclo. Ou seja: 1ª entrada `LOSS` + Gale `WIN` mostra "WIN" no
overlay mesmo que o multiplicador do Gale não cubra 100% da perda anterior
(depende do payout do ativo no momento). Para conferir o resultado líquido
real de uma sequência de Gale, some a coluna **Lucro/Prejuízo** das duas
linhas na tabela — não olhe só o rótulo **Resultado** da última.

Isso já gerou relatos de lead ("a operação foi loss e apareceu win") quando na
verdade era uma sequência Gale sendo lida como uma operação só. Ver também a
investigação de isolamento de sessão no resultado da ordem em
[`ROBO_E_SUPORTE.md`](./ROBO_E_SUPORTE.md) §9 (2026-08-07).

## Conta marketing + Shift+O

Quando a conta é `marketing` em simulação **e** o painel Shift+O está aberto:

1. Aparece a coluna **Ações** (sticky à esquerda) com botão de excluir — em
   telas estreitas a lixeira permanece visível sem rolar a tabela até o fim.
2. A exclusão chama `DELETE /marketing-simulation/trades/{id}` e invalida
   histórico/placar do robô e do painel.
3. Texto de ajuda no lead: “Shift+O ativo: você pode excluir operações abaixo.”
4. No painel Shift+O, use a aba **Histórico** (ou o atalho “Ver histórico”
   nas abas Manual/Placar) para editar/excluir sem depender de scroll longo.

O `{id}` pode ser:

- o **UUID** de `marketing_simulated_trades` (operações geradas/editadas no
  Shift+O, após sync o `order_id` do `/robot/history` é esse UUID);
- o **order_id numérico da Bullex** (operações ao vivo listadas no Histórico).

IDs não-UUID **não** são enviados como filtro da coluna UUID do Supabase
(isso gerava HTTP 400 → 500 e o toast “Failed to fetch”). Nesse caso o backend
procura o espelho por `broker_order_id` e, se não achar, remove a operação
direto das fontes do robô (ver “As três fontes do histórico” abaixo) e ajusta
o placar da sessão.

O `DELETE` é **idempotente** (`204` mesmo se a operação já não existir), para
não exibir `SIMULATED_TRADE_NOT_FOUND` em duplo clique ou cache stale.

## As três fontes do histórico

`GET /robot/history` (`load_robot_history_items`) **mescla** duas fontes, e uma
terceira as realimenta. Excluir em só uma delas faz a operação reaparecer no
próximo carregamento da tela (F5) ou depois de um restart:

| Fonte | Papel | Como é limpa na exclusão |
|-------|-------|--------------------------|
| `robot_trade_history` | Lista canônica persistida | `delete_trade_history_item` / `clear_trade_history` |
| `auto_trader._histories` | Operações da sessão em memória, mescladas no GET | `remove_history_trade` / `replace_history` |
| `robot_trades` | Espelho lido pelo restore, que repovoa a memória | `delete_trade` / `clear_finished_trades` |

Regra: **toda** exclusão ou sincronização de histórico precisa alinhar as três.

- `delete_marketing_robot_history_item` remove a operação nas três.
- `sync_marketing_display_to_robot` faz **upsert** das operações simuladas
  em `robot_trade_history` e **mescla** a memória do `auto_trader`. Não chama
  mais `clear_trade_history` / `clear_finished_trades` — o histórico antigo
  permanece. O overlay recebe o placar do lote (gerar) ou soma (criar).

## Operação ao vivo espelhada no Shift+O

Operações reais da conta marketing são espelhadas em
`marketing_simulated_trades` guardando o `order_id` da corretora em
`broker_order_id` (`backend/migration_marketing_broker_order_id.sql`).

Assim a sincronização reusa esse `order_id` no histórico do robô, em vez do
UUID do espelho. Sem o vínculo, a mesma operação aparecia duas vezes (uma por
identificador) e excluir uma delas deixava a outra na tela.

O backend funciona antes da migration ser aplicada — grava o espelho sem o
vínculo e a exclusão cai no fallback por `order_id` — mas a duplicidade só
desaparece depois que ela roda.

Estado do painel: `MarketingPanelProvider` / `useMarketingPanel`
(`marketingPanelContext.tsx`), ligado no `AppShell`.

## Arquivos

- `routes/_authenticated/history.tsx`
- `hooks/useRobotHistory.ts`
- `lib/marketingPanelContext.tsx`
- Relacionado: [`MARKETING_SIMULATION.md`](./MARKETING_SIMULATION.md)

## Reiniciar placar

O botão **Reiniciar placar** (`POST /robot/reset-score`) zera só o placar
da sessão no overlay (wins / losses / lucro). A tela **Histórico** e as
tabelas persistidas **não** são apagadas.

Para limpar histórico junto com o ciclo, use o fluxo de
`POST /robot/reset-cycle` (não o botão de placar).

## Histórico

- **2026-08-16 (simular sem apagar histórico)** — Generate/create de marketing
  deixam de zerar `robot_trade_history`. Ver `MARKETING_SIMULATION.md`.
- **2026-08-07 (revisão visual WIN/LOSS + isolamento de sessão)** — Leads
  relataram operação perdida aparecendo como WIN. Auditoria de 500 operações
  reais não achou `result`/`profit` inconsistentes, mas achou e corrigiu uma
  falha de desenho real (`socket_option_closed`/`order_binary` globais entre
  sessões no `bullex-service` — ver `ROBO_E_SUPORTE.md` §9 e
  `PERFORMANCE_SISTEMA.md`). Documentado também que o badge do overlay em
  sequências de Gale reflete só a última entrada, não o líquido do ciclo
  (ver seção "Gale" acima).
- **2026-08-03 (lixeira em tela pequena)** — Shift+O ganhou abas Manual /
  Placar / Histórico; coluna Ações sticky à esquerda em `/history`. Ver
  `MARKETING_SIMULATION.md`.
- **2026-07-29 (exclusão que voltava no F5)** — Operação apagada reaparecia ao
  recarregar a tela: o DELETE atualizava só parte das fontes. Agora sync e
  exclusão alinham `robot_trade_history`, `robot_trades` e a memória do
  `auto_trader`; o espelho ao vivo guarda `broker_order_id` (fim da duplicação
  order_id × UUID). Testes: `test_marketing_history_deletion_persistence`.
- **2026-07-26 (reset placar)** — `POST /robot/reset-score` deixa de apagar
  `robot_trade_history` / `robot_trades` / simulações marketing; só zera
  o placar visual. Ver `ROBO_E_SUPORTE.md` §3.
- **2026-07-26** — Colunas Estratégia + Análise no histórico; modal com
  detalhe da estratégia nomeada (`strategy_key`, `analysis_detail`,
  `speech_preview`). Ver `ESTRATEGIAS_NOMEADAS.md`.
- **2026-07-25** — Exclusão de operações ao vivo: remove também o fantasma do
  histórico em memória (`auto_trader`); DELETE idempotente (fim do toast
  `SIMULATED_TRADE_NOT_FOUND` em re-clique). Espelho ao vivo não usa mais
  `order_id` Bullex como `synthetic_sequence` (overflow INTEGER → 400).
- **2026-07-24 (noite, placar offline)** — Com a tela fechada o WIN/LOSS
  sumia em 5s e o usuário voltava só vendo “analisando”. Agora o backend
  marca `unseen_result` e o overlay/placar/narração mostram o resultado e
  o valor ao reabrir (~8s). Restore hidrata trades a partir do histórico
  se `robot_trades` estiver vazio. Ver `ROBO_E_SUPORTE.md` §3.
- **2026-07-24** — Corrige exclusão de operações ao vivo (order_id Bullex):
  fallback para `robot_trade_history` + validação UUID no Supabase (fim do
  “Failed to fetch” / 500).
- **2026-07-23** — Label Losses → Loss; exclusão de operações com Shift+O aberto.
