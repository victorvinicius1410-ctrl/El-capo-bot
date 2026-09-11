# Narração e overlay do robô — textos de análise contínua

Documento das frases faladas/exibidas enquanto o El Capo monitora o mercado.
Atualizado em **2026-08-07**.

## 1. Ao iniciar a operação

O áudio gravado legado (`/robot-voiceover.mp3`, que falava em “pode demorar
mais de 5 minutos / aguarde”) **não é mais usado** no start.

Ao ligar o robô, a narração TTS diz:

1. **“El Capo está analisando o mercado.”**
2. **“Identificando uma oportunidade de operação lucrativa.”**

Constantes: `ROBOT_START_NARRATION_LINES` / `ROBOT_START_NARRATION_TEXT` em
`frontend/src/lib/robotNarration.ts`. Player: `useRobotNarrator` →
`playStartVoiceover` (SpeechSynthesis, sem MP3).

### Pronúncia “El Capo” (não “El Cepo”)

O SpeechSynthesis pt-BR costuma ler **“Capo”** como **“Cepo”**. Na **UI** o
texto permanece **“El Capo”**. Na **voz**, `sanitizeForSpeech` reescreve para
**“El Kápo”** (`BRAND_NAME_SPEECH`), forçando a sílaba correta.

Arquivo: `frontend/src/lib/robotNarration.ts` (`sanitizeForSpeech`).

## 1b. Voz no macOS / Safari / celular (atualizado 2026-08-07)

O narrador **prefere** voz **masculina** pt-BR (Felipe, Antonio, Thalysson,
Eloquence Reed/Eddy/Rocko). Se não houver masculino instalado, usa **fallback
pt-BR** (ex.: Luciana no Safari stock, Google português no Chrome) com pitch
mais grave — **nunca fica mudo** só por falta de voz masculina.

### Por que o MacBook ficava mudo

1. Política antiga: sem voz masculina → silêncio (Safari stock só tem Luciana).
2. Bloqueio de `"google"` no seletor → Chrome Mac/Android sem fala.
3. Safari descarta `speak()` logo após `cancel()` (race).
4. Safari/iOS exige **gesto do usuário** antes do TTS (unlock).
5. Utterance coletada pelo GC do Safari se não houver referência viva.

### Correção vigente (2026-08-07)

| Peça | Comportamento |
|---|---|
| `voiceIdentity` | Concatena `name` + `voiceURI` |
| `pickNarratorVoice` | 1º masculino; 2º qualquer pt-BR (`scoreVoiceFallback`) |
| `speechPitchForVoice` | `0.85` male · `0.68` fallback |
| `unlockSpeechSynthesis` | Warm-up no clique de **Iniciar operação** |
| `speakUtterance` | Delay `60ms` após `cancel()` + `utteranceRef` |
| Bloqueios | Só motores de **tradução** (`tradutor` / `translate`) — **não** bloqueia Google TTS nativo |
| Cache | Re-resolve no `voiceschanged` |

Arquivos: `frontend/src/lib/robotNarration.ts`, `frontend/src/hooks/useRobotNarrator.ts`,
`frontend/src/components/AppShell.tsx` (`narrator.unlockAudio?.()` no start).

### Bug 2026-08-07 — `unlockAudio is not a function`

Sintoma (Safari/macOS): clique em **Iniciar Operação** no flutuante gera
`TypeError: unlockAudio is not a function` e o pop-up **não abre**.

Causa: `AppShell` chamava `narrator.unlockAudio()`, mas `useRobotNarrator`
tinha perdido o método (e `unlockSpeechSynthesis`) numa regressão.

Correção: restaurar `unlockSpeechSynthesis` + `unlockAudio`, e no AppShell
usar `narrator.unlockAudio?.()` para o clique nunca morrer se o método
faltar de novo.

### Celular (iOS / Android)

- **iOS Safari/Chrome:** mesmo unlock por gesto + delay pós-cancel; vozes Apple.
- **Android Chrome:** usa “Google português do Brasil” no fallback (deixou de ser bloqueada).
- Aba em segundo plano: `document.visibilityState !== "visible"` pausa novas falas (igual desktop).

