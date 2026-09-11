-- ============================================================================
-- El Capo STAGING — schema completo (projeto Supabase NOVO)
-- Gerado em 2026-08-21T16:21:02Z
-- NÃO roda em produção. Não copia dados de clientes.
-- ============================================================================

-- ============================================================================
-- EL CAPO — BOOTSTRAP COMPLETO DO BANCO SUPABASE
-- ============================================================================
-- Uso: projeto Supabase NOVO (SQL Editor → cole e Execute).
-- Não migra dados do banco antigo; só cria schema, funções, RLS e seeds.
--
-- Fontes consolidadas (estado FINAL):
--   Backend/backend/supabase_schema.sql
--   Frontend/supabase/migrations/20260622*.sql … 20260718123000_*.sql
--
-- Depois de rodar:
--   1. Configure SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY no backend
--   2. Configure VITE_SUPABASE_URL + VITE_SUPABASE_ANON_KEY no frontend
--   3. Crie o admin (com service_role / SQL Editor):
--        select public.bootstrap_admin_user(
--          'seu-email@dominio.com',
--          'SenhaForte123',
--          'Administrador',
--          true
--        );
-- ============================================================================

-- Em projetos Supabase hosted, pgcrypto costuma estar no schema `extensions`.
create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- Funções utilitárias (robô)
-- ---------------------------------------------------------------------------

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = timezone('utc'::text, now());
  return new;
end;
$$;

create or replace function public.sync_robot_state_json_alias()
returns trigger
language plpgsql
as $$
begin
  if coalesce(new.state, '{}'::jsonb) = '{}'::jsonb
     and coalesce(new.state_json, '{}'::jsonb) <> '{}'::jsonb then
    new.state = new.state_json;
  elsif coalesce(new.state_json, '{}'::jsonb) = '{}'::jsonb
     and coalesce(new.state, '{}'::jsonb) <> '{}'::jsonb then
    new.state_json = new.state;
  end if;
  return new;
end;
$$;

create or replace function public.set_current_timestamp_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

create or replace function public.reject_admin_customer_event_mutation()
returns trigger
language plpgsql
as $$
begin
  raise exception 'admin_customer_events é append-only'
    using errcode = '42501';
end;
$$;

create or replace function public.reject_billing_event_mutation()
returns trigger
language plpgsql
as $$
begin
  raise exception 'billing_events é append-only'
    using errcode = '42501';
end;
$$;

-- ============================================================================
-- PARTE 1 — ROBÔ / TRADING (gateway backend via service_role)
-- public.users.id é TEXT (UUID do auth como string). Sem FK para auth.users.
-- ============================================================================

create table if not exists public.users (
  id text primary key,
  created_at timestamptz not null default timezone('utc'::text, now()),
  updated_at timestamptz not null default timezone('utc'::text, now())
);

create table if not exists public.bullex_connections (
  id bigint generated always as identity primary key,
  user_id text not null references public.users(id) on delete cascade,
  bullex_email text,
  connected boolean not null default false,
  requires_2fa boolean not null default false,
  account_mode text,
  currency text,
  last_balance numeric,
  last_connected_at timestamptz,
  encrypted_password text,
  credentials_saved_at timestamptz,
  created_at timestamptz not null default timezone('utc'::text, now()),
  updated_at timestamptz not null default timezone('utc'::text, now())
);

alter table public.bullex_connections
  add column if not exists encrypted_password text;

alter table public.bullex_connections
  add column if not exists credentials_saved_at timestamptz;

create unique index if not exists bullex_connections_user_id_key
  on public.bullex_connections (user_id);

create table if not exists public.market_assets (
  id bigint generated always as identity primary key,
  user_id text not null references public.users(id) on delete cascade,
  active_id integer,
  symbol text not null,
  name text,
  enabled boolean not null default true,
  payout numeric,
  last_seen_at timestamptz,
  created_at timestamptz not null default timezone('utc'::text, now()),
  updated_at timestamptz not null default timezone('utc'::text, now())
);

create unique index if not exists market_assets_user_symbol_key
  on public.market_assets (user_id, symbol);

create table if not exists public.robot_states (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.users(id) on delete cascade,
  state jsonb not null default '{}'::jsonb,
  enabled boolean not null default false,
  account_mode text not null default 'REAL',
  strategy_mode text not null default 'conservative',
  entry_value numeric not null default 2,
  cycle_minutes integer not null default 5,
  min_confidence integer not null default 80,
  min_payout numeric not null default 80,
  stop_win numeric not null default 50,
  stop_loss numeric not null default 30,
  wins integer not null default 0,
  losses integer not null default 0,
  profit numeric not null default 0,
  accuracy numeric not null default 0,
  connected boolean not null default false,
  active_mode text,
  connection_checked_at timestamptz,
  connection_status_source text not null default 'cached',
  connection_failure_count integer not null default 0,
  state_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc'::text, now()),
  updated_at timestamptz not null default timezone('utc'::text, now())
);

create unique index if not exists robot_states_id_key
  on public.robot_states (id);

create unique index if not exists robot_states_user_id_key
  on public.robot_states (user_id);

create table if not exists public.robot_user_settings (
  user_id text primary key references public.users(id) on delete cascade,
  entry_value numeric not null default 2,
  stop_win numeric not null default 50,
  stop_loss numeric not null default 30,
  cycle_minutes integer not null default 5,
  min_confidence integer not null default 80,
  min_payout numeric not null default 80,
  strategy_mode text not null default 'conservative',
  account_mode text not null default 'REAL',
  timeframe text not null default 'M1',
  market_mode text not null default 'OTC',
  allow_real boolean not null default true,
  confirm_real boolean not null default true,
  max_entries_per_cycle integer not null default 1,
  martingale_enabled boolean not null default false,
  martingale_steps integer not null default 1,
  martingale_multiplier numeric not null default 2,
  created_at timestamptz not null default timezone('utc'::text, now()),
  updated_at timestamptz not null default timezone('utc'::text, now())
);

create table if not exists public.robot_trades (
  id bigint generated always as identity primary key,
  user_id text not null references public.users(id) on delete cascade,
  order_id text not null,
  active text,
  direction text,
  entry_value numeric,
  result text,
  payout numeric,
  profit numeric,
  executed_at timestamptz not null default timezone('utc'::text, now()),
  trade_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc'::text, now()),
  updated_at timestamptz not null default timezone('utc'::text, now())
);

create unique index if not exists robot_trades_user_order_key
  on public.robot_trades (user_id, order_id);

create table if not exists public.robot_trade_history (
  id bigint generated always as identity primary key,
  user_id text not null references public.users(id) on delete cascade,
  created_at timestamptz not null default timezone('utc'::text, now()),
  account_mode text not null check (account_mode = 'REAL'),
  active text not null,
  direction text not null check (direction in ('CALL', 'PUT')),
  amount numeric not null,
  confidence numeric not null,
  payout numeric not null,
  order_id text not null,
  result text not null check (result in ('WIN', 'LOSS')),
  profit numeric not null,
  opened_at timestamptz not null,
  finished_at timestamptz not null,
  timeframe text not null,
  is_gale boolean not null default false,
  gale_step integer not null default 0,
  parent_order_id text,
  cycle_result text,
  final_result text,
  original_amount numeric not null default 0,
  gale_amount numeric not null default 0,
  analysis_json jsonb not null default '{}'::jsonb
);

