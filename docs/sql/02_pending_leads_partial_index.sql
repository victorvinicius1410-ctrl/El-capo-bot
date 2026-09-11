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