### Voz masculina opcional (melhor qualidade no Mac)

1. **Ajustes do macOS** → Acessibilidade → Conteúdo falado → Voz do sistema
2. **Gerenciar vozes** → Português (Brasil) → baixar **Felipe**
3. Recarregar o painel (`Cmd+Shift+R`)

Sem Felipe o Capo **ainda fala** (fallback); com Felipe a voz fica masculina.

Testes: `frontend/src/lib/robotNarration.test.ts` (bloco macOS/Safari + hamburger).

## 1c. Entrada: estratégia + balão do El Capo (2026-07-26)

Quando há `pending_signal` (`SIGNAL_FOUND` / janela de entrada / compra):

1. **TTS** fala a estratégia (`strategy_name`), o motivo resumido
   (`strategy_summary` / `analysis_detail`) e o `speech_preview`
   (“Vou de CALL por retração em suporte.”).
2. **Balão** acima do avatar mostra o `speech_preview` (clicável).
3. **Clique no balão** abre popup com estratégia + explicação completa
   (`analysis_detail`).

Campos vêm de `backend/named_strategies.py` → `pending_signal` →
`RobotSignal` no frontend. Ver `ESTRATEGIAS_NOMEADAS.md`.

## 2. Durante a análise (sem operação aberta)

| Canal | Texto |
|---|---|
| Narração (voz) | **“El Capo está analisando o mercado.”** (falado como “El Kápo…”) |
| Narração (voz, seguimento) | **“Identificando uma oportunidade de operação lucrativa.”** |
| Overlay / status | **“El Capo está analisando o mercado”** |
| Detalhe / rodapé | **“Buscando melhor oportunidade”** |
| `analysis_message` / `status_message` | **“Buscando melhor oportunidade”** |
| `display_countdown_label` | **“Buscando melhor oportunidade”** |
| `display_countdown_seconds` | **0** (sem timer mm:ss) |

**Não** exibir/falar mais: “Próxima análise em mm:ss”, “Aguardando próxima
análise”, “iremos analisar em X minutos”, nem “a análise pode demorar mais
de 5 minutos”.

## 3. Onde está no código

| Camada | Arquivo | Pontos |
|---|---|---|
| Start TTS | `frontend/src/hooks/useRobotNarrator.ts` | `playStartVoiceover` |
| Frases + voz | `frontend/src/lib/robotNarration.ts` | `ROBOT_START_NARRATION_*`, `sanitizeForSpeech`, `pickNarratorVoice`, `voiceIdentity` |
| Backend estado | `backend/auto_trader.py` | `ANALYSIS_MESSAGE`, `SEEKING_OPPORTUNITY_LABEL`, `to_dict()` |
| Backend payload | `backend/main.py` | `build_robot_payload` para `WAITING_NEXT_CYCLE` |
| Overlay | `frontend/src/lib/robotPresentation.ts` | `waitingNextCyclePresentation` |
| Visual avatar | `docs/OVERLAY_ROBO.md` + `RobotAvatarVideo.tsx` | lima → canvas + CSS `hue-rotate` (Windows=Mac=celular) — ver `OVERLAY_ROBO.md` |
| Countdown live | `frontend/src/lib/robotState.ts` | label “buscando” → 0 |

## 4b. Placar WIN/LOSS — por que só a 1ª operação falava (2026-07-28)

Sintoma: El Capo anunciava “Fechou no win/los… Placar…” só na **primeira**
operação da sessão; nas seguintes o placar atualizava na UI mas a voz ficava muda.

Causa:

1. Com o painel **online**, `finish_monitored_trade` zerava `unseen_result`.
2. O status `WIN`/`LOSS` durava só **5s** (`result_display_until`).
3. O narrador só fala o placar nesses status (ou com `unseen_result`).
4. Na 1ª op a análise inicial é suprimida → pouca fala concorrente → pega a janela.
5. Nas ops seguintes o TTS ainda fala análise/entrada (“operação aberta…”) e,
   ocupado, **perde os 5s** → status já virou `WAITING_NEXT_CYCLE` → silêncio.

