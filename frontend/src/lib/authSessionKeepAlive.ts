/**
 * Mantém a sessão do painel viva após o access token (~1h) expirar.
 *
 * O backend renova cookies em `GET /auth/session` e `POST /auth/refresh`
 * quando o refresh cookie httpOnly ainda é válido (até ~30 dias).
 */

/** Intervalo mínimo entre revalidações disparadas por foco/visibilidade. */
export const AUTH_REVALIDATE_MIN_INTERVAL_MS = 120_000;

/** Indica se a aba acabou de voltar ao foco após ficar oculta. */
export function shouldRevalidateAuthOnVisibilityChange(
  isVisible: boolean,
  wasHidden: boolean,
): boolean {
  return isVisible && wasHidden;
}

/**
 * Evita rajada de GET /auth/session ao alternar abas rapidamente.
 *
 * Args:
 *   nowMs: Timestamp atual (Date.now()).
 *   lastRevalidateAtMs: Última revalidação bem enfileirada.
 *   minIntervalMs: Intervalo mínimo entre tentativas.
 *
 * Returns:
 *   true se pode revalidar agora.
 */
export function canRevalidateAuthNow(
  nowMs: number,
  lastRevalidateAtMs: number,
  minIntervalMs: number = AUTH_REVALIDATE_MIN_INTERVAL_MS,
): boolean {
  if (lastRevalidateAtMs <= 0) return true;
  return nowMs - lastRevalidateAtMs >= minIntervalMs;
}

/**
 * Tenta obter a identidade; se falhar, renova a sessão uma vez e tenta de novo.
 *
 * Args:
 *   attempt: Lê a sessão atual (ex.: GET /auth/session).
 *   refresh: Chama POST /auth/refresh; true se renovou com sucesso.
 *
 * Returns:
 *   Identidade encontrada, ou null se refresh e segunda tentativa falharem.
 */
export async function withAuthRefreshRetry<T>(
  attempt: () => Promise<T | null>,
  refresh: () => Promise<boolean>,
): Promise<T | null> {
  const first = await attempt();
  if (first != null) return first;
  const renewed = await refresh();
  if (!renewed) return null;
  return attempt();
}
