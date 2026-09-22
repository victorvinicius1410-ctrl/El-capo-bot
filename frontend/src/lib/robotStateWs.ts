/**
 * Cliente WebSocket do estado do robô (canal primário).
 *
 * Fluxo: `GET /robot/ws-ticket` → `wss://…/ws/robot-state?ticket=…` →
 * mensagens `{type:"robot_state", data}` atualizam o React Query.
 * Ping a cada 20s; reconnect com backoff. Poll HTTP fica só como fallback.
 *
 * Ver docs/ROBOT_STATE_WEBSOCKET.md.
 */

import { apiConfig, apiRequest } from "./api.ts";
import {
  ROBOT_WS_FALLBACK_POLL_MS,
  ROBOT_WS_PING_INTERVAL_MS,
  ROBOT_WS_STALE_AFTER_MS,
  robotStateWsIsStale,
  robotStateWsUrl as buildRobotStateWsUrl,
} from "./robotStateWsUrl.ts";

export {
  ROBOT_WS_FALLBACK_POLL_MS,
  ROBOT_WS_PING_INTERVAL_MS,
  ROBOT_WS_STALE_AFTER_MS,
  robotStateWsIsStale,
  buildRobotStateWsUrl as robotStateWsUrl,
};

export type RobotStateWsHandlers = {
  onState: (data: Record<string, unknown>) => void;
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (error: unknown) => void;
};

function robotStateWsUrl(ticket: string, baseUrl = apiConfig.BASE_URL): string {
  return buildRobotStateWsUrl(ticket, baseUrl);
}

/** Obtém ticket one-shot do gateway. */
export async function fetchRobotWsTicket(): Promise<string> {
  const response = await apiRequest<{ ticket: string }>("/robot/ws-ticket");
  if (!response.ok || !response.data?.ticket) {
    throw new Error(response.ok === false ? response.error : "WS_TICKET_MISSING");
  }
  return response.data.ticket;
}

/**
 * Mantém uma conexão WS viva com reconnect.
 *
 * Returns:
 *   Função de cleanup (fecha socket e cancela timers).
 */
export function connectRobotStateWs(handlers: RobotStateWsHandlers): () => void {
  let closed = false;
  let socket: WebSocket | null = null;
  let pingTimer: ReturnType<typeof setInterval> | null = null;
  let watchdogTimer: ReturnType<typeof setInterval> | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let lastMessageAt = 0;
  let attempt = 0;

  const clearTimers = () => {
    if (pingTimer != null) {
      clearInterval(pingTimer);
      pingTimer = null;
    }
    if (watchdogTimer != null) {
      clearInterval(watchdogTimer);
      watchdogTimer = null;
    }
    if (reconnectTimer != null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };

  /**
   * Derruba socket zumbi: OPEN, mas sem entregar nada (nem o `pong` do ping).
   *
   * Sem isto o painel ficava dependendo de um canal morto — o `onclose` nunca
   * chega, o poll HTTP segue pausado e o placar congela no último valor até o
   * F5. Avisa `onClose` na hora (é o que religa o poll) e reconecta.
   */
  const dropStaleSocket = () => {
    if (closed || socket == null) return;
    const dead = socket;
    socket = null;
    dead.onopen = null;
    dead.onmessage = null;
    dead.onerror = null;
    dead.onclose = null;
    try {
      dead.close(4000);
    } catch {
      // socket já inutilizável: só os timers importam a partir daqui
    }
    clearTimers();
    handlers.onClose?.();
    scheduleReconnect();
  };

  const scheduleReconnect = () => {
    if (closed) return;
    const delay = Math.min(30_000, 1_000 * 2 ** Math.min(attempt, 4));
    attempt += 1;
    reconnectTimer = setTimeout(() => {
      void open();
    }, delay);
  };

  const open = async () => {
    if (closed) return;
    clearTimers();
    try {
      const ticket = await fetchRobotWsTicket();
      if (closed) return;
      const url = robotStateWsUrl(ticket);
      socket = new WebSocket(url);
      socket.onopen = () => {
        attempt = 0;
        lastMessageAt = Date.now();
        handlers.onOpen?.();
        pingTimer = setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: "ping" }));
          }
        }, ROBOT_WS_PING_INTERVAL_MS);
        watchdogTimer = setInterval(() => {
          if (robotStateWsIsStale(lastMessageAt)) dropStaleSocket();
        }, ROBOT_WS_PING_INTERVAL_MS);
      };
      socket.onmessage = (event) => {
        // Qualquer mensagem (inclusive o `pong`) conta como sinal de vida.
        lastMessageAt = Date.now();
        try {
          const message = JSON.parse(String(event.data)) as {
            type?: string;
            data?: Record<string, unknown>;
            error?: string;
          };
          if (message.type === "robot_state" && message.data && typeof message.data === "object") {
            handlers.onState(message.data);
          } else if (message.type === "error") {
            handlers.onError?.(message.error ?? "ROBOT_WS_ERROR");
          }
        } catch (error) {
          handlers.onError?.(error);
        }
      };
      socket.onerror = (event) => {
        handlers.onError?.(event);
      };
      socket.onclose = () => {
        clearTimers();
        handlers.onClose?.();
        scheduleReconnect();
      };
    } catch (error) {
      handlers.onError?.(error);
      scheduleReconnect();
    }
  };

  void open();

  return () => {
    closed = true;
    clearTimers();
    if (socket != null) {
      try {
        socket.close(1000);
      } catch {
        // ignore
      }
      socket = null;
    }
  };
}
