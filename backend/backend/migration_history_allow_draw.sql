-- Empate (DRAW) no Histórico do cliente — 2026-09-15
--
-- `build_trade_history_item` aceita DRAW desde 11/09 e está no ar, mas a
-- operação continuava sumindo do Histórico: o CHECK da tabela só admite
-- WIN/LOSS, o PostgREST devolve 400 e o `except` genérico engole com
-- `[ROBOT HISTORY ERROR]`. Medido em 15/09: 45 de 45 empates desde 01/09 sem
-- linha, e ZERO linhas DRAW em toda a história da tabela.
-- Ver backend/docs/PLACAR_DIAGNOSTICO_2026-09-15.md §F4.
--
-- TIMEOUT continua de fora de propósito: ali o resultado é DESCONHECIDO, e
-- gravar desconhecido como histórico é pior do que não gravar.
--
-- Rodar no SQL editor do Supabase (produção) e conferir a saída do passo 3 —
-- doc dizer que rodou não é prova.

-- 1) Antes: qual é o nome real da constraint neste banco?
select conname, pg_get_constraintdef(oid) as definicao
  from pg_constraint
 where conrelid = 'public.robot_trade_history'::regclass
   and contype = 'c';

-- 2) Troca o CHECK (idempotente: pode rodar de novo sem quebrar).
alter table public.robot_trade_history
  drop constraint if exists robot_trade_history_result_check;

alter table public.robot_trade_history
  add constraint robot_trade_history_result_check
  check (result in ('WIN', 'LOSS', 'DRAW'));

-- 3) Prova do efeito: tem de listar DRAW na definição.
select conname, pg_get_constraintdef(oid) as definicao
  from pg_constraint
 where conrelid = 'public.robot_trade_history'::regclass
   and conname = 'robot_trade_history_result_check';

-- 4) Depois da migration, rodar o backfill dos empates perdidos:
--    python scripts/backfill_history_from_trades.py --dry-run
--    python scripts/backfill_history_from_trades.py --apply
