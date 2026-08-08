-- =============================================================================
-- El Capo — estratégias nomeadas + histórico de análise detalhado
-- Data: 2026-07-26
--
-- O histórico JÁ persiste telemetria em robot_trade_history.analysis_json
-- (jsonb). Esta migration é OPCIONAL: denormaliza campos para filtros/SQL
-- mais fáceis. Se você não rodar, o painel continua funcionando via
-- analysis_json expandido pela API.
--
-- Estratégias cobertas:
--   RETRACEMENT_SR      — Retração em Zonas de Suporte e Resistência (M1/M5)
--   EXHAUSTION_REVERSAL  — Padrões de Reversão em Zonas de Exaustão
--   CANDLE_FLOW          — Fluxo de Velas (Seguimento de Força)
--   CONTINUATION         — Continuação clássica (fallback)
-- =============================================================================

alter table if exists public.robot_trade_history
  add column if not exists strategy_key text,
  add column if not exists strategy_summary text,
  add column if not exists analysis_detail text,
  add column if not exists speech_preview text;

comment on column public.robot_trade_history.strategy_key is
  'Chave da estratégia nomeada: RETRACEMENT_SR | EXHAUSTION_REVERSAL | CANDLE_FLOW | CONTINUATION';
comment on column public.robot_trade_history.strategy_summary is
  'Resumo curto da estratégia (balão / lista do histórico)';
comment on column public.robot_trade_history.analysis_detail is
  'Explicação completa da análise usada na entrada';
comment on column public.robot_trade_history.speech_preview is
  'Texto curto falado/exibido pelo El Capo no balão';

-- Backfill a partir do analysis_json já existente (quando houver).
update public.robot_trade_history
set
  strategy_key = coalesce(
    strategy_key,
    nullif(analysis_json->>'strategy_key', '')
  ),
  strategy_summary = coalesce(
    strategy_summary,
    nullif(analysis_json->>'strategy_summary', '')
  ),
  analysis_detail = coalesce(
    analysis_detail,
    nullif(analysis_json->>'analysis_detail', ''),
    nullif(analysis_json->>'entry_reason', ''),
    nullif(analysis_json->>'strategy_reason', '')
  ),
  speech_preview = coalesce(
    speech_preview,
    nullif(analysis_json->>'speech_preview', '')
  )
where analysis_json is not null
  and (
    strategy_key is null
    or strategy_summary is null
    or analysis_detail is null
    or speech_preview is null
  );

create index if not exists robot_trade_history_strategy_key_idx
  on public.robot_trade_history (user_id, strategy_key);