Correção **descartada** (1ª tentativa, 2026-07-28 — reprovada, ver §4c): esticar
`result_display_until` para 12s, `acknowledge_unseen_result` para 15s e marcar
`unseen_result=True` sempre. Funcionou para a voz, mas mexeu no ciclo operacional.

Correção **vigente** (2026-07-29): canal de voz próprio — §4c.

| Camada | Mudança mantida |
|---|---|
| Frontend `robotNarration` | Eventos `RESULT\|…` primeiro na fila; chave única por `order_id` |
| Frontend `useRobotNarrator` | Preempta fala menor quando chega placar; watchdog 25s; resume Chrome |

## 4c. Canal `result_voice` — fala do placar sem tocar no ciclo (2026-07-29)

Sintoma reportado depois da 1ª tentativa: o robô passou a **pegar operações com
resultado ruim** e a **anunciar entradas que não executava**.

Medição (Supabase `robot_trade_history`, deploy às 23:54 de 28/07):

| Janela | Ops | Acerto | P/L |
|---|---|---|---|
| Antes | 135 | 55,6% | +884,20 |
| Depois | 22 | 31,8% | −568,50 |

Causa raiz das duas queixas:

1. **Timing do ciclo.** `result_display_until` **bloqueia `prepare_cycle`**
   (`auto_trader.prepare_cycle` + `main.result_display_expired`). Subindo de 5s
   para 12s, o reset pós-resultado passou a cair no **fim** da janela de análise
   (segundos 5–20 da vela). A varredura terminava tarde e a compra chegava fora
   dos 0–5s → `[ENTRY_WINDOW_MISSED]` (registrado com
   `current_candle_seconds=7.1` e `11.5`) e mais dependência de candidato de
   fallback.
2. **Payload preso no resultado.** Com `unseen_result=True` sempre, `to_dict`
   forçava `status=WIN/LOSS` (e `result_display_until` = agora+60s quando vazio).
   Isso pulava o ramo `WAITING_NEXT_CYCLE` de `build_robot_payload`, que é quem
   **limpa** `best_candidate` / `last_signal`. O balão do overlay
   (`RobotOverlay`: `pending_signal ?? best_candidate ?? last_signal`) continuava
   anunciando um ativo que nunca viraria ordem.

### Solução: `result_voice`

A fala do placar não depende mais do `status` nem da janela de display.

| Peça | Arquivo | Papel |
|---|---|---|
| `RESULT_VOICE_TTL_SECONDS = 25` | `backend/auto_trader.py` | Validade da fala do placar |
| `RobotState.result_voice` | `backend/auto_trader.py` | `{order_id, result, cycle_result, gale_step, wins, losses, profit, at}` |
| `finish_trade` | `backend/auto_trader.py` | Publica `result_voice` no fechamento |
| `to_dict` | `backend/auto_trader.py` | Expõe e expira por TTL; **não** altera `status` |
| `ROBOT_BUSY_STATUSES` | `backend/auto_trader.py` | `unseen_result` não sobrescreve sinal/ordem em andamento |
| `normalizeResultVoice` | `frontend/src/lib/robotState.ts` | Normaliza o campo (`RobotResultVoice`) |
| `resultVoiceEvent` | `frontend/src/lib/robotNarration.ts` | Gera o evento de fala; legado só entra se o canal não vier |

Valores restaurados ao comportamento original (não alterar):

| Constante | Valor | Por quê |
|---|---|---|
| `result_display_until` | **5s** | Bloqueia `prepare_cycle`; esticar atrasa a análise na vela |
| Overlay WIN/LOSS + ativo | **60s** | Só UI (`RESULT_OVERLAY_DISPLAY_MS`); não bloqueia o ciclo |
| `acknowledge_unseen_result(hold_seconds)` | **8s** | Só overlay de tela fechada |
| `unseen_result` no fechamento | só com painel **offline** | Online, o payload não deve mascarar o ciclo |

