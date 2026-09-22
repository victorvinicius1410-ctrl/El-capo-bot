/** Helpers puros de URL/constantes do WS do robô (testáveis sem carregar api.ts). */

export const ROBOT_WS_FALLBACK_POLL_MS = 30_000;
export const ROBOT_WS_PING_INTERVAL_MS = 20_000;
/**
 * Silêncio que caracteriza socket zumbi (OPEN, sem entregar nada).
 *
 * O servidor responde `pong` a todo ping, então em rede sã chega mensagem a
 * cada 20s mesmo com o robô parado. Duas janelas de ping + folga.
 */
export const ROBOT_WS_STALE_AFTER_MS = ROBOT_WS_PING_INTERVAL_MS * 2 + 5_000;

/**
 * True quando o socket está calado há tempo demais e deve ser derrubado.
 *
 * @param lastMessageAt - Epoch ms da última mensagem recebida (0 = nenhuma)
 * @param now - Relógio atual
 */
export function robotStateWsIsStale(lastMessageAt: number, now = Date.now()): boolean {
  if (!lastMessageAt) return false;
  return now - lastMessageAt > ROBOT_WS_STALE_AFTER_MS;
}

/** Converte `https://api…` em `wss://api…/ws/robot-state`. */
export function robotStateWsUrl(ticket: string, baseUrl: string): string {
  const http = baseUrl.replace(/\/+$/, "");
  const wsBase = http.replace(/^http:/i, "ws:").replace(/^https:/i, "wss:");
  const url = new URL(`${wsBase}/ws/robot-state`);
  url.searchParams.set("ticket", ticket);
  return url.toString();
}
