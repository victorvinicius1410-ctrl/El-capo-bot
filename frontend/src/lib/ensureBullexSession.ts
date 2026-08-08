/**
 * Decide se o painel deve disparar auto-reconexão Bullex ao entrar no sistema.
 *
 * Se o cliente já salvou o login uma vez, a sessão da corretora pode cair
 * (SSID inválido / restart), mas o painel deve reconectar sozinho — sem pedir
 * email/senha de novo.
 */

import {
  isManualBullexDisconnectActive,
  markManualBullexDisconnect,
  resetManualBullexDisconnectForTests,
} from "./manualBullexDisconnect.ts";

export {
  isManualBullexDisconnectActive,
  markManualBullexDisconnect,
  resetManualBullexDisconnectForTests,
};

export interface EnsureBullexSessionInput {
  /** Conta já confirmada como conectada na Bullex. */
  connected: boolean;
  /** Há email/senha criptografados no servidor. */
  credentialsSaved: boolean;
  /** Já tentamos reconectar nesta visita (evita loop). */
  alreadyAttempted: boolean;
  /** Login/reconnect já em andamento (UI). */
  pendingConnect: boolean;
  /** Polling/queries ainda carregando o estado inicial. */
  stillLoading: boolean;
}

/**
 * Retorna true quando devemos chamar POST /bullex/reconnect automaticamente.
 */
export function shouldAutoReconnectBullex(input: EnsureBullexSessionInput): boolean {
  if (isManualBullexDisconnectActive()) return false;
  if (input.stillLoading) return false;
  if (input.connected) return false;
  if (!input.credentialsSaved) return false;
  if (input.alreadyAttempted) return false;
  if (input.pendingConnect) return false;
  return true;
}

export interface AutoReconnectAttemptResult {
  ok: boolean;
  error?: string;
}

export interface RunAutoReconnectOptions {
  /** Total de tentativas (1ª + retries). Padrão: 4 (1 + 3 retries). */
  maxAttempts?: number;
  /** Backoff entre tentativas, em ms. Reusa o último valor se faltar índice. */
  delaysMs?: number[];
  /** Interrompe o loop sem contar como falha final (aba trocada/unmount). */
  isCancelled?: () => boolean;
  /** Chamado antes de cada espera, para refletir "Reconectando..." na UI. */
  onRetryScheduled?: (attempt: number, delayMs: number) => void;
  /** Injeção para teste (evita esperar tempo real). */
  sleep?: (ms: number) => Promise<void>;
}

const DEFAULT_RETRY_DELAYS_MS = [5_000, 15_000, 40_000];

/**
 * Reconecta a Bullex com retry + backoff, em vez de desistir na 1ª falha.
 *
 * Antes disso, uma falha transitória (cooldown de 60s por usuário, rate
 * limit global da corretora, blip de rede logo após um deploy) fazia o
 * painel mostrar "desconectado" até o cliente trocar de aba ou clicar
 * manualmente em "Reconectar com login salvo". Ver `BULLEX_CREDENCIAIS.md`.
 */
export async function runAutoReconnectWithRetry(
  attempt: () => Promise<AutoReconnectAttemptResult>,
  options: RunAutoReconnectOptions = {},
): Promise<AutoReconnectAttemptResult> {
  const delays = options.delaysMs ?? DEFAULT_RETRY_DELAYS_MS;
  const maxAttempts = options.maxAttempts ?? delays.length + 1;
  const sleep = options.sleep ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  let lastResult: AutoReconnectAttemptResult = {
    ok: false,
    error: "Não foi possível reconectar a Bullex.",
  };

  for (let index = 0; index < maxAttempts; index += 1) {
    if (options.isCancelled?.()) return lastResult;
    lastResult = await attempt();
    if (lastResult.ok || options.isCancelled?.()) return lastResult;
    const isLastAttempt = index === maxAttempts - 1;
    if (isLastAttempt) return lastResult;
    const delayMs = delays[index] ?? delays[delays.length - 1] ?? 0;
    options.onRetryScheduled?.(index + 1, delayMs);
    await sleep(delayMs);
  }
  return lastResult;
}