Como a voz sobrevive sem o status: o canal vive 25s, o evento tem prioridade
100 na fila (`narrationEventPriority`) e preempta fala menor — então o placar é
anunciado mesmo com o robô já analisando a vela seguinte.

Chave de deduplicação: `RESULT|<result>|<order_id>|<wins>-<losses>`
(`resultEventKey`), compartilhada pelo canal novo e pelo caminho legado. Os dois
são **exclusivos** (`if (voiceEvent) … else …`) para nunca falar 2x o mesmo resultado.

Testes: `Backend/tests/test_result_voice_channel.py` (6 casos) e
`Frontend/src/lib/robotNarration.test.ts` (§“narração do placar”).

## 4. Valores monetários na narração

A fala de dinheiro usa `formatMoneyForSpeech` (`bullexConnection.ts`):

- **Antes:** `84,13 reais` (TTS lia número quebrado / vírgula).
- **Agora:** `84 reais e 13 centavos`, `84 reais e 0 centavos`,
  `1 real e 1 centavo`, `menos 12 reais e 40 centavos`.

Sem vírgula nem ponto decimal na string falada.


## 4d. Fala longa cortada no meio (2026-09-08)

Sintoma: o El Capo começava a explicar a operação e **parava antes do fim** —
a explicação nunca era repetida, porque a chave já entrava em `spokenKeys`
antes do `speak()`.

Três causas somadas, todas em `useRobotNarrator.ts`:

1. **Watchdog cego.** O bloco de `BUSY_WATCHDOG_MS` (25s) cancelava por tempo
   puro, sem checar `speechSynthesis.speaking`. O texto de `SIGNAL_FOUND` tem
   ~52 palavras (~22s no `SPEECH_RATE` de 0,92) e passa disso quando a
   estratégia traz resumo longo — então o watchdog cortava fala legítima.
2. **Corte de fala longa do Chrome (~15s).** O keep-alive só chamava
   `resume()` sob `speaking && paused`, mas nesse bug o motor mantém
   `paused=false` — a condição nunca era satisfeita.
3. **Preempção por `ORDER_REJECTED`** (prioridade 80 contra 0). Enquanto o
   par recusado repetia "Entrada rejeitada" a cada vela, ele cortava a
   explicação da entrada seguinte. Ver o cooldown progressivo em
   [`ESTRATEGIA.md`](./ESTRATEGIA.md) §6 (2026-09-08).

### Correção vigente

| Peça | Arquivo | Comportamento |
|---|---|---|
| `splitSpeechChunks` | `frontend/src/lib/speechChunks.ts` | Quebra o texto em pedaços de até `SPEECH_CHUNK_MAX_CHARS` (140), **sem partir frase** |
| `speakSequence` | `frontend/src/hooks/useRobotNarrator.ts` | Encadeia os pedaços no `onend` do anterior; só o 1º passa pelo `cancel()` + 60ms do Safari |
| `onChunkStart` | idem | Reinicia `busyStartedAtRef` a cada pedaço — o watchdog mede **falta de progresso**, não duração total |
| `engineIdle` | idem | Watchdog normal só corta com o motor parado |
| `BUSY_HARD_WATCHDOG_MS` | idem | 120s — destrava se o Chrome deixar `speaking` preso em `true` |
| Keep-alive | idem | `pause()+resume()` incondicional a cada 5s enquanto `speaking` |

O texto ouvido **não mudou** — só a forma de entregá-lo ao motor de voz.

