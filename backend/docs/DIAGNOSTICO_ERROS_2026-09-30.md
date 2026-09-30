# Diagnóstico e correção dos erros de 29–30/09/2026

O vigia de erros (29/09 14:11 BRT em diante) registrou **72 tipos, 1.040 ocorrências e ~30 e-mails** em
26 h. Diagnosticados com calma (logs, banco e código), eles se reduziram a ~12 causas. Este documento
registra cada causa, a correção e o número que prova que resolveu — medir 24 h depois do deploy.

Plano aprovado: `/root/.claude/plans/cria-um-planejamento-para-floofy-willow.md`.

## Causas e correções

| # | Causa | Correção | Prova (antes → meta) |
|---|---|---|---|
| A | **Vazamento de memória** no robot-runtime (~300–420 MB/h) e no bullex-service (~115 MB/h): cache de mercado com chave por vela (`endtime`), entrada vencida nunca apagada — nem no cache compartilhado nem nas cópias por usuário. OOM do kernel em 27/09 e 30/09. | `prune_market_caches` (runtime) e `SessionManager.podar_caches` (bullex): velas vencidas há > 10 min saem, 1x/min. `[RUNTIME_MEMORY]` a cada 10 min. `mem_limit` por container. Swap 4 GB. Vigia avisa memória < 20% e `Killed process` do kernel. | runtime 7,5 GB/26 h → curva plana; 0 OOM |
| B | **EURGBP-op** fora da tabela da biblioteca → `KeyError` no buy → sessão do cliente derrubada. | `register_broker_active` preenche a tabela pelo `get_all_init_v2`; `ensure_asset_in_broker_table` recusa como "indisponível"; `KeyError/ValueError/TypeError` não derrubam sessão (`[SESSION_OPERATION_ERROR]`). | 4 `SESSION_DISCONNECTED`/dia → 0 |
| C | **Payout de par aberto sempre 4 s** (canal digital primeiro; espera `int(...) > 3`). Cascata de timeouts e RSI sem dados. | `read_open_or_otc_payout`: par aberto vai direto ao turbo/binário; espera `>=`; erro de dado não vira sessão caída. | 131 `/payouts timeout` → < 5/dia; p90 4,0 s → < 1 s |
| D | **EURJPY-OTC com código errado na tabela** (1346, ativo desativado): velas de junho de 2025, sinal CALL/100 falso e 0/143 compras aceitas — o par está vivo na BullEx com o código **79**. Recusa tratada só por conta. | Tabela corrigida (79) e `register_broker_active` passa a alinhar TODO código ao que a corretora informa no `get_all_init_v2` (1x/min). Salvaguardas: bloqueio global a partir da 2ª recusa (escada até 6 h) e `candles_stale_age` recusa vela velha de qualquer ativo. | 170 recusas (33% das compras) → < 5%; EURJPY-OTC com ordens aceitas |
| E | **Resultado perdido** na queda do websocket (ordem 14307723702): bullex só lia a memória da sessão; ciclo reciclado com monitor vivo; TIMEOUT mudo quando o `last_trade` era outro; órfãs só no boot; auditoria só "hoje". | Consulta à corretora (`get-options`) após a expiração, com teto; reconexão herda resultados; `waiting_result_stale` espera o monitor vivo (teto 10 min); `marcar_timeout_no_espelho` + `[ORDER_RESULT_UNKNOWN]` (ERROR); varredura de órfãs a cada 5 min; auditoria olha 3 dias e cruza com o Histórico de qualquer dia. | 0 órfãs > 1 h |
| E' | WIN de 28/09 do 11e0b3d5 **voltou a PENDENTE** em 30/09: o restore carregava `last_trade` pendente de dias atrás e o runtime regravava o espelho. | Restore não carrega pendente com > 2 h; espelho nunca regrava pendente com > 2 h (`[TRADE_MIRROR_STALE_PENDING_BLOCKED]`). | auditoria limpa |
| F | **Gateway rebaixou placar** sem snapshot (9f6af6f2: 4x2 do boot por cima do 5x3); reidratação exibia placar de outro dia. | Sem snapshot o gateway preserva o placar do banco (`[SCORE_PERSIST_KEPT_DB]`); `score_day` gravado pelo runtime; reidratação ignora outro dia. | auditoria limpa 3 dias |
| J1 | **Offline fantasma de 60 s** depois do Iniciar/reconexão (404 falso em todos os ativos). | `upsert`/`clear_probe_cache` zeram o offline; atalho não vale com sessão viva; varredura para no 1º `SESSION_DISCONNECTED`; marca renomeada `[BULLEX_SESSION_MISSING]`. | 174 linhas/dia → perto de 0 |
| J2 | **Trava de envio presa** depois de queda do websocket (5 s por envio, 503). | `try/finally` no `send_websocket_request`. | 0 `websocket_send_lock_timeout` |
| J3 | `check_connect` sem `()`; `get_candles` fazia login por senha (sessão por token não tem). | Parênteses; a exceção sobe para o gerenciador reconectar. | 0 `get_candles need reconnect` |
| J4 | `RemoteProtocolError` (keep-alive de 5 s nos dois lados). | `keepalive_expiry=3` no runtime. | 8/dia → ~0 |
| G | "Time for purchasing options is over" perdia a vela. | 1 nova tentativa em 0,4 s (a ordem não foi criada). | — |
| I | ~2/3 dos e-mails eram condição esperada logada como ERROR. | Recusas esperadas em WARNING (`[ORDER_SEND_REFUSED]`), RSI e saldo sem traceback, `[ORDER_SUBMITTING]` no lugar do `[ORDER_SENT]` pré-envio, painel fechado em INFO. Vigia em 2 níveis (ver `VIGIA_DE_ERROS.md`). | e-mail na hora só do inesperado |
| H | nginx: 17 × 502 de robôs no staging parado (sem `default_server` na 443). | `000-padrao-443` com `ssl_reject_handshake`; site `api.elcapo2.shop` desativado (arquivo em `sites-available`). | 0 × 502 |

## Não feito (e por quê)
- **Cache de abertura global no bullex**: o mapa por usuário é usado por `mark_binary_option_closed` e
  `clear_binary_open_cache`; torná-lo global exige refatorar esses caminhos. O ganho maior (4 s do digital)
  já veio do item C.
- **`ensure_session_alive` checando o socket**: exigiria entrar no contexto isolado da sessão num caminho
  quente; a trava liberada (J2) já tira o sintoma.

## Benignos (sem correção)
`invalid_credentials` = cliente digitando senha; quedas de websocket da corretora (reconectam sozinhas);
`invalid_ssid` recuperado por senha.
