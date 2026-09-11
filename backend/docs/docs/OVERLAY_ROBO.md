# Overlay do robô — visual e assets

Documento do avatar animado no dashboard (`RobotOverlay` + `RobotAvatarVideo`).
Atualizado em **2026-08-07**.

## 1. Visual oficial

| Item | Valor |
|---|---|
| Arquivo | `frontend/public/robo-wink-orig.webm` (lima original) |
| Alias | `frontend/public/robo-wink.webm` (mesmo MD5) |
| Fonte | `frontend/public/robo-wink-lime-source.webm` |
| URL | `/robo-wink-orig.webm` (`ROBOT_AVATAR_WEBM_SRC`) |
| Componente UI | `RobotOverlay.tsx` |
| Componente avatar | `RobotAvatarVideo.tsx` |
| Constantes | `lib/robotAvatarVisual.ts` |
| CSS | `.robot-avatar-video` + `.robot-avatar-source` |

O WebM é o **lima original**. O roxo/teal vem do filtro CSS:

```
hue-rotate(175deg) saturate(1.35) brightness(1.03) contrast(1.12)
+ drop-shadow teal
```

### Pipeline híbrido (Windows sem borda + Mac/celular roxo)

| Ambiente | Pipeline | Motivo |
|---|---|---|
| Windows / Chrome / Edge / Firefox / Android | `<video>` + filtro CSS | Visual original do El Capo; canvas criava “borda/quadro” |
| Safari macOS / iPhone / iPad | vídeo oculto → `<canvas>` + mesmo CSS | WebKit ignora `filter` em `<video>` (ficava lima) |

Seleção: `resolveRobotAvatarPipeline()` / `isRobotAvatarVideoFilterUnreliable()`.

### Por que não usar canvas em todos

O canvas materializa o bitmap 512×512 opaco. No Windows isso aparecia como
**borda/fundo quadrado** atrás do personagem. O `<video>` com filtro CSS
compõe de forma limpa no Chrome — é o visual que o painel sempre teve.

### Por que não bake classic

O bake roxo (`robo-wink-classic.webm`) pinta o fundo 512×512 com
gradiente roxo/azul — no Chrome/Windows o quadro fica óbvio.

Com lima + `hue-rotate` no vídeo:

- pixels quase pretos do fundo **continuam escuros**;
- no painel charcoal/teal o quadrado **some**;
- só o personagem lima vira roxo/azul.

## 2. CSS oficial

```css
.robot-avatar-source {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
  overflow: hidden;
}

.robot-avatar-video {
  filter: hue-rotate(175deg) saturate(1.35) brightness(1.03) contrast(1.12)
    drop-shadow(0 0 14px rgba(37, 219, 224, 0.42))
    drop-shadow(0 0 6px rgba(143, 176, 184, 0.24));
  -webkit-filter: /* mesmo */;
}
```

Constantes: `ROBOT_AVATAR_COLOR_FILTER` / `ROBOT_AVATAR_CSS_FILTER`.

## 3. Cache

- URL versionada (`-orig`) para furar cache do Windows após o bake clássico.
- Nginx: `.webm` com 1h `must-revalidate` (não immutable 1 ano).

No Windows: **Ctrl+F5** após deploy.
No Safari/iOS: recarregar a página (ou limpar cache do site).

## 4. O que NÃO fazer de novo

- WebM com fundo roxo bake opaco (`-classic`) — reaparece o quadrado no Windows.
- Recorte alpha agressivo (“flat”) — corta o personagem.
- `hue-rotate` em cima de WebM **já** roxo — vira amarelo no Chrome.
- Drop-shadow roxo circular extra — reforça “roda” artificial.
- Forçar canvas em **todos** os browsers — reaparece borda no Windows.
- Só `<video>` sem canvas no Safari — Mac/celular voltam ao lima.

## 5. Saldo insuficiente no overlay (2026-08-11)

Quando a Bullex rejeita a compra com `Insufficient funds` (ou o start
detecta saldo zero / entrada maior que o saldo), o robô **para** com
status `INSUFFICIENT_BALANCE` e o overlay mostra:

- Título: **Saldo insuficiente**
- Detalhe: depósito ou redução do valor da entrada

Arquivos:

| Camada | Arquivo | Papel |
|---|---|---|
| Runtime | `backend/main.py` | `is_insufficient_funds_error` + stop no buy fail |
| UI texto | `lib/robotPresentation.ts` | `looksLikeInsufficientBalance` |
| API codes | `lib/api.ts` | `INSUFFICIENT_BALANCE` / `INSUFFICIENT_FUNDS` |

Antes: rejeição virava só `ORDER_REJECTED` e o ciclo seguia “analisando”
sem aviso claro.

## 6. Placar WIN/LOSS/Resultado (2026-08-11)

| Problema | Correção |
|---|---|
| Snapshot Redis TTL curto (120s) + gateway `external` sem worker → placar 0 | TTL 600s; `rehydrate_score_from_persistence_if_blank` no snapshot/HTTP |
| `GET /robot/state` no gateway ignorava snapshot do runtime | Prefere `robot_bus.get_snapshot` em modo external |
| Badges só com glow, sem fundo — sumiam no dashboard | `ScoreBadge` / `ProfitBadge` com borda + fundo escuro |

