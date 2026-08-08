-- Registro público de leads: aprovação administrativa + dias de acesso.
-- Atualizado em 2026-08-06.

alter table public.user_access_profiles
  add column if not exists approval_status text not null default 'approved';

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'user_access_profiles_approval_status_check'
  ) then
    alter table public.user_access_profiles
      add constraint user_access_profiles_approval_status_check
      check (approval_status in ('pending', 'approved', 'rejected'));
  end if;
end $$;

comment on column public.user_access_profiles.approval_status is
  'pending = lead auto-cadastrado aguardando admin; approved = liberado (admin ou pagamento); rejected = recusado';

create index if not exists user_access_profiles_company_approval_idx
  on public.user_access_profiles (company_id, approval_status, created_at desc)
  where deleted_at is null;

-- Contas já existentes permanecem aprovadas (default).
update public.user_access_profiles
set approval_status = 'approved'
where approval_status is null
   or approval_status not in ('pending', 'approved', 'rejected');
