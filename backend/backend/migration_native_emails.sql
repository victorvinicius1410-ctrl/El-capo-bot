-- =============================================================================
-- El Capo — Emails nativos (templates + entregas)
-- Data: 2026-08-07
--
-- IMPORTANTE: rode no SQL Editor do Supabase do projeto da VPS
--   URL: https://dfxmasxpqpujxahwzpqn.supabase.co
--
-- Sem estas tabelas, Admin → E-mails falha ao listar/salvar (PGRST205).
-- O backend usa SERVICE ROLE (bypassa RLS) para escrever.
-- =============================================================================

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
grant all on public.email_templates to service_role;
grant all on public.email_deliveries to service_role;

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

notify pgrst, 'reload schema';
