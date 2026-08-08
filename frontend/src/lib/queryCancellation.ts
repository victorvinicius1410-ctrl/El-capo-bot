import { isCancelledError } from "@tanstack/query-core";

/**
 * Indica se o erro veio de cancelamento do React Query (não é falha de negócio).
 *
 * Args:
 *   error: valor rejeitado por ensureQueryData / fetchQuery / error boundary.
 *
 * Returns:
 *   true quando for CancelledError (ou equivalente por nome/mensagem).
 */
export function isQueryCancellationError(error: unknown): boolean {
  if (isCancelledError(error)) return true;
  if (error instanceof Error) {
    return error.name === "CancelledError" || error.message === "CancelledError";
  }
  return false;
}

/**
 * Erros que não devem prender o usuário na tela azul / “não carregou”.
 *
 * Inclui cancelamento de query e rejeições vazias (`undefined`/`null`) que o
 * Chrome mostra como “Uncaught undefined” e o React error boundary não captura
 * quando escapam como unhandledrejection.
 *
 * Args:
 *   error: valor rejeitado ou passado ao errorComponent.
 *
 * Returns:
 *   true quando a UI deve recuperar (spinner + invalidate) em vez de falhar.
 */
export function isBenignRouteLoadError(error: unknown): boolean {
  if (error == null) return true;
  if (isQueryCancellationError(error)) return true;
  if (typeof error === "string" && error.trim() === "") return true;
  return false;
}
