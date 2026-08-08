/**
 * Janela após "Desconectar Bullex" em que auto-reconnect e preferStable
 * não devem desfazer o clique do usuário.
 */

const MANUAL_DISCONNECT_SUPPRESS_MS = 60_000;
let manualDisconnectUntilMs = 0;

/**
 * Marca desconexão explícita do usuário (Configurações → Desconectar).
 */
export function markManualBullexDisconnect(nowMs: number = Date.now()): void {
  manualDisconnectUntilMs = nowMs + MANUAL_DISCONNECT_SUPPRESS_MS;
}

/** True enquanto a janela pós-desconexão manual ainda está ativa. */
export function isManualBullexDisconnectActive(nowMs: number = Date.now()): boolean {
  return nowMs < manualDisconnectUntilMs;
}

/** Só para testes — zera o suppress. */
export function resetManualBullexDisconnectForTests(): void {
  manualDisconnectUntilMs = 0;
}
