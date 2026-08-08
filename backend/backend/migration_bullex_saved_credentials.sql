-- =============================================================================
-- Migration: credenciais Bullex salvas (criptografadas) para auto-reconexão
-- =============================================================================
-- Execute no SQL Editor do Supabase (ou psql) ANTES do deploy do backend.
--
-- O que muda:
--   - Guarda a senha da corretora criptografada (AES-256-GCM no gateway).
--   - Permite o robô reconectar automaticamente com a tela fechada, sem o
--     cliente digitar email/senha de novo a cada queda de sessão/SSID.
--
-- Segurança:
--   - A coluna NUNCA deve ser lida pelo frontend (apenas service_role).
--   - O gateway grava o envelope EncryptionService (`v1.<payload>`).
--   - Em "Desconectar Bullex" a sessão cai; as credenciais permanecem até
--     o cliente usar "Esquecer credenciais salvas".
-- =============================================================================

alter table public.bullex_connections
  add column if not exists encrypted_password text;

alter table public.bullex_connections
  add column if not exists credentials_saved_at timestamptz;

comment on column public.bullex_connections.encrypted_password is
  'Senha Bullex criptografada (AES-256-GCM / EncryptionService v1). Nunca expor ao client.';

comment on column public.bullex_connections.credentials_saved_at is
  'Momento em que email+senha criptografados foram gravados para auto-reconexão.';

-- Garante RLS e grants (idempotente com o bootstrap)
alter table public.bullex_connections enable row level security;

revoke all on public.bullex_connections from anon, authenticated;
grant all on public.bullex_connections to service_role;