create unique index if not exists robot_trade_history_user_order_key
  on public.robot_trade_history (user_id, order_id);

create index if not exists robot_trade_history_user_finished_idx
  on public.robot_trade_history (user_id, finished_at desc);

create table if not exists public.robot_history (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.users(id) on delete cascade,
  event_type text,
  history_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc'::text, now()),
  updated_at timestamptz not null default timezone('utc'::text, now())
);

create index if not exists robot_history_user_created_at_idx
  on public.robot_history (user_id, created_at desc);

create table if not exists public.robot_restore_status (
  user_id text primary key references public.users(id) on delete cascade,
  session_restored boolean not null default false,
  robot_restored boolean not null default false,
  last_restore_at timestamptz,
  updated_at timestamptz not null default timezone('utc'::text, now())
);

create unique index if not exists robot_restore_status_user_id_key
  on public.robot_restore_status (user_id);

-- Triggers robô
drop trigger if exists set_users_updated_at on public.users;
create trigger set_users_updated_at
before update on public.users
for each row execute function public.set_updated_at();

drop trigger if exists set_bullex_connections_updated_at on public.bullex_connections;
create trigger set_bullex_connections_updated_at
before update on public.bullex_connections
for each row execute function public.set_updated_at();

drop trigger if exists set_market_assets_updated_at on public.market_assets;
create trigger set_market_assets_updated_at
before update on public.market_assets
for each row execute function public.set_updated_at();

drop trigger if exists sync_robot_states_state_alias on public.robot_states;
create trigger sync_robot_states_state_alias
before insert or update on public.robot_states
for each row execute function public.sync_robot_state_json_alias();

drop trigger if exists set_robot_states_updated_at on public.robot_states;
create trigger set_robot_states_updated_at
before update on public.robot_states
for each row execute function public.set_updated_at();

drop trigger if exists set_robot_user_settings_updated_at on public.robot_user_settings;
create trigger set_robot_user_settings_updated_at
before update on public.robot_user_settings
for each row execute function public.set_updated_at();

drop trigger if exists set_robot_trades_updated_at on public.robot_trades;
create trigger set_robot_trades_updated_at
before update on public.robot_trades
for each row execute function public.set_updated_at();

drop trigger if exists set_robot_history_updated_at on public.robot_history;
create trigger set_robot_history_updated_at
before update on public.robot_history
for each row execute function public.set_updated_at();

drop trigger if exists set_robot_restore_status_updated_at on public.robot_restore_status;
create trigger set_robot_restore_status_updated_at
before update on public.robot_restore_status
for each row execute function public.set_updated_at();

-- RLS robô: sem policies → anon/authenticated bloqueados; service_role bypassa
alter table public.users enable row level security;
alter table public.bullex_connections enable row level security;
alter table public.market_assets enable row level security;
alter table public.robot_states enable row level security;
alter table public.robot_user_settings enable row level security;
alter table public.robot_trades enable row level security;
alter table public.robot_trade_history enable row level security;
alter table public.robot_history enable row level security;
alter table public.robot_restore_status enable row level security;

revoke all on public.users from anon, authenticated;
revoke all on public.bullex_connections from anon, authenticated;
revoke all on public.market_assets from anon, authenticated;
revoke all on public.robot_states from anon, authenticated;
revoke all on public.robot_user_settings from anon, authenticated;
revoke all on public.robot_trades from anon, authenticated;
revoke all on public.robot_trade_history from anon, authenticated;
revoke all on public.robot_history from anon, authenticated;
revoke all on public.robot_restore_status from anon, authenticated;

grant all on public.users to service_role;
grant all on public.bullex_connections to service_role;
grant all on public.market_assets to service_role;
grant all on public.robot_states to service_role;
grant all on public.robot_user_settings to service_role;
grant all on public.robot_trades to service_role;
grant all on public.robot_trade_history to service_role;
grant all on public.robot_history to service_role;
grant all on public.robot_restore_status to service_role;
grant usage, select on all sequences in schema public to service_role;

-- ============================================================================
-- PARTE 2 — MULTI-TENANT, PERFIS, RBAC, ADMIN
-- ============================================================================

create table if not exists public.companies (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text not null unique,
  created_at timestamptz not null default timezone('utc', now())
);

insert into public.companies (id, name, slug)
values ('00000000-0000-0000-0000-000000000001', 'ElCapo', 'elcapo')
on conflict (id) do nothing;

create table if not exists public.user_access_profiles (
  user_id uuid primary key references auth.users (id) on delete cascade,
  company_id uuid not null references public.companies(id),
  name text,
  email text not null,
  plan_name text not null default 'Mensal',
  plan_status text not null default 'expired'
    check (plan_status in ('active', 'expired', 'trial', 'canceled')),
  amount numeric(12, 2) not null default 0,
  currency text not null default 'BRL',
  started_at timestamptz,
  expires_at timestamptz,
  next_billing_at timestamptz,
  grant_access boolean not null default false,
  approval_status text not null default 'approved'
    check (approval_status in ('pending', 'approved', 'rejected')),
  is_admin boolean not null default false,
  phone text,
  trader_id text,
  plan_id text,
  account_type text not null default 'client'
    check (account_type in ('trial', 'client', 'marketing')),
  payment_status text not null default 'pending'
    check (
      payment_status in (
        'not_applicable',
        'not_required',
        'pending',
        'paid',
        'overdue',
        'canceled',
        'refunded',
        'chargeback',
        'refused'
      )
    ),
  marketing_mode text,
  marketing_win_rate integer,
  deleted_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint user_access_profiles_marketing_mode_check check (
    (account_type <> 'marketing' and marketing_mode is null)
    or (account_type = 'marketing' and marketing_mode = 'simulation')
  ),
  constraint user_access_profiles_marketing_rate_check check (
    marketing_win_rate is null
    or (account_type = 'marketing' and marketing_win_rate between 0 and 100)
  )
);

create unique index if not exists user_access_profiles_email_idx
  on public.user_access_profiles (lower(email));

create unique index if not exists user_access_profiles_company_trader_idx
  on public.user_access_profiles (company_id, trader_id)
  where trader_id is not null and deleted_at is null;

create index if not exists user_access_profiles_company_id_idx
  on public.user_access_profiles (company_id);

create index if not exists user_access_profiles_company_type_idx
  on public.user_access_profiles (company_id, account_type, created_at desc);

create index if not exists user_access_profiles_company_stage_idx
  on public.user_access_profiles (company_id, account_type, deleted_at, created_at desc);

create index if not exists user_access_profiles_company_email_idx
  on public.user_access_profiles (company_id, lower(email));

drop trigger if exists trg_user_access_profiles_updated_at on public.user_access_profiles;
create trigger trg_user_access_profiles_updated_at
before update on public.user_access_profiles
for each row execute function public.set_current_timestamp_updated_at();

create table if not exists public.security_roles (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  role_key text not null,
  name text not null,
  is_owner boolean not null default false,
  can_manage_all_roles boolean not null default false,
  created_at timestamptz not null default timezone('utc', now()),
  unique (company_id, role_key)
);

