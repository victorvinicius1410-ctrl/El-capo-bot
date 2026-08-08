# Estratégias nomeadas do El Capo

Documento criado em **2026-07-26** para as três estratégias explícitas pedidas
no produto, além da continuação clássica.

## Status no pipeline (2026-07-26)

**Inativas no robô em produção.** Em 2026-07-26 a estratégia operacional
voltou para `backup-classic` (ver [`ESTRATEGIA.md`](./ESTRATEGIA.md)):
`signal_engine.analyze_signal` e `main.apply_strategy_guard` **não** chamam
mais este módulo. O arquivo `backend/named_strategies.py` e os testes de
detecção isolada permanecem no repositório para referência / reativação futura.
A UI de balão/popup continua preparada para campos `strategy_key` /
`speech_preview` quando existirem no sinal; com o clássico ativo esses campos
não são preenchidos pela análise.

## Objetivo

1. Detectar setups nomeados na análise técnica.
2. Explicar a operação (TTS + balão + popup).
3. Persistir estratégia + detalhe no histórico (`analysis_json`).

## Módulo

Arquivo: `backend/named_strategies.py` (código presente; **fora** do pipeline ativo)

| Função | Papel |
|---|---|
| `detect_retracement_sr` | Retração em S/R (só **M1/M5**) |
| `detect_exhaustion_reversal` | Reversão em zona de exaustão |
| `detect_candle_flow` | Fluxo de velas / seguimento de força |
| `detect_named_strategies` | Avalia as três e devolve matches |
| `pick_primary_strategy` | Prioridade: retração → exaustão → fluxo → CONTINUATION |
| `attach_strategy_narration` | Grava campos no sinal |

Integração **histórica** (desligada no clássico): `_apply_quality_filters`
chamava a detecção após proximidade de S/R e RSI; `apply_strategy_guard`
respeitava `sr_zone_exempt`. No estado atual esses caminhos não executam.

## Estratégias

### 1. `RETRACEMENT_SR` — Retração em Zonas de Suporte e Resistência

- Timeframes: **somente M1 e M5**.
- CALL: `near_support` + pullback baixista recente + rejeição com pavio inferior.
- PUT: `near_resistance` + pullback altista + rejeição com pavio superior.
- `sr_zone_exempt = True` → **pode operar no nível** (exceção à política `SR_ZONE`).

### 2. `EXHAUSTION_REVERSAL` — Padrões de Reversão em Zonas de Exaustão

- CALL: RSI baixo (≤35) ou 3 velas de venda + rejeição altista.
- PUT: RSI alto (≥65) ou 3 velas de compra + rejeição baixista.
- `sr_zone_exempt = True` (pode coincidir com região de nível).

### 3. `CANDLE_FLOW` — Fluxo de Velas (Seguimento de Força)

- ≥3 das últimas 4 velas com corpo forte alinhado à direção.
- **Não** segue CALL colado em resistência nem PUT colado em suporte.
- `sr_zone_exempt = False` — continuação cega em S/R continua bloqueada.

### 4. `CONTINUATION` (fallback)

Usado quando não há match nomeado mas o setup clássico de continuação
passou. Continua sujeito a `SR_ZONE` (não opera colado no nível).

## Política `SR_ZONE` (atualizada)

| Situação | Resultado |
|---|---|
| Continuação / fluxo cego em S/R | Bloqueio crítico `SR_ZONE` |
| `RETRACEMENT_SR` / `EXHAUSTION_REVERSAL` confirmados | Isentos de `SR_ZONE` |
| Setup `REVERSAL` sem match nomeado | Bloqueio `PRICE_ACTION_SETUP` |

## Campos no sinal / pending / trade / histórico

| Campo | Uso |
|---|---|
| `strategy_key` | Chave estável (`RETRACEMENT_SR`, …) |
| `strategy_name` | Rótulo legível |
| `strategy_summary` | Resumo curto (lista / balão) |
| `analysis_detail` | Explicação completa (popup / histórico) |
| `speech_preview` | Frase do El Capo (“Vou de CALL por…”) |
| `named_strategies` | Lista de matches detectados |

Persistência: `robot_persistence.TRADE_ANALYSIS_FIELDS` → `robot_trade_history.analysis_json`.

SQL opcional (colunas denormalizadas):  
`backend/migration_named_strategies_analysis.sql`

## UI

- **Overlay:** balão de texto acima do avatar com `speech_preview`; clique abre
  popup com `analysis_detail` + estratégia.
- **Histórico:** colunas Estratégia + “Ver análise” (modal).
- **Narração TTS:** inclui estratégia + motivo + preview.

## Testes

- `tests/test_named_strategies.py`
- `tests/test_candle_analysis.py` (retracement isenta `SR_ZONE`; continuação cega bloqueia)
- `tests/test_strategy_filters.py` (`SR_ZONE` em continuação sem isenção)

## Histórico

- **2026-07-26 (restauração clássica)** — Pipeline volta a `backup-classic`;
  este módulo deixa de alimentar entradas. Documentação mantida.
- **2026-07-26** — Criação das 3 estratégias + narrativa + histórico detalhado + balão/popup.