Testes: `frontend/src/lib/robotNarration.test.ts` (bloco "fala longa não é
cortada no meio").

## 5. Janela de entrada (inalterada)

Quando há `pending_signal` aguardando a vela:

- Label: **“Entrada no início da próxima vela em”** + mm:ss
- Narração de entrada preparada permanece a mesma

**Importante (2026-07-26):** só `pending_signal` gera visual/voz de
“Melhor ativo encontrado”. `best_candidate` durante a análise é telemetria
e **não** deve aparecer como entrada preparada (regressão: overlay mentia
a operação e a corretora depois rejeitava o ativo).

**Reforço (2026-07-31):** o balão do `RobotOverlay` ainda caía em
`pending_signal ?? best_candidate ?? last_signal`, e o `to_dict()` do
`auto_trader` usava `pending_signal or best_candidate` na voz. Isso gerava
“vou de CALL/PUT…” sem ordem. Ambos voltaram a usar **somente**
`pending_signal`. Ver também `ROBO_E_SUPORTE.md` (motivo real do bloqueio
na compra: `ACTIVE_CLOSED` vs “baixa qualidade”).

## 6. Histórico

- **2026-09-08** — Fala longa deixou de ser cortada: texto quebrado em
  pedaços encadeados, watchdog só corta com o motor parado, keep-alive do
  Chrome com `pause()+resume()`. Ver §4d.
- **2026-07-31 (visual Mac/celular)** — Avatar via canvas (`RobotAvatarVideo`)
  para Safari/iOS aplicar o mesmo `hue-rotate` do Windows. Ver `OVERLAY_ROBO.md`.
- **2026-07-31 (entrada anunciada sem compra)** — Overlay e voz deixam de
  promover `best_candidate`/`last_signal`; portão na compra reporta
  `ACTIVE_CLOSED`/`STALE_MARKET_DATA` em vez de mascarar como baixa qualidade.
  Ver §5 e `ROBO_E_SUPORTE.md`.
- **2026-07-31** — MacBook/Safari/celular: fallback pt-BR (não silencia),
  unlock por gesto, delay pós-`cancel()`, libera Google TTS nativo; menu
  hamburger mobile (`SHELL_LAYOUT.md`). Ver §1b.
- **2026-07-29** — Canal `result_voice`: placar falado em toda operação **sem**
  tocar no ciclo. Reverte display 12s → 5s, hold 15s → 8s e `unseen_result`
  sempre → só offline (que degradaram acerto e geravam entrada anunciada sem
  execução). Ver §4c.
- **2026-07-28** — Placar: narração WIN/LOSS em todas as operações (não só a
  1ª). `unseen_result` sempre no fechamento + hold 15s; display 12s; preempt
  TTS do placar. **Timing revertido em 2026-07-29** — ver §4b/§4c.
- **2026-07-26** — Entrada visual/voz só com `pending_signal`; remove
  promoção de `best_candidate` → “Melhor ativo”. Ver `ESTRATEGIA.md` §6.
- **2026-07-25** — macOS/Safari: bloqueio real de Luciana (name+URI), sem
  fallback feminino; preferência Felipe/Eloquence; pitch 0.85. Ver §1b.
- **2026-07-25 (backup)** — Visual do backup: lima + CSS `hue-rotate`
  (`/robo-wink-orig.webm`) — remove quadrado no Windows. Ver `OVERLAY_ROBO.md`.
- **2026-07-25 (restore)** — Tentativa de bake clássico
  (`/robo-wink-classic.webm`); Windows mostrou fundo quadrado.
- **2026-07-25 (noite++)** — Tentativa de remover “roda” (recorte flat);
  revertida no restore.
- **2026-07-25 (noite+)** — Windows: cache-bust + nginx sem immutable em webm.
- **2026-07-25 (tarde)** — Bake roxo no WebM.
- **2026-07-25 (cedo)** — Tentativa de remover hue-rotate (Mac amarelo).
- **2026-07-24 (noite)** — TTS: “Capo” → “Kápo” na fala para evitar “Cepo”.
- **2026-07-24 (tarde)** — Valores falados como “X reais e Y centavos”.
- **2026-07-24 (tarde)** — Remove MP3 legado no start; TTS contínuo sem
  “aguarde 5 minutos”.
- **2026-07-24** — Textos de análise contínua; remove countdown “próxima análise”.
