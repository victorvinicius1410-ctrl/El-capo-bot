const TRIAL_START_KEY = "bullex_trial_start";
export const TRIAL_DAYS = 3;
export const TRIAL_DISCOUNT = 15;

/** Inicializa o marcador local do período de teste. */
export function initTrial(force = false): void {
  if (typeof window === "undefined") return;
  if (force || !localStorage.getItem(TRIAL_START_KEY)) {
    localStorage.setItem(TRIAL_START_KEY, String(Date.now()));
  }
}

/** Formata um intervalo em horas, minutos e segundos. */
export function formatRemaining(ms: number): string {
  const seconds = Math.floor(ms / 1000);
  return [Math.floor(seconds / 3600), Math.floor((seconds % 3600) / 60), seconds % 60]
    .map((part) => String(part).padStart(2, "0"))
    .join(":");
}