Testes: `robotPresentation.insufficient.test.ts`,
`backend/tests/test_insufficient_funds_and_score.py`.

## 7. Nota Mac/Safari / celular

| Antes (bug) | Agora |
|---|---|
| Safari ignora filter no `<video>` → lima | Canvas + mesmo CSS → roxo/teal |
| Windows com canvas forçado → borda | Windows de volta ao `<video>` limpo |

Arquivo clássico bake (`robo-wink-classic.webm`) fica no repo só como
referência, **não** é a URL ativa.

Testes: `frontend/src/lib/robotAvatarVisual.test.ts` + bloco em
`robotNarration.test.ts`.

## 8. Flash de WIN/LOSS + ativo (2026-08-15)

Depois que a operação fecha, o overlay mostra **WIN** ou **LOSS** e o **ativo**
por no máximo **60 segundos**. Embaixo, se o robô segue ligado, aparece
**Buscando melhor oportunidade**. Passado 1 minuto, o resultado e o ativo
somem e fica só a análise.

| Camada | Constante | Papel |
|---|---|---|
| Ciclo operacional | `result_display_until` = **5s** | Libera `prepare_cycle` (não esticar) |
| Overlay (UI) | `RESULT_OVERLAY_DISPLAY_MS` = **60s** | Cap visual a partir de `last_trade.finished_at` |
| Payload unseen | `RESULT_OVERLAY_DISPLAY_SECONDS` = **60** | Âncora em `finished_at`, **não** `now+60` a cada serialize |

Problemas que prendiam o sinal na tela:

1. O WebSocket (`robot_panel_maintenance`) não chamava `acknowledge_unseen_result`
   (só o GET `/robot/state`). Com poll HTTP pausado, `unseen_result` ficava true.
2. `to_dict` preenchia `result_display_until = now+60s` **em todo snapshot**,
   renovando o WIN/LOSS para sempre.
3. O título **LOSS no Gale** usava `last_trade` mesmo depois da análise voltar.

Arquivos: `frontend/src/lib/robotPresentation.ts`, `RobotOverlay.tsx`,
`backend/auto_trader.py`, `backend/main.py`.

Testes: `robotPresentation.resultFlash.test.ts`,
`tests/test_unseen_result_offline.py`.

## 9. Histórico

- **2026-08-15 (flash WIN/LOSS 60s)** — Overlay limita resultado+ativo a 1 min
  e mostra “Buscando melhor oportunidade” embaixo. Ver §8.
- **2026-08-14 (placar 0-0 no start/stop)** — Snapshot de controle zerava
  WIN/LOSS no overlay ao iniciar/parar. Ver `PLACAR_OVERLAY.md`.
- **2026-08-11 (saldo insuficiente + placar)** — Aviso fixo no overlay quando
  a compra REAL falha por fundos; placar reforçado (reidratação + badges
  com contraste). Ver §5 e §6.
- **2026-08-07 (noite+ — Confirmar e iniciar)** — Botão do pop-up reforçado
  (`stopPropagation`, `data-testid`, validação de `enabled`). Start com
  sessão Bullex morta deixa de fingir “sem saldo”. Ver `ROBO_E_SUPORTE.md`
  e `MARKETING_SIMULATION.md`.
- **2026-08-07 (madrugada — Iniciar Operação “não abre” no flutuante)**
  - No mobile, abrir o `StartOperationDialog` de forma síncrona no `onClick`
    fazia o mesmo toque cair no overlay Radix e **fechar o modal na hora**
    (Configurações → Robô seguia ok porque inicia sem diálogo).
  - Correção: `scheduleDialogOpen` (`setTimeout(0)`), grace de ~450ms contra
    dismiss externo no diálogo, `interactionLocked` no overlay enquanto o
    modal está aberto, e Dialog em `z-[100]` (acima do robô z-50/60 e
    WhatsApp z-60). Ver `ROBO_E_SUPORTE.md`.
- **2026-08-07 (noite)** — **Iniciar Operação** no overlay sempre abre o
  diálogo (não bloqueia por `connected` stale pós-stop/backoff). Ver
  `ROBO_E_SUPORTE.md` e `STOP_WIN_LOSS.md`.
- **2026-07-31 (borda Windows)** — Pipeline híbrido: `<video>` no
  Chrome/Windows (remove borda do canvas); canvas só no Safari/iOS.
- **2026-07-31 (cross-platform)** — `RobotAvatarVideo` + canvas para
  Safari/iOS. Constantes em `robotAvatarVisual.ts`.
- **2026-07-31** — Remove botão **Config** do rodapé do overlay: a
  configuração operacional fica só em **Iniciar operação**. Stops com modo
  valor/operações: `STOP_WIN_LOSS.md`.
- **2026-07-25 (backup restore)** — Volta ao lima + CSS `hue-rotate` do
  backup; remove fundo quadrado no Windows causado pelo bake clássico.
- **2026-07-25 (classic)** — Tentativa de bake roxo para Mac; Windows
  mostrou quadrado roxo/azul.
- **2026-07-25** — Tentativas de alpha/flat/cache-bust.
- **Original** — lima + `hue-rotate(175deg)` só no CSS (backup).
