-- =============================================================================
-- El Capo — Caderno GLOBAL de memória de padrões — v4
-- Data: 2026-08-03
--
-- IMPORTANTE: rode no projeto Supabase da VPS
--   URL: https://dfxmasxpqpujxahwzpqn.supabase.co
--
-- Cria a tabela única do sistema e UNIFICA todos os cadernos pessoais
-- (robot_pattern_memory) somando wins/losses/profit por pattern_key.
-- Os cadernos por usuário NÃO são apagados (arquivo/auditoria).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- PARTE 1 — tabela global (sem FK de user; é o caderno do sistema)
-- ---------------------------------------------------------------------------
create table if not exists public.robot_pattern_memory_global (
  pattern_key text primary key,
  active text not null,
  direction text not null,
  setup text not null,
  timeframe text not null default 'M1',
  hour_utc smallint not null check (hour_utc >= 0 and hour_utc <= 23),
  wins integer not null default 0 check (wins >= 0),
  losses integer not null default 0 check (losses >= 0),
  profit numeric(14, 2) not null default 0,
  updated_at timestamptz not null default timezone('utc', now()),
  created_at timestamptz not null default timezone('utc', now())
);

comment on table public.robot_pattern_memory_global is
  'Caderno único do sistema: agregados WIN/LOSS por contexto (todas as contas)';
comment on column public.robot_pattern_memory_global.pattern_key is
  'Chave canônica ATIVO|HH|SETUP|CALL_OR_PUT|M1_M5_M15';

create index if not exists robot_pattern_memory_global_updated_idx
  on public.robot_pattern_memory_global (updated_at desc);

create index if not exists robot_pattern_memory_global_active_idx
  on public.robot_pattern_memory_global (active);

alter table public.robot_pattern_memory_global enable row level security;

-- Leitura autenticada (o gateway usa service role; policy defensiva para clients).
drop policy if exists robot_pattern_memory_global_select_authenticated
  on public.robot_pattern_memory_global;
create policy robot_pattern_memory_global_select_authenticated
  on public.robot_pattern_memory_global
  for select
  to authenticated
  using (true);

-- ---------------------------------------------------------------------------
-- PARTE 2 — unificar cadernos pessoais no caderno global (idempotente)
-- ---------------------------------------------------------------------------
do $$
begin
  if to_regclass('public.robot_pattern_memory') is null then
    raise notice 'robot_pattern_memory nao encontrada — tabela global criada vazia.';
    return;
  end if;

  -- Substitui o agregado global pela soma atual dos cadernos pessoais.
  delete from public.robot_pattern_memory_global where wins >= 0;

  insert into public.robot_pattern_memory_global (
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
    p.pattern_key,
    max(p.active) as active,
    max(p.direction) as direction,
    max(p.setup) as setup,
    max(p.timeframe) as timeframe,
    max(p.hour_utc)::smallint as hour_utc,
    sum(p.wins)::integer as wins,
    sum(p.losses)::integer as losses,
    coalesce(sum(p.profit), 0)::numeric(14, 2) as profit,
    timezone('utc', now()) as updated_at
  from public.robot_pattern_memory p
  where coalesce(p.pattern_key, '') <> ''
  group by p.pattern_key
  on conflict (pattern_key) do update set
    active = excluded.active,
    direction = excluded.direction,
    setup = excluded.setup,
    timeframe = excluded.timeframe,
    hour_utc = excluded.hour_utc,
    wins = excluded.wins,
    losses = excluded.losses,
    profit = excluded.profit,
    updated_at = excluded.updated_at;

  raise notice 'Unificacao robot_pattern_memory -> robot_pattern_memory_global concluida.';
end $$;
