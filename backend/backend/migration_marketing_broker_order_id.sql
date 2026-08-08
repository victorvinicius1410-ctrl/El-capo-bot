-- =============================================================================
-- Migration: vínculo do espelho marketing com o order_id da corretora
-- =============================================================================
-- Execute no SQL Editor do Supabase (ou psql) ANTES do deploy do backend.
--
-- Problema corrigido:
--   Operações ao vivo da Bullex são espelhadas em marketing_simulated_trades
--   para o painel Shift+O. Sem guardar o order_id original, a mesma operação
--   aparecia duas vezes no Histórico (uma pelo order_id, outra pelo UUID do
--   espelho) e a exclusão de uma delas era desfeita na próxima sincronização.
--
-- O que muda:
--   - broker_order_id guarda o order_id da Bullex quando a linha é espelho de
--     uma operação real; permanece NULL nas operações geradas no Shift+O.
--   - A exclusão por order_id passa a encontrar o espelho por esta coluna, sem
--     filtrar a coluna UUID com um id numérico (evita HTTP 400).
--
-- Compatibilidade:
--   O backend funciona com ou sem esta coluna (grava o espelho sem o vínculo
--   enquanto a migration não é aplicada), mas o histórico só para de duplicar
--   operações ao vivo depois que ela roda.
-- =============================================================================

alter table public.marketing_simulated_trades
  add column if not exists broker_order_id text;

comment on column public.marketing_simulated_trades.broker_order_id is
  'order_id da Bullex quando a linha espelha uma operação real; NULL nas operações geradas no Shift+O.';

create index if not exists marketing_simulated_trades_broker_order_id_idx
  on public.marketing_simulated_trades (company_id, user_id, broker_order_id)
  where broker_order_id is not null;

-- Garante RLS e grants (idempotente com o bootstrap)
alter table public.marketing_simulated_trades enable row level security;

revoke insert, update, delete on public.marketing_simulated_trades from anon, authenticated;
grant all on public.marketing_simulated_trades to service_role;
