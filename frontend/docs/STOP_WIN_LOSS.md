# Stop Win / Stop Loss — modos por valor e por operações

Documento da regra de parada do robô (Stop Win e Stop Loss).
Atualizado em **2026-08-07**.

## Objetivo

No diálogo **Iniciar operação** (e no painel Configurações → Robô), cada
stop pode ser configurado de **duas formas**:

| Modo | Stop Win | Stop Loss |
|---|---|---|
| **Por valor** (`money`) | Para quando o lucro bruto da sessão ≥ valor em R$ | Para quando a perda bruta da sessão ≥ valor em R$ |
| **Por operações** (`operations`) | Para quando o placar de **WIN** ≥ quantidade | Para quando o placar de **LOSS** ≥ quantidade |

O botão **Config** do overlay do robô foi removido: a configuração operacional
fica só em **Iniciar operação** (e no painel Configurações → Robô).

## Campos

| Campo (API / estado) | Tipo | Default | Descrição |
|---|---|---|---|
| `stop_win` / `stopWin` | float ≥ 5 | 50 | Meta de lucro em R$ (modo `money`) |
| `stop_loss` / `stopLoss` | float ≥ 5 | 30 | Limite de perda em R$ (modo `money`) |
| `stop_win_mode` / `stopWinMode` | `money` \| `operations` | `money` | Como o Stop Win é avaliado |
| `stop_loss_mode` / `stopLossMode` | `money` \| `operations` | `money` | Como o Stop Loss é avaliado |
| `stop_win_operations` / `stopWinOperations` | int ≥ 1 | 5 | Quantidade de WINs para parar |
| `stop_loss_operations` / `stopLossOperations` | int ≥ 1 | 3 | Quantidade de LOSSes para parar |

Valores monetários continuam persistidos mesmo no modo `operations`, para a
pessoa voltar ao modo “por valor” sem perder o número digitado.

## Avaliação (backend)

Helper: `resolve_robot_stop_reason` em `backend/auto_trader.py`.

Ordem de prioridade: **Stop Loss primeiro**, depois Stop Win (mais
conservador).

### Modo `money`

- Usa lucro/perda brutos do dia desde o último `stop_reset_at`
  (`management_totals` / `build_management_summary`), igual ao comportamento
  anterior.
- Em `robot_stop_reason` (checagem rápida no ciclo): `state.profit` da sessão.

### Modo `operations`

- Stop Win: `state.wins >= stop_win_operations`
- Stop Loss: `state.losses >= stop_loss_operations`
- O placar é o mesmo do overlay (zera com **Reiniciar placar** /
  `POST /robot/reset-score`).

Ao atingir o stop:

1. Status vira `STOP_WIN_HIT` ou `STOP_LOSS_HIT`
2. Worker pausa (`pause_by_stop` / `ROBOT_PAUSED_BY_STOP`)
3. Overlay mostra a mensagem de stop; o robô fica parado (`enabled=false`)

### Como religar depois do stop

1. Clique em **Reiniciar placar** (`POST /robot/reset-score`) — zera
   wins/losses/profit **e** limpa o status `STOP_*_HIT` → `STOPPED`.
2. Clique em **Iniciar Operação** (overlay) ou **Iniciar robô**
   (Configurações) de novo.

Sem o passo 1, `POST /robot/start` responde `409 RESET_CYCLE_REQUIRED`
(enquanto o placar ainda viola o stop). Se o placar já estiver zerado mas o
status `STOP_*` ficou órfão, o start limpa o status sozinho
(`[STOP_STATUS_CLEARED_ON_START]`).

O overlay **abre o diálogo** Iniciar Operação mesmo com status `STOP_*`
(igual ao fluxo das Configurações). O bloqueio real fica no backend; se o
placar ainda viola o stop, a API devolve erro amigável. Não há toast
prévio no overlay pedindo “reinicie o placar” antes de abrir o diálogo —
isso travava o robô flutuante enquanto Configurações ainda iniciava.

