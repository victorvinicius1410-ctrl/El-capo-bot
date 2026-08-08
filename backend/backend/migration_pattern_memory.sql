-- =============================================================================
-- El Capo — Memória de padrões (pattern memory) — v3
-- Data: 2026-07-31
--
-- IMPORTANTE: rode no projeto Supabase da VPS
--   URL: https://dfxmasxpqpujxahwzpqn.supabase.co
-- (se estiver em outro projeto, robot_trade_history "não existe")
--
-- Parte 1 cria a tabela (obrigatória).
-- Parte 2 faz backfill SÓ se robot_trade_history existir (senão ignora).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- PARTE 1 — criar tabela (rode sempre)
-- ---------------------------------------------------------------------------
drop table if exists public.robot_pattern_memory cascade;

create table public.robot_pattern_memory (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.users (id) on delete cascade,
  pattern_key text not null,
  active text not null,
  direction text not null,
  setup text not null,
  timeframe text not null default 'M1',
  hour_utc smallint not null check (hour_utc >= 0 and hour_utc <= 23),
  wins integer not null default 0 check (wins >= 0),
  losses integer not null default 0 check (losses >= 0),
  profit numeric(14, 2) not null default 0,
  updated_at timestamptz not null default timezone('utc', now()),
  created_at timestamptz not null default timezone('utc', now()),
  constraint robot_pattern_memory_user_key unique (user_id, pattern_key)
);

comment on table public.robot_pattern_memory is
  'Agregados WIN/LOSS por contexto (ativo×hora×setup×direção×TF) para PATTERN_MEMORY_WEAK';
comment on column public.robot_pattern_memory.pattern_key is
  'Chave canônica ATIVO|HH|SETUP|CALL_OR_PUT|M1_M5_M15';
comment on column public.robot_pattern_memory.hour_utc is
  'Hora UTC da abertura da operação (0-23)';
comment on column public.robot_pattern_memory.setup is
  'Setup: CONTINUATION | REVERSAL | SUPPORT_RESISTANCE | UNKNOWN';

create index if not exists robot_pattern_memory_user_updated_idx
  on public.robot_pattern_memory (user_id, updated_at desc);

create index if not exists robot_pattern_memory_user_active_idx
  on public.robot_pattern_memory (user_id, active);

alter table public.robot_pattern_memory enable row level security;

drop policy if exists robot_pattern_memory_select_own on public.robot_pattern_memory;
create policy robot_pattern_memory_select_own
  on public.robot_pattern_memory
  for select
  using (auth.uid()::text = user_id);

drop policy if exists robot_pattern_memory_service_all on public.robot_pattern_memory;
create policy robot_pattern_memory_service_all
  on public.robot_pattern_memory
  for all
  using (auth.uid()::text = user_id)
  with check (auth.uid()::text = user_id);

-- ---------------------------------------------------------------------------
-- PARTE 2 — backfill opcional (não quebra se o histórico não existir)
-- ---------------------------------------------------------------------------
do $$
begin
  if to_regclass('public.robot_trade_history') is null then
    raise notice 'robot_trade_history nao encontrada — tabela criada sem backfill. O robô hidrata pela API/histórico em RAM.';
    return;
  end if;

  insert into public.robot_pattern_memory (
    user_id,
    pattern_key,
    active,
    direction,
    setup,
    timeframe,
    hour_utc,
    wins,
    losses,
    profit,
    updated_at
  )
  select
    h.user_id,
    upper(coalesce(nullif(h.active, ''), 'UNKNOWN'))
      || '|'
      || lpad(extract(hour from h.opened_at at time zone 'utc')::int::text, 2, '0')
      || '|'
      || upper(coalesce(
           nullif(h.analysis_json->>'strategy_setup', ''),
           nullif(h.analysis_json->>'price_action_setup', ''),
           nullif(h.analysis_json->'metrics'->>'price_action_setup', ''),
           'UNKNOWN'
         ))
      || '|'
      || upper(coalesce(nullif(h.direction, ''), 'UNKNOWN'))
      || '|'
      || upper(coalesce(nullif(h.timeframe, ''), 'M1')) as pattern_key,
    upper(coalesce(nullif(h.active, ''), 'UNKNOWN')) as active,
    upper(coalesce(nullif(h.direction, ''), 'UNKNOWN')) as direction,
    upper(coalesce(
      nullif(h.analysis_json->>'strategy_setup', ''),
      nullif(h.analysis_json->>'price_action_setup', ''),
      nullif(h.analysis_json->'metrics'->>'price_action_setup', ''),
      'UNKNOWN'
    )) as setup,
    upper(coalesce(nullif(h.timeframe, ''), 'M1')) as timeframe,
    extract(hour from h.opened_at at time zone 'utc')::int as hour_utc,
    count(*) filter (
      where upper(coalesce(h.final_result, h.result, '')) = 'WIN'
    )::int as wins,
    count(*) filter (
      where upper(coalesce(h.final_result, h.result, '')) = 'LOSS'
    )::int as losses,
    coalesce(sum(h.profit), 0)::numeric(14, 2) as profit,
    timezone('utc', now()) as updated_at
  from public.robot_trade_history h
  where coalesce(h.is_gale, false) = false
    and h.finished_at >= (timezone('utc', now()) - interval '90 days')
    and upper(coalesce(h.final_result, h.result, '')) in ('WIN', 'LOSS')
  group by 1, 2, 3, 4, 5, 6, 7
  on conflict (user_id, pattern_key) do update set
    wins = excluded.wins,
    losses = excluded.losses,
    profit = excluded.profit,
    updated_at = excluded.updated_at;

  raise notice 'Backfill de robot_pattern_memory concluido.';
end $$;
