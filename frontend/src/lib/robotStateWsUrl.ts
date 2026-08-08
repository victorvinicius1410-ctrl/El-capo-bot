/** Helpers puros de URL/constantes do WS do robô (testáveis sem carregar api.ts). */

export const ROBOT_WS_FALLBACK_POLL_MS = 30_000;
export const ROBOT_WS_PING_INTERVAL_MS = 20_000;

/** Converte `https://api…` em `wss://api…/ws/robot-state`. */
export function robotStateWsUrl(ticket: string, baseUrl: string): string {
  const http = baseUrl.replace(/\/+$/, "");
  const wsBase = http.replace(/^http:/i, "ws:").replace(/^https:/i, "wss:");
  const url = new URL(`${wsBase}/ws/robot-state`);
  url.searchParams.set("ticket", ticket);
  return url.toString();
}
