/**
 * Cache em memória da identidade lida em `GET /auth/session`.
 *
 * Sem isso, cada `apiRequest` (e o gate admin) dispara um round-trip extra de
 * sessão antes do endpoint real, dobrando a latência em navegação e polling.
 *
 * TTL padrão: 120s — sob carga de lançamento (muitos leads no painel), 30s
 * ainda gerava dezenas de `/auth/session` por minuto e saturava o gateway.
 */

export type SessionIdentity = { userId: string; email: string | null };

const DEFAULT_TTL_MS = 120_000;

let cached: { value: SessionIdentity | null; expiresAt: number } | null = null;

/**
 * Devolve a identidade cacheada se ainda estiver dentro do TTL.
 *
 * Args:
 *   nowMs: timestamp de referência (injetável em testes).
 *
 * Returns:
 *   Identidade cacheada ou `undefined` se o cache estiver vazio/expirado.
 */
export function readSessionIdentityCache(nowMs: number = Date.now()): SessionIdentity | null | undefined {
  if (!cached) return undefined;
  if (nowMs >= cached.expiresAt) {
    cached = null;
    return undefined;
  }
  return cached.value;
}

/**
 * Grava a identidade no cache com TTL curto.
 *
 * Args:
 *   value: identidade autenticada ou `null` (não autenticado).
 *   ttlMs: validade do cache em milissegundos.
 *   nowMs: timestamp de referência (injetável em testes).
 */
export function writeSessionIdentityCache(
  value: SessionIdentity | null,
  ttlMs: number = DEFAULT_TTL_MS,
  nowMs: number = Date.now(),
): void {
  cached = { value, expiresAt: nowMs + ttlMs };
}

/**
 * Invalida o cache (logout, 401, troca de usuário).
 */
export function clearSessionIdentityCache(): void {
  cached = null;
}

/**
 * TTL padrão usado por `getSessionIdentity` no cliente HTTP.
 */
export function sessionIdentityCacheTtlMs(): number {
  return DEFAULT_TTL_MS;
}
