const TRIAL_START_KEY = "bullex_trial_start";
export const TRIAL_DAYS = 3;
export const TRIAL_DISCOUNT = 15;

const MS_PER_DAY = 86_400_000;

/** Inicializa o marcador local do período de teste. */
export function initTrial(force = false): void {
  if (typeof window === "undefined") return;
  if (force || !localStorage.getItem(TRIAL_START_KEY)) {
    localStorage.setItem(TRIAL_START_KEY, String(Date.now()));
  }
}

/** Milissegundos restantes até a expiração informada pelo servidor. */
export function remainingMs(expiresAt: string, nowMs = Date.now()): number {
  return Math.max(0, new Date(expiresAt).getTime() - nowMs);
}

/**
 * Dias restantes com base em ``expires_at`` (arredondamento ao inteiro mais próximo).
 *
 * Usa ``Math.round`` em vez de ``Math.ceil``: com ceil, um trial de 3 dias
 * recém-criado (``now + 3d``) podia aparecer como **4** se o relógio do
 * browser estivesse alguns segundos atrás do servidor
 * (``3 dias + 1s`` → ``ceil`` = 4).
 *
 * Args:
 *   expiresAt: ISO-8601 retornado pela API.
 *   nowMs: Referência temporal opcional (testes).
 *
 * Returns:
 *   Quantidade de dias entre 0 e 365.
 */
export function remainingDaysFromExpiresAt(expiresAt: string, nowMs = Date.now()): number {
  const ms = remainingMs(expiresAt, nowMs);
  if (ms <= 0) return 0;
  return Math.min(365, Math.max(0, Math.round(ms / MS_PER_DAY)));
}

/**
 * Valor inicial do campo "dias de teste" no admin, derivado de ``expires_at``.
 *
 * Args:
 *   expiresAt: Expiração persistida no perfil (nullable).
 *   fallback: Valor quando não há expiração (criação).
 *   nowMs: Referência temporal opcional (testes).
 *
 * Returns:
 *   String numérica entre 1 e 365.
 */
export function trialDaysFromExpiresAt(
  expiresAt: string | null | undefined,
  fallback = "7",
  nowMs = Date.now(),
): string {
  if (!expiresAt) return fallback;
  const days = remainingDaysFromExpiresAt(expiresAt, nowMs);
  if (days < 1) return fallback;
  return String(days);
}

/** Formata um intervalo em horas, minutos e segundos (legado). */
export function formatRemaining(ms: number): string {
  const seconds = Math.floor(ms / 1000);
  return [Math.floor(seconds / 3600), Math.floor((seconds % 3600) / 60), seconds % 60]
    .map((part) => String(part).padStart(2, "0"))
    .join(":");
}

/**
 * Formata tempo restante de trial de forma legível (dias + HH:MM:SS).
 *
 * Args:
 *   ms: Milissegundos restantes.
 *
 * Returns:
 *   Ex.: ``3 dias, 05:12:00`` ou ``05:12:00`` quando falta menos de 1 dia.
 */
export function formatTrialRemaining(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const days = Math.floor(totalSeconds / 86_400);
  const hours = Math.floor((totalSeconds % 86_400) / 3_600);
  const minutes = Math.floor((totalSeconds % 3_600) / 60);
  const seconds = totalSeconds % 60;
  const clock = [hours, minutes, seconds].map((part) => String(part).padStart(2, "0")).join(":");
  if (days <= 0) return clock;
  const dayLabel = days === 1 ? "1 dia" : `${days} dias`;
  return `${dayLabel}, ${clock}`;
}