## Frontend

- `StartOperationDialog`: toggle **Por valor** / **Por operações** em cada stop
- `RobotControlPanel`: mesmos controles (Configurações → Robô)
- Overlay (`FloatingRobot` / `AppShell`): botão **Config** removido; clique em
  **Iniciar Operação** abre o mesmo `StartOperationDialog` das configurações
  (sem gate local por `STOP_*`). Conexão Bullex é checada no clique (toast);
  o botão não fica desabilitado só por `connected` (alinhar com
  Configurações → Robô). Regra: `canStartRobotOperation` em
  `bullexConnection.ts`.
- Persistência local da última operação (`elcapo:last-operation-config:*`)
  inclui os modos e as quantidades

Arquivos:

- `frontend/src/lib/robotSettings.ts`
- `frontend/src/lib/bullexConnection.ts`
- `frontend/src/components/StartOperationDialog.tsx`
- `frontend/src/components/RobotControlPanel.tsx`
- `frontend/src/components/AppShell.tsx`
- `frontend/src/hooks/useRobotSettings.ts`
- `frontend/src/hooks/useLiveTradingData.tsx`
- `backend/auto_trader.py`
- `backend/main.py`

## Validação

| Modo | Regra |
|---|---|
| `money` | Stop Win/Loss ≥ R$ 5 (`MIN_STOP_MONEY` / `STOP_MONEY_MIN`) |
| `operations` | Quantidade ≥ 1 (`STOP_OPERATIONS_MIN`) |

No `POST /robot/config`, a checagem de mínimo monetário só aplica quando o
modo correspondente é `money`.

## Testes

- Frontend: `robotSettings.test.ts` (normalize + parse de modos/ops)
- Frontend: `bullexConnection.test.ts` (`canStartRobotOperation`)
- Backend: `tests/test_stop_modes.py` (resolve por valor e por quantidade)
- Backend: `tests/test_robot_reset_cycle.py` (reset-score libera start após stop)

## Histórico

- **2026-08-13 (reiniciar placar em mode=external)** — Snapshot Redis + cmd
  `reset_score` ao runtime + cache do painel; evita placar “voltar” após
  o clique. Ver `REINICIAR_PLACAR.md`.
- **2026-08-07 (madrugada)** — Diálogo do flutuante: abertura adiada +
  z-index/`interactionLocked` para o modal não fechar no mesmo toque
  (mobile). Ver `OVERLAY_ROBO.md` / `ROBO_E_SUPORTE.md`.
- **2026-08-07 (noite — start sem bloqueio connected)**
  - Overlay, diálogo e Configurações → Robô não bloqueiam mais o start por
    `connected` do poll (backoff/cache falso). Aviso amarelo no diálogo;
    a API decide. Ver `ROBO_E_SUPORTE.md`.
- **2026-08-07 (noite)** — Overlay alinhado às Configurações: remove toast que
  bloqueava abrir o diálogo com `STOP_*`; `canStartRobotOperation` não
  desabilita o botão por `connected` (checagem no clique).
- **2026-08-07 (noite — marketing)** — Start em conta marketing auto-reseta
  placar quando Stop Win/Loss (incl. histórico Shift+O / `daily_stop_reason`)
  bloquearia; evita 403 `STOP_*_HIT`. Ver `MARKETING_SIMULATION.md`.
- **2026-08-07** — `reset_score` limpa `STOP_WIN_HIT`/`STOP_LOSS_HIT`; start
  libera status órfão com placar zerado; toast/mensagens amigáveis para
  `RESET_CYCLE_REQUIRED` / stop hit. Corrige “Iniciar Operação não vai”
  após Stop Win com placar reiniciado.
- **2026-07-31** — Dois modos (valor / operações); remove Config do overlay.
- **2026-07-24** — Mínimo R$ 5 para stop win/loss por valor.
- **2026-07-21** — Inputs liberados para digitar valores baixos sem reset.
