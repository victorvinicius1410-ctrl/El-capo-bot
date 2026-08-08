import { useSyncExternalStore } from "react";

export type BullExLoginPhase =
  | "idle"
  | "connecting"
  | "authenticating"
  | "opening_connection"
  | "loading_balance"
  | "reconnecting"
  | "ready"
  | "failed"
  | "cancelled";

export interface BullExLoginState {
  phase: BullExLoginPhase;
  email: string | null;
  startedAt: number | null;
  backendStatus: string | null;
  failureMessage: string | null;
  visualTimeoutReached: boolean;
  isPending: boolean;
}

const DEFAULT_STATE: BullExLoginState = {
  phase: "idle",
  email: null,
  startedAt: null,
  backendStatus: null,
  failureMessage: null,
  visualTimeoutReached: false,
  isPending: false,
};
const states = new Map<string, BullExLoginState>();
const listeners = new Set<() => void>();
const key = (userId?: string | null) => userId ?? "__anonymous__";
const get = (userId?: string | null) => states.get(key(userId)) ?? DEFAULT_STATE;
const set = (userId: string | null | undefined, state: BullExLoginState) => {
  states.set(key(userId), state);
  listeners.forEach((listener) => listener());
};

/** Inicia a representação visual do login Bullex. */
export function startBullExLogin(email: string, userId?: string | null): void {
  set(userId, { ...DEFAULT_STATE, phase: "connecting", email, startedAt: Date.now(), isPending: true });
}

export function updateBullExLoginBackendStatus(status: string | null, userId?: string | null): void {
  const current = get(userId);
  if (current.isPending) set(userId, { ...current, backendStatus: status, phase: phaseForStatus(status) });
}

export function completeBullExLogin(userId?: string | null): void {
  set(userId, { ...get(userId), phase: "ready", isPending: false, failureMessage: null });
}

export function failBullExLogin(message: string, userId?: string | null): void {
  set(userId, { ...get(userId), phase: "failed", isPending: false, failureMessage: message });
}

export function cancelBullExLogin(userId?: string | null): void {
  set(userId, { ...get(userId), phase: "cancelled", isPending: false });
}

export function resetBullExLoginState(userId?: string | null): void {
  states.delete(key(userId));
  listeners.forEach((listener) => listener());
}

/** Lê isPending sem inscrever o caller em re-render (útil em effects). */
export function getBullExLoginPending(userId?: string | null): boolean {
  return get(userId).isPending;
}

export function markBullExLoginVisualTimeout(userId?: string | null): void {
  const current = get(userId);
  if (current.isPending) set(userId, { ...current, visualTimeoutReached: true });
}

/** Observa o estado compartilhado do fluxo de conexão Bullex. */
export function useBullExLoginState(userId?: string | null): BullExLoginState {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => get(userId),
    () => get(userId),
  );
}

export function getBullExLoginStepLabel(state: BullExLoginState): string {
  if (state.phase === "ready") return "Pronto.";
  if (state.phase === "failed") return state.failureMessage ?? "Falha ao conectar.";
  if (state.phase === "cancelled") return "Login cancelado.";
  const labels: Partial<Record<BullExLoginPhase, string>> = {
    authenticating: "Autenticando...",
    opening_connection: "Abrindo conexão...",
    loading_balance: "Carregando saldo...",
    reconnecting: "Reconectando automaticamente...",
  };
  return labels[state.phase] ?? "Conectando...";
}

function phaseForStatus(status: string | null): BullExLoginPhase {
  const normalized = status?.trim().toUpperCase();
  if (normalized === "CONNECTED") return "ready";
  if (normalized === "FAILED" || normalized === "LOGIN_FAILED") return "failed";
  if (normalized === "AUTHENTICATING") return "authenticating";
  if (normalized === "OPENING_CONNECTION" || normalized === "CONNECTING_SESSION") return "opening_connection";
  if (normalized === "LOADING_BALANCE" || normalized === "SYNCING_BALANCE") return "loading_balance";
  if (normalized === "RECONNECTING" || normalized === "SESSION_RECONNECTING") return "reconnecting";
  return "connecting";
}