create index if not exists security_roles_company_id_idx
  on public.security_roles (company_id);

create table if not exists public.security_permissions (
  permission_key text primary key,
  description text not null
);

create table if not exists public.role_permissions (
  company_id uuid not null references public.companies(id) on delete cascade,
  role_id uuid not null references public.security_roles(id) on delete cascade,
  permission_key text not null references public.security_permissions(permission_key),
  primary key (company_id, role_id, permission_key)
);

create index if not exists role_permissions_company_id_idx
  on public.role_permissions (company_id);

create table if not exists public.admin_role_assignments (
  company_id uuid not null references public.companies(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role_id uuid not null references public.security_roles(id) on delete restrict,
  job_title text not null,
  created_at timestamptz not null default timezone('utc', now()),
  deleted_at timestamptz,
  primary key (company_id, user_id)
);

create index if not exists admin_role_assignments_company_id_idx
  on public.admin_role_assignments (company_id);

create table if not exists public.role_assignable_roles (
  company_id uuid not null references public.companies(id) on delete cascade,
  actor_role_id uuid not null references public.security_roles(id) on delete cascade,
  target_role_id uuid not null references public.security_roles(id) on delete cascade,
  primary key (company_id, actor_role_id, target_role_id)
);

create index if not exists role_assignable_roles_company_id_idx
  on public.role_assignable_roles (company_id);

-- Legado (mantido por compatibilidade com migrations antigas)
create table if not exists public.admin_audit_events (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  actor_user_id uuid not null references auth.users(id),
  target_user_id uuid references auth.users(id),
  action text not null,
  request_id text,
  context jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists admin_audit_events_company_created_idx
  on public.admin_audit_events (company_id, created_at desc);

create table if not exists public.impersonation_sessions (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  actor_user_id uuid not null references auth.users(id),
  target_user_id uuid not null references auth.users(id),
  reason text not null,
  token_hash text not null unique,
  read_only boolean not null default true,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  check (actor_user_id <> target_user_id)
);

create index if not exists impersonation_company_actor_idx
  on public.impersonation_sessions (company_id, actor_user_id, expires_at desc);

-- Canônico (usado pelo backend atual)
create table if not exists public.support_access_sessions (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  actor_user_id uuid not null references auth.users(id) on delete restrict,
  subject_user_id uuid not null references auth.users(id) on delete restrict,
  reason text not null check (char_length(trim(reason)) >= 10),
  token_hash text not null unique,
  scopes text[] not null default array['account.view', 'broker_account.edit']::text[],
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  check (actor_user_id <> subject_user_id),
  check (expires_at <= created_at + interval '15 minutes'),
  check (
    scopes <@ array['account.view', 'broker_account.edit']::text[]
    and scopes @> array['account.view', 'broker_account.edit']::text[]
  )
);

create index if not exists support_access_sessions_company_id_idx
  on public.support_access_sessions (company_id);
create index if not exists support_access_sessions_company_actor_idx
  on public.support_access_sessions (company_id, actor_user_id, expires_at desc);
create index if not exists support_access_sessions_company_subject_idx
  on public.support_access_sessions (company_id, subject_user_id, expires_at desc);

create table if not exists public.admin_customer_events (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  actor_user_id uuid not null references auth.users(id) on delete restrict,
  subject_user_id uuid references auth.users(id) on delete restrict,
  action text not null,
  before jsonb not null default '{}'::jsonb,
  after jsonb not null default '{}'::jsonb,
  context jsonb not null default '{}'::jsonb,
  request_id text not null,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists admin_customer_events_company_id_idx
  on public.admin_customer_events (company_id);
create index if not exists admin_customer_events_company_subject_idx
  on public.admin_customer_events (company_id, subject_user_id, created_at desc);

drop trigger if exists trg_admin_customer_events_immutable on public.admin_customer_events;
create trigger trg_admin_customer_events_immutable
before update or delete on public.admin_customer_events
for each row execute function public.reject_admin_customer_event_mutation();

create table if not exists public.marketing_simulated_trades (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  synthetic_sequence integer not null,
  result text not null check (result in ('WIN', 'LOSS')),
  asset text not null,
  direction text not null check (direction in ('CALL', 'PUT')),
  amount numeric(12, 2) not null,
  payout integer not null,
  profit numeric(12, 2) not null,
  is_simulated boolean not null default true check (is_simulated = true),
  source text not null default 'marketing_demo' check (source = 'marketing_demo'),
  broker_order_id text,
  created_at timestamptz not null default timezone('utc', now()),
  unique (company_id, user_id, synthetic_sequence)
);

create index if not exists marketing_simulated_trades_broker_order_id_idx
  on public.marketing_simulated_trades (company_id, user_id, broker_order_id)
  where broker_order_id is not null;

create table if not exists public.subscription_revenue_events (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete restrict,
  event_type text not null check (event_type in ('sale', 'renewal')),
  amount numeric(14, 2) not null check (amount >= 0),
  currency text not null default 'BRL' check (char_length(currency) = 3),
  status text not null default 'confirmed'
    check (status in ('confirmed', 'refunded', 'cancelled')),
  source text not null default 'billing_backend',
  source_reference text,
  occurred_at timestamptz not null,
  created_at timestamptz not null default timezone('utc', now())
);

create unique index if not exists revenue_company_source_reference_idx
  on public.subscription_revenue_events (company_id, source, source_reference)
  where source_reference is not null;

create index if not exists revenue_company_occurred_idx
  on public.subscription_revenue_events (company_id, occurred_at desc)
  where status = 'confirmed';

create table if not exists public.client_lifecycle_events (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  event_type text not null
    check (event_type in ('client_activated', 'client_inactivated', 'trial_started')),
  occurred_at timestamptz not null,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists lifecycle_company_occurred_idx
  on public.client_lifecycle_events (company_id, occurred_at desc);

create index if not exists lifecycle_company_user_idx
  on public.client_lifecycle_events (company_id, user_id, occurred_at desc);

-- ============================================================================
-- PARTE 3 — BILLING (Cakto)
-- ============================================================================

create table if not exists public.billing_plans (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  slug text not null,
  name text not null,
  description text not null default '',
  price numeric(14, 2) not null check (price >= 0),
  currency text not null default 'BRL' check (currency = 'BRL'),
  billing_interval_months integer not null check (billing_interval_months >= 1),
  features jsonb not null default '[]'::jsonb check (jsonb_typeof(features) = 'array'),
  is_featured boolean not null default false,
  is_active boolean not null default false,
  display_order integer not null default 0,
  cakto_product_id text,
  cakto_offer_id text,
  checkout_url text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  deleted_at timestamptz,
  unique (company_id, slug),
  check (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  check (
    checkout_url is null
    or checkout_url ~ '^https://pay\.cakto\.com\.br(/|$)'
  ),
  check (
    not is_active
    or (
      cakto_product_id is not null
      and cakto_offer_id is not null
      and checkout_url is not null
    )
  )
);

create unique index if not exists billing_plans_cakto_offer_unique_idx
  on public.billing_plans (cakto_offer_id)
  where cakto_offer_id is not null and deleted_at is null;

create index if not exists billing_plans_company_product_idx
  on public.billing_plans (company_id, cakto_product_id)
  where cakto_product_id is not null and deleted_at is null;

create index if not exists billing_plans_company_display_idx
  on public.billing_plans (company_id, is_active, display_order, name)
  where deleted_at is null;

create table if not exists public.billing_events (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  provider text not null default 'cakto' check (provider = 'cakto'),
  provider_event_key text not null,
  event_name text not null check (
    event_name in (
      'purchase_approved',
      'purchase_refused',
      'refund',
      'chargeback',
      'subscription_created',
      'subscription_canceled',
      'subscription_renewed',
      'subscription_renewal_refused',
      'pix_gerado',
      'boleto_gerado',
      'picpay_gerado',
      'openfinance_nubank_gerado',
      'initiate_checkout',
      'checkout_abandonment'
    )
  ),
  status text not null check (
    status in (
      'approved',
      'pending',
      'refused',
      'refunded',
      'chargeback',
      'canceled',
      'informative'
    )
  ),
  plan_id uuid not null references public.billing_plans(id) on delete restrict,
  user_id uuid references auth.users(id) on delete set null,
  provider_reference text not null,
  subscription_reference text,
  amount numeric(14, 2) not null default 0 check (amount >= 0),
  currency text not null default 'BRL' check (currency = 'BRL'),
  occurred_at timestamptz not null,
  received_at timestamptz not null default timezone('utc', now()),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  unique (provider, company_id, provider_event_key)
);

create index if not exists billing_events_company_occurred_idx
  on public.billing_events (company_id, occurred_at desc);

create index if not exists billing_events_company_user_idx
  on public.billing_events (company_id, user_id, occurred_at desc)
  where user_id is not null;

create index if not exists billing_events_company_status_idx
  on public.billing_events (company_id, status, occurred_at desc);

create index if not exists billing_events_company_subscription_idx
  on public.billing_events (company_id, subscription_reference, occurred_at desc)
  where subscription_reference is not null;

drop trigger if exists trg_billing_events_immutable on public.billing_events;
create trigger trg_billing_events_immutable
before update or delete on public.billing_events
for each row execute function public.reject_billing_event_mutation();

create table if not exists public.billing_provider_state (
  company_id uuid not null references public.companies(id) on delete cascade,
  provider text not null default 'cakto' check (provider = 'cakto'),
  last_event_at timestamptz,
  last_reconciled_at timestamptz,
  updated_at timestamptz not null default timezone('utc', now()),
  primary key (company_id, provider)
);

create index if not exists billing_provider_state_company_idx
  on public.billing_provider_state (company_id);

-- ============================================================================
-- PARTE 4 — WEBHOOKS DE SAÍDA
-- ============================================================================

create table if not exists public.outgoing_webhook_endpoints (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  name text not null check (char_length(btrim(name)) between 1 and 120),
  url text not null check (url ~ '^https://'),
  subscribed_events text[] not null check (cardinality(subscribed_events) > 0),
  encrypted_secret text not null,
  secret_version integer not null default 1 check (secret_version > 0),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (company_id, id)
);

create index if not exists outgoing_webhook_endpoints_company_active_idx
  on public.outgoing_webhook_endpoints (company_id, is_active, created_at desc);

create index if not exists outgoing_webhook_endpoints_events_idx
  on public.outgoing_webhook_endpoints using gin (subscribed_events);

create table if not exists public.domain_event_outbox (
  id uuid not null,
  company_id uuid not null references public.companies(id) on delete cascade,
  event_type text not null,
  subject_user_id uuid null,
  request_id text not null,
  payload jsonb not null,
  occurred_at timestamptz not null,
  created_at timestamptz not null default now(),
  processed_at timestamptz null,
  primary key (company_id, id)
);

create index if not exists domain_event_outbox_pending_idx
  on public.domain_event_outbox (company_id, created_at)
  where processed_at is null;

create index if not exists domain_event_outbox_subject_idx
  on public.domain_event_outbox (company_id, subject_user_id, occurred_at desc);

create table if not exists public.webhook_deliveries (
  id uuid not null,
  company_id uuid not null references public.companies(id) on delete cascade,
  endpoint_id uuid not null,
  event_id uuid not null,
  request_id text not null,
  status text not null check (status in ('pending', 'delivered', 'retrying', 'failed')),
  attempt_count integer not null default 0 check (attempt_count >= 0),
  response_status integer null check (response_status between 100 and 599),
  latency_ms integer null check (latency_ms >= 0),
  next_attempt_at timestamptz null,
  last_error_code text null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (company_id, id),
  unique (company_id, endpoint_id, event_id),
  foreign key (company_id, endpoint_id)
    references public.outgoing_webhook_endpoints(company_id, id) on delete restrict,
  foreign key (company_id, event_id)
    references public.domain_event_outbox(company_id, id) on delete restrict
);

create index if not exists webhook_deliveries_due_idx
  on public.webhook_deliveries (company_id, status, next_attempt_at)
  where status in ('pending', 'retrying');

create index if not exists webhook_deliveries_endpoint_idx
  on public.webhook_deliveries (company_id, endpoint_id, created_at desc);

-- ============================================================================
-- PARTE 5 — FUNÇÕES DE AUTENTICAÇÃO / ADMIN / BILLING / WEBHOOKS
-- ============================================================================

create or replace function public.current_company_id()
returns uuid
language sql
stable
as $$
  select nullif(auth.jwt() -> 'app_metadata' ->> 'company_id', '')::uuid;
$$;

create or replace function public.is_admin_user(target_user_id uuid default auth.uid())
returns boolean
language sql
stable
as $$
  select exists (
    select 1
    from public.user_access_profiles profile
    where profile.user_id = coalesce(target_user_id, auth.uid())
      and profile.is_admin = true
  )
  or coalesce((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin', false)
  or coalesce((auth.jwt() -> 'app_metadata' ->> 'is_admin')::boolean, false);
$$;

create or replace function public.handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  resolved_company_id uuid;
begin
  resolved_company_id := coalesce(
    nullif(new.raw_app_meta_data ->> 'company_id', '')::uuid,
    '00000000-0000-0000-0000-000000000001'::uuid
  );

  insert into public.user_access_profiles (
    user_id,
    company_id,
    name,
    email,
    plan_name,
    plan_status,
    grant_access,
    is_admin
  )
  values (
    new.id,
    resolved_company_id,
    coalesce(new.raw_user_meta_data ->> 'name', split_part(new.email, '@', 1)),
    new.email,
    'Mensal',
    'expired',
    false,
    coalesce((new.raw_app_meta_data ->> 'is_admin')::boolean, false)
  )
  on conflict (user_id) do update
  set
    company_id = excluded.company_id,
    name = excluded.name,
    email = excluded.email,
    is_admin = excluded.is_admin,
    updated_at = timezone('utc', now());

  return new;
end;
$$;

drop trigger if exists on_auth_user_created_access_profile on auth.users;
create trigger on_auth_user_created_access_profile
after insert on auth.users
for each row execute function public.handle_new_auth_user();

create or replace function public.admin_set_user_access(
  target_user_id uuid,
  next_plan_status text default null,
  next_plan_name text default null,
  next_amount numeric default null,
  next_currency text default null,
  next_started_at timestamptz default null,
  next_expires_at timestamptz default null,
  next_billing_at timestamptz default null,
  next_grant_access boolean default null,
  next_is_admin boolean default null
)
returns public.user_access_profiles
language plpgsql
security definer
set search_path = public
as $$
declare
  updated_row public.user_access_profiles;
begin
  if not public.is_admin_user() then
    raise exception 'Apenas administradores podem atualizar acessos.'
      using errcode = '42501';
  end if;

  update public.user_access_profiles profile
  set
    plan_status = coalesce(next_plan_status, profile.plan_status),
    plan_name = coalesce(next_plan_name, profile.plan_name),
    amount = coalesce(next_amount, profile.amount),
    currency = coalesce(next_currency, profile.currency),
    started_at = coalesce(next_started_at, profile.started_at),
    expires_at = coalesce(next_expires_at, profile.expires_at),
    next_billing_at = coalesce(next_billing_at, profile.next_billing_at),
    grant_access = coalesce(next_grant_access, profile.grant_access),
    is_admin = coalesce(next_is_admin, profile.is_admin)
  where profile.user_id = target_user_id
  returning * into updated_row;

  if updated_row.user_id is null then
    raise exception 'Usuario nao encontrado.'
      using errcode = 'P0002';
  end if;

  return updated_row;
end;
$$;

create or replace function public.promote_user_to_admin(
  target_email text,
  target_name text default null,
  activate_access boolean default true
)
returns uuid
language plpgsql
security definer
set search_path = public, auth
as $$
declare
  normalized_email text;
  auth_user auth.users%rowtype;
  next_now timestamptz := timezone('utc', now());
begin
  normalized_email := lower(trim(target_email));

  if normalized_email is null or normalized_email = '' then
    raise exception 'Informe um email valido.'
      using errcode = '22023';
  end if;

  select *
  into auth_user
  from auth.users
  where lower(email) = normalized_email
  limit 1;

  if auth_user.id is null then
    raise exception 'Usuario com email % nao encontrado em auth.users.', normalized_email
      using errcode = 'P0002';
  end if;

  update auth.users
  set
    raw_app_meta_data = coalesce(raw_app_meta_data, '{}'::jsonb)
      || jsonb_build_object(
        'role', 'admin',
        'is_admin', true,
        'company_id', '00000000-0000-0000-0000-000000000001'
      ),
    raw_user_meta_data = coalesce(raw_user_meta_data, '{}'::jsonb)
      || case
        when coalesce(trim(target_name), '') = '' then '{}'::jsonb
        else jsonb_build_object('name', trim(target_name))
      end,
    updated_at = next_now
  where id = auth_user.id;

  insert into public.user_access_profiles (
    user_id,
    company_id,
    name,
    email,
    plan_name,
    plan_status,
    amount,
    currency,
    started_at,
    expires_at,
    next_billing_at,
    grant_access,
    is_admin
  )
  values (
    auth_user.id,
    '00000000-0000-0000-0000-000000000001',
    coalesce(nullif(trim(target_name), ''), auth_user.raw_user_meta_data ->> 'name', split_part(auth_user.email, '@', 1)),
    auth_user.email,
    'Mensal',
    case when activate_access then 'active' else 'expired' end,
    0,
    'BRL',
    case when activate_access then next_now else null end,
    case when activate_access then next_now + interval '30 days' else null end,
    case when activate_access then next_now + interval '30 days' else null end,
    activate_access,
    true
  )
  on conflict (user_id) do update
  set
    name = excluded.name,
    email = excluded.email,
    plan_name = excluded.plan_name,
    plan_status = excluded.plan_status,
    started_at = excluded.started_at,
    expires_at = excluded.expires_at,
    next_billing_at = excluded.next_billing_at,
    grant_access = excluded.grant_access,
    is_admin = true,
    updated_at = next_now;

  insert into public.admin_role_assignments (company_id, user_id, role_id, job_title)
  values (
    '00000000-0000-0000-0000-000000000001',
    auth_user.id,
    '00000000-0000-0000-0000-000000001001',
    'Administrador'
  )
  on conflict (company_id, user_id) do update
  set
    role_id = excluded.role_id,
    job_title = excluded.job_title,
    deleted_at = null;

  return auth_user.id;
end;
$$;

create or replace function public.bootstrap_admin_user(
  target_email text,
  target_password text,
  target_name text default 'Administrador',
  activate_access boolean default true
)
returns uuid
language plpgsql
security definer
set search_path = public, auth, extensions
as $$
declare
  normalized_email text;
  normalized_name text;
  auth_user_id uuid;
  auth_instance_id uuid := coalesce(
    (select id from auth.instances limit 1),
    '00000000-0000-0000-0000-000000000000'::uuid
  );
  next_now timestamptz := timezone('utc', now());
  password_hash text;
begin
  normalized_email := lower(trim(target_email));
  normalized_name := coalesce(nullif(trim(target_name), ''), 'Administrador');

  if normalized_email is null or normalized_email = '' then
    raise exception 'Informe um email valido.'
      using errcode = '22023';
  end if;

  if target_password is null or length(trim(target_password)) < 6 then
    raise exception 'A senha deve ter pelo menos 6 caracteres.'
      using errcode = '22023';
  end if;

  -- crypt/gen_salt: tenta schema extensions e cai para public.
  begin
    password_hash := extensions.crypt(target_password, extensions.gen_salt('bf'));
  exception
    when undefined_function then
      password_hash := crypt(target_password, gen_salt('bf'));
    when undefined_object then
      password_hash := crypt(target_password, gen_salt('bf'));
  end;

  select id
  into auth_user_id
  from auth.users
  where lower(email) = normalized_email
  limit 1;

  if auth_user_id is null then
    auth_user_id := gen_random_uuid();

    -- Tokens vazios (não NULL): GoTrue quebra com "converting NULL to string"
    -- se confirmation/recovery/email_change tokens forem NULL.
    insert into auth.users (
      id,
      instance_id,
      aud,
      role,
      email,
      encrypted_password,
      email_confirmed_at,
      confirmation_token,
      recovery_token,
      email_change_token_new,
      email_change,
      email_change_token_current,
      reauthentication_token,
      phone_change,
      phone_change_token,
      raw_app_meta_data,
      raw_user_meta_data,
      created_at,
      updated_at
    )
    values (
      auth_user_id,
      auth_instance_id,
      'authenticated',
      'authenticated',
      normalized_email,
      password_hash,
      next_now,
      '',
      '',
      '',
      '',
      '',
      '',
      '',
      '',
      jsonb_build_object(
        'provider', 'email',
        'providers', jsonb_build_array('email'),
        'role', 'admin',
        'is_admin', true,
        'company_id', '00000000-0000-0000-0000-000000000001'
      ),
      jsonb_build_object('name', normalized_name),
      next_now,
      next_now
    );
  end if;

  if not exists (
    select 1
    from auth.identities
    where user_id = auth_user_id
      and provider = 'email'
  ) then
    insert into auth.identities (
      id,
      user_id,
      identity_data,
      provider,
      provider_id,
      last_sign_in_at,
      created_at,
      updated_at
    )
    values (
      gen_random_uuid(),
      auth_user_id,
      jsonb_build_object(
        'sub', auth_user_id::text,
        'email', normalized_email,
        'email_verified', true,
        'phone_verified', false
      ),
      'email',
      auth_user_id::text,
      next_now,
      next_now,
      next_now
    );
  end if;

  perform public.promote_user_to_admin(normalized_email, normalized_name, activate_access);

  return auth_user_id;
end;
$$;

create or replace function public.resolve_billing_plan_external(
  target_product_id text,
  target_offer_id text
)
returns setof public.billing_plans
language sql
stable
security definer
set search_path = public
as $$
  select plan.*
  from public.billing_plans plan
  where plan.deleted_at is null
    and target_offer_id is not null
    and plan.cakto_offer_id = target_offer_id
    and (
      target_product_id is null
      or plan.cakto_product_id = target_product_id
    )
  limit 1;
$$;

create or replace function public.billing_product_is_available_for_company(
  target_company_id uuid,
  target_product_id text
)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select
    target_product_id is not null
    and not exists (
      select 1
      from public.billing_plans plan
      where plan.deleted_at is null
        and plan.cakto_product_id = target_product_id
        and plan.company_id <> target_company_id
    );
$$;

create or replace function public.enqueue_domain_event(
  target_company_id uuid,
  target_event_id uuid,
  target_event_type text,
  target_subject_user_id uuid,
  target_request_id text,
  target_payload jsonb,
  target_occurred_at timestamptz
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.domain_event_outbox (
    id,
    company_id,
    event_type,
    subject_user_id,
    request_id,
    payload,
    occurred_at
  )
  values (
    target_event_id,
    target_company_id,
    target_event_type,
    target_subject_user_id,
    target_request_id,
    target_payload,
    target_occurred_at
  )
  on conflict (company_id, id) do nothing;

  insert into public.webhook_deliveries (
    id,
    company_id,
    endpoint_id,
    event_id,
    request_id,
    status,
    next_attempt_at
  )
  select
    gen_random_uuid(),
    target_company_id,
    endpoint.id,
    target_event_id,
    target_request_id,
    'pending',
    now()
  from public.outgoing_webhook_endpoints endpoint
  where endpoint.company_id = target_company_id
    and endpoint.is_active = true
    and target_event_type = any(endpoint.subscribed_events)
  on conflict (company_id, endpoint_id, event_id) do nothing;

  return found;
end;
$$;

create or replace view public.admin_user_overview
with (security_invoker = true)
as
select
  profile.user_id as id,
  profile.name,
  profile.email,
  profile.plan_name,
  profile.plan_status,
  profile.amount,
  profile.currency,
  profile.started_at,
  profile.expires_at,
  profile.next_billing_at,
  profile.grant_access,
  profile.is_admin,
  case
    when profile.plan_status = 'active' and profile.grant_access = true then 'active'
    when profile.plan_status = 'trial' and profile.grant_access = true then 'trial'
    when profile.plan_status = 'canceled' then 'canceled'
    else 'expired'
  end as status
from public.user_access_profiles profile;

-- ============================================================================
-- PARTE 6 — SEEDS (empresa, role owner, permissões, planos)
-- ============================================================================

insert into public.security_roles (
  id, company_id, role_key, name, is_owner, can_manage_all_roles
)
values (
  '00000000-0000-0000-0000-000000001001',
  '00000000-0000-0000-0000-000000000001',
  'platform-owner',
  'Proprietário da plataforma',
  true,
  true
)
on conflict (company_id, role_key) do update
set name = excluded.name, is_owner = true, can_manage_all_roles = true;

-- Ambas as famílias de chaves (legado + canônicas) — backend aceita as duas
insert into public.security_permissions (permission_key, description)
values
  ('clients.create', 'Criar clientes'),
  ('clients.edit', 'Editar clientes'),
  ('clients.update', 'Editar clientes (legado)'),
  ('clients.delete', 'Desativar clientes'),
  ('clients.view_history', 'Visualizar histórico administrativo'),
  ('clients.history.read', 'Ver histórico dos clientes (legado)'),
  ('clients.access_account', 'Abrir sessão temporária de suporte'),
  ('clients.impersonate', 'Acessar conta em modo de suporte (legado)'),
  ('admins.create', 'Criar administradores'),
  ('admins.edit', 'Editar acessos administrativos'),
  ('admins.update', 'Editar administradores (legado)'),
  ('admins.delete', 'Desativar administradores'),
  ('finance.view', 'Visualizar catálogo, histórico e métricas financeiras'),
  ('finance.plans.manage', 'Criar, editar e desativar planos'),
  ('finance.settings.manage', 'Visualizar configuração operacional financeira'),
  ('finance.reconcile', 'Executar reconciliação manual da Cakto'),
  ('webhooks.view', 'Visualizar destinos, catálogo e entregas de webhooks'),
  ('webhooks.manage', 'Criar, editar e desativar destinos de webhooks'),
  ('webhooks.replay', 'Reenviar entregas de webhooks')
on conflict (permission_key) do update
set description = excluded.description;

insert into public.role_permissions (company_id, role_id, permission_key)
select
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000001001',
  permission.permission_key
from public.security_permissions permission
on conflict do nothing;

insert into public.role_assignable_roles (company_id, actor_role_id, target_role_id)
values (
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000001001',
  '00000000-0000-0000-0000-000000001001'
)
on conflict do nothing;

insert into public.billing_plans (
  company_id,
  slug,
  name,
  description,
  price,
  currency,
  billing_interval_months,
  features,
  is_featured,
  is_active,
  display_order
)
values
  (
    '00000000-0000-0000-0000-000000000001',
    'mensal',
    'Mensal',
    'Cobrança recorrente mensal',
    147.90,
    'BRL',
    1,
    '[]'::jsonb,
    false,
    false,
    1
  ),
  (
    '00000000-0000-0000-0000-000000000001',
    'semestral',
    'Semestral',
    'Cobrança recorrente a cada seis meses',
    667.00,
    'BRL',
    6,
    '[]'::jsonb,
    true,
    false,
    2
  ),
  (
    '00000000-0000-0000-0000-000000000001',
    'anual',
    'Anual',
    'Cobrança recorrente anual',
    1447.90,
    'BRL',
    12,
    '[]'::jsonb,
    false,
    false,
    3
  )
on conflict (company_id, slug) do update
set
  name = excluded.name,
  description = excluded.description,
  price = excluded.price,
  currency = excluded.currency,
  billing_interval_months = excluded.billing_interval_months,
  is_featured = excluded.is_featured,
  display_order = excluded.display_order,
  updated_at = timezone('utc', now());

-- ============================================================================
-- PARTE 7 — RLS + POLICIES (admin / billing / webhooks)
-- ============================================================================

alter table public.companies enable row level security;
alter table public.user_access_profiles enable row level security;
alter table public.security_roles enable row level security;
alter table public.security_permissions enable row level security;
alter table public.role_permissions enable row level security;
alter table public.admin_role_assignments enable row level security;
alter table public.role_assignable_roles enable row level security;
alter table public.admin_audit_events enable row level security;
alter table public.impersonation_sessions enable row level security;
alter table public.support_access_sessions enable row level security;
alter table public.admin_customer_events enable row level security;
alter table public.marketing_simulated_trades enable row level security;
alter table public.subscription_revenue_events enable row level security;
alter table public.client_lifecycle_events enable row level security;
alter table public.billing_plans enable row level security;
alter table public.billing_events enable row level security;
alter table public.billing_provider_state enable row level security;
alter table public.outgoing_webhook_endpoints enable row level security;
alter table public.domain_event_outbox enable row level security;
alter table public.webhook_deliveries enable row level security;

drop policy if exists "companies_same_tenant_select" on public.companies;
create policy "companies_same_tenant_select"
on public.companies for select
using (id = public.current_company_id());

drop policy if exists "profiles_same_tenant_select" on public.user_access_profiles;
create policy "profiles_same_tenant_select"
on public.user_access_profiles for select
using (
  company_id = public.current_company_id()
  and (auth.uid() = user_id or public.is_admin_user())
);

drop policy if exists "profiles_same_tenant_admin_update" on public.user_access_profiles;
create policy "profiles_same_tenant_admin_update"
on public.user_access_profiles for update
using (company_id = public.current_company_id() and public.is_admin_user())
with check (company_id = public.current_company_id() and public.is_admin_user());

drop policy if exists "profiles_same_tenant_admin_insert" on public.user_access_profiles;
create policy "profiles_same_tenant_admin_insert"
on public.user_access_profiles for insert
with check (company_id = public.current_company_id() and public.is_admin_user());

drop policy if exists "roles_same_tenant_admin" on public.security_roles;
create policy "roles_same_tenant_admin"
on public.security_roles for all
using (company_id = public.current_company_id() and public.is_admin_user())
with check (company_id = public.current_company_id() and public.is_admin_user());

drop policy if exists "permissions_authenticated_read" on public.security_permissions;
create policy "permissions_authenticated_read"
on public.security_permissions for select
using (auth.uid() is not null);

drop policy if exists "role_permissions_same_tenant_admin" on public.role_permissions;
create policy "role_permissions_same_tenant_admin"
on public.role_permissions for all
using (company_id = public.current_company_id() and public.is_admin_user())
with check (company_id = public.current_company_id() and public.is_admin_user());

drop policy if exists "assignments_same_tenant_admin" on public.admin_role_assignments;
create policy "assignments_same_tenant_admin"
on public.admin_role_assignments for all
using (company_id = public.current_company_id() and public.is_admin_user())
with check (company_id = public.current_company_id() and public.is_admin_user());

drop policy if exists "assignable_roles_same_tenant_admin" on public.role_assignable_roles;
create policy "assignable_roles_same_tenant_admin"
on public.role_assignable_roles for all
using (company_id = public.current_company_id() and public.is_admin_user())
with check (company_id = public.current_company_id() and public.is_admin_user());

drop policy if exists "audit_same_tenant_admin_read" on public.admin_audit_events;
create policy "audit_same_tenant_admin_read"
on public.admin_audit_events for select
using (company_id = public.current_company_id() and public.is_admin_user());

drop policy if exists "impersonation_same_tenant_admin" on public.impersonation_sessions;
create policy "impersonation_same_tenant_admin"
on public.impersonation_sessions for select
using (company_id = public.current_company_id() and actor_user_id = auth.uid());

drop policy if exists "support_sessions_actor_same_tenant_read" on public.support_access_sessions;
create policy "support_sessions_actor_same_tenant_read"
on public.support_access_sessions for select
using (
  company_id = public.current_company_id()
  and actor_user_id = auth.uid()
);

drop policy if exists "admin_customer_events_same_tenant_read" on public.admin_customer_events;
create policy "admin_customer_events_same_tenant_read"
on public.admin_customer_events for select
using (
  company_id = public.current_company_id()
  and public.is_admin_user()
);

drop policy if exists "simulated_trades_owner_or_admin" on public.marketing_simulated_trades;
create policy "simulated_trades_owner_or_admin"
on public.marketing_simulated_trades for select
using (
  company_id = public.current_company_id()
  and (user_id = auth.uid() or public.is_admin_user())
);

drop policy if exists "revenue_same_tenant_admin_read" on public.subscription_revenue_events;
create policy "revenue_same_tenant_admin_read"
on public.subscription_revenue_events for select
using (
  company_id = public.current_company_id()
  and public.is_admin_user()
);

drop policy if exists "lifecycle_same_tenant_admin_read" on public.client_lifecycle_events;
create policy "lifecycle_same_tenant_admin_read"
on public.client_lifecycle_events for select
using (
  company_id = public.current_company_id()
  and public.is_admin_user()
);

drop policy if exists "billing_plans_same_tenant_read" on public.billing_plans;
create policy "billing_plans_same_tenant_read"
on public.billing_plans for select
using (
  company_id = public.current_company_id()
  and deleted_at is null
  and (is_active or public.is_admin_user())
);

drop policy if exists "billing_events_owner_or_admin_read" on public.billing_events;
create policy "billing_events_owner_or_admin_read"
on public.billing_events for select
using (
  company_id = public.current_company_id()
  and (user_id = auth.uid() or public.is_admin_user())
);

drop policy if exists "billing_provider_state_admin_read" on public.billing_provider_state;
create policy "billing_provider_state_admin_read"
on public.billing_provider_state for select
using (
  company_id = public.current_company_id()
  and public.is_admin_user()
);

drop policy if exists "outgoing_webhook_endpoints_admin_read" on public.outgoing_webhook_endpoints;
create policy "outgoing_webhook_endpoints_admin_read"
on public.outgoing_webhook_endpoints for select
using (
  company_id = public.current_company_id()
  and public.is_admin_user()
);

drop policy if exists "domain_event_outbox_admin_read" on public.domain_event_outbox;
create policy "domain_event_outbox_admin_read"
on public.domain_event_outbox for select
using (
  company_id = public.current_company_id()
  and public.is_admin_user()
);

drop policy if exists "webhook_deliveries_admin_read" on public.webhook_deliveries;
create policy "webhook_deliveries_admin_read"
on public.webhook_deliveries for select
using (
  company_id = public.current_company_id()
  and public.is_admin_user()
);

-- Escrita somente via service_role (backend)
revoke insert, update, delete on public.admin_audit_events from anon, authenticated;
revoke insert, update, delete on public.impersonation_sessions from anon, authenticated;
revoke insert, update, delete on public.support_access_sessions from anon, authenticated;
revoke insert, update, delete on public.admin_customer_events from anon, authenticated;
revoke insert, update, delete on public.marketing_simulated_trades from anon, authenticated;
revoke insert, update, delete on public.subscription_revenue_events from anon, authenticated;
revoke insert, update, delete on public.client_lifecycle_events from anon, authenticated;
revoke insert, update, delete on public.billing_plans from anon, authenticated;
revoke insert, update, delete on public.billing_events from anon, authenticated;
revoke insert, update, delete on public.billing_provider_state from anon, authenticated;
revoke insert, update, delete on public.outgoing_webhook_endpoints from anon, authenticated;
revoke insert, update, delete on public.domain_event_outbox from anon, authenticated;
revoke insert, update, delete on public.webhook_deliveries from anon, authenticated;

grant all on public.companies to service_role;
grant all on public.user_access_profiles to service_role;
grant all on public.security_roles to service_role;
grant all on public.security_permissions to service_role;
grant all on public.role_permissions to service_role;
grant all on public.admin_role_assignments to service_role;
grant all on public.role_assignable_roles to service_role;
grant all on public.admin_audit_events to service_role;
grant all on public.impersonation_sessions to service_role;
grant all on public.support_access_sessions to service_role;
grant all on public.admin_customer_events to service_role;
grant all on public.marketing_simulated_trades to service_role;
grant all on public.subscription_revenue_events to service_role;
grant all on public.client_lifecycle_events to service_role;
grant all on public.billing_plans to service_role;
grant all on public.billing_events to service_role;
grant all on public.billing_provider_state to service_role;
grant all on public.outgoing_webhook_endpoints to service_role;
grant all on public.domain_event_outbox to service_role;
grant all on public.webhook_deliveries to service_role;
grant usage, select on all sequences in schema public to service_role;

-- RPCs sensíveis só service_role
revoke all on function public.promote_user_to_admin(text, text, boolean)
  from public, anon, authenticated;
revoke all on function public.bootstrap_admin_user(text, text, text, boolean)
  from public, anon, authenticated;
revoke all on function public.resolve_billing_plan_external(text, text)
  from public, anon, authenticated;
revoke all on function public.billing_product_is_available_for_company(uuid, text)
  from public, anon, authenticated;
revoke all on function public.enqueue_domain_event(uuid, uuid, text, uuid, text, jsonb, timestamptz)
  from public, anon, authenticated;

grant execute on function public.promote_user_to_admin(text, text, boolean) to service_role;
grant execute on function public.bootstrap_admin_user(text, text, text, boolean) to service_role;
grant execute on function public.resolve_billing_plan_external(text, text) to service_role;
grant execute on function public.billing_product_is_available_for_company(uuid, text) to service_role;
grant execute on function public.enqueue_domain_event(uuid, uuid, text, uuid, text, jsonb, timestamptz)
  to service_role;

-- Comentários
comment on table public.user_access_profiles is
'Controle administrativo de acesso, plano e cobrança dos usuários.';
comment on view public.admin_user_overview is
'Visão pronta para listar usuários ativos, vencidos, em teste e administradores.';
comment on table public.admin_customer_events is
'Histórico administrativo append-only com snapshots sanitizados e correlation ID.';
comment on table public.support_access_sessions is
'Sessões de suporte de 15 minutos, ator e sujeito separados, com escopo mínimo.';
comment on table public.marketing_simulated_trades is
'Operações exclusivamente sintéticas e isoladas do histórico financeiro real.';
comment on table public.subscription_revenue_events is
'Ledger append-only de vendas e renovações confirmado pelo backend financeiro.';
comment on table public.client_lifecycle_events is
'Transições append-only usadas nas métricas administrativas por período.';
comment on table public.billing_plans is
'Catálogo multi-tenant de planos; contratação só é ativa após IDs e checkout Cakto.';
comment on table public.billing_events is
'Ledger append-only e idempotente dos webhooks e reconciliações Cakto, sem PII.';
comment on table public.billing_provider_state is
'Timestamps operacionais da integração por empresa; credenciais ficam somente no ambiente.';

-- ============================================================================
-- PRÓXIMO PASSO (rode SEPARADAMENTE após o bootstrap)
-- ============================================================================
-- select public.bootstrap_admin_user(
--   'admin@seudominio.com',
--   'TroqueEstaSenha123',
--   'Administrador',
--   true
-- );
-- ============================================================================


-- =============================================================================
-- Emails nativos (20260720100000_native_emails)
-- =============================================================================

-- Emails nativos: templates HTML por evento e entregas multi-tenant.

create table if not exists public.email_templates (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  event_type text not null,
  subject text not null check (char_length(btrim(subject)) between 1 and 200),
  html_body text not null check (char_length(btrim(html_body)) between 1 and 200000),
  is_enabled boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (company_id, event_type),
  unique (company_id, id)
);

create index if not exists email_templates_company_idx
  on public.email_templates (company_id, updated_at desc);

create table if not exists public.email_deliveries (
  id uuid not null,
  company_id uuid not null references public.companies(id) on delete cascade,
  event_id uuid null,
  event_type text not null,
  recipient_email_hash text not null,
  subject text not null,
  status text not null check (status in ('pending', 'delivered', 'retrying', 'failed')),
  attempt_count integer not null default 0 check (attempt_count >= 0),
  provider_message_id text null,
  latency_ms integer null check (latency_ms >= 0),
  next_attempt_at timestamptz null,
  last_error_code text null,
  request_id text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (company_id, id)
);

create unique index if not exists email_deliveries_event_uidx
  on public.email_deliveries (company_id, event_id, event_type)
  where event_id is not null;

create index if not exists email_deliveries_due_idx
  on public.email_deliveries (company_id, status, next_attempt_at)
  where status in ('pending', 'retrying');
create index if not exists email_deliveries_company_created_idx
  on public.email_deliveries (company_id, created_at desc);

alter table public.email_templates enable row level security;
alter table public.email_deliveries enable row level security;

drop policy if exists "email_templates_admin_read" on public.email_templates;
create policy "email_templates_admin_read"
on public.email_templates for select
using (
  company_id = public.current_company_id()
  and public.is_admin_user()
);

drop policy if exists "email_deliveries_admin_read" on public.email_deliveries;
create policy "email_deliveries_admin_read"
on public.email_deliveries for select
using (
  company_id = public.current_company_id()
  and public.is_admin_user()
);

revoke insert, update, delete on public.email_templates from anon, authenticated;
revoke insert, update, delete on public.email_deliveries from anon, authenticated;
grant select on public.email_templates to authenticated;
grant select on public.email_deliveries to authenticated;

insert into public.security_permissions (permission_key, description)
values
  ('emails.view', 'Visualizar templates e entregas de email'),
  ('emails.manage', 'Editar layouts HTML e enviar emails de teste')
on conflict (permission_key) do update
set description = excluded.description;

insert into public.role_permissions (company_id, role_id, permission_key)
select role.company_id, role.id, permission.permission_key
from public.security_roles role
cross join public.security_permissions permission
where role.is_owner = true
  and permission.permission_key in ('emails.view', 'emails.manage')
on conflict do nothing;

-- ============================================================================
-- PÓS-BOOTSTRAP 02 — índice fila de leads pending
-- ============================================================================
-- Índice parcial da fila de Pedidos (leads pending sem acesso).
-- Atualizado em 2026-08-07.
--
-- O índice company_approval_idx cobre approval_status genérico; este parcial
-- é menor e alinha exatamente ao filtro de GET /admin/clients?segment=pending.

create index if not exists user_access_profiles_pending_idx
  on public.user_access_profiles (company_id, created_at desc)
  where approval_status = 'pending'
    and grant_access = false
    and deleted_at is null;

comment on index public.user_access_profiles_pending_idx is
  'Acelera a aba Pedidos: pending + grant_access=false + não deletado';

-- ============================================================================
-- PÓS-BOOTSTRAP 03 — memória de padrões (por usuário)
-- ============================================================================
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

-- ============================================================================
-- PÓS-BOOTSTRAP 04 — memória de padrões GLOBAL
-- ============================================================================
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

-- ============================================================================
-- PÓS-BOOTSTRAP 05 — colunas estratégias nomeadas no histórico
-- ============================================================================
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
