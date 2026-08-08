import type { RobotGaleInfo, RobotSignal, RobotState, RobotTrade } from "./robotState";
import { liveDisplayCountdownSeconds } from "./robotState";

export type RobotPresentationKind =
  | "loading"
  | "stopped"
  | "analyzing"
  | "entry"
  | "operation"
  | "result"
  | "rejected"
  | "gale";

export interface RobotPresentation {
  kind: RobotPresentationKind;
  title: string;
  detail: string | null;
  footer: string | null;
  signal: RobotSignal | null;
  trade: RobotTrade | null;
  gale: RobotGaleInfo | null;
  direction: string | null;
  result: "WIN" | "LOSS" | "DRAW" | null;
}

const REJECTION_DISPLAY_MS = 5_000;
let rejectionMemory: { key: string; reason: string; observedAt: number } | null = null;

/** Ajusta o rodapé do overlay conforme a fase da operação. */
export function formatCountdownFooter(
  countdown: string,
  flags: { operationInProgress: boolean; resultWaiting: boolean },
): string {
  if (flags.operationInProgress || flags.resultWaiting) return `Resultado em ${countdown}`;
  if (/^(Analisando por|Próxima análise em|Proxima analise em|Buscando melhor oportunidade)/i.test(countdown)) {
    return countdown;
  }
  return `Entrada em ${countdown}`;
}

/** Traduz motivos técnicos de rejeição/erro para linguagem amigável. */
export function humanizeRobotReason(reason?: string | null): string {
  if (!reason) return "Motivo nao informado.";
  if (/field required/i.test(reason)) {
    return "A corretora recusou a ordem. O robo vai analisar novamente.";
  }
  const translations: Record<string, string> = {
    TREND_CLEAR: "Tendencia clara",
    SIDEWAYS: "Mercado lateral",
    WAITING_NEXT_CYCLE: "Aguardando proxima analise",
    SIGNAL_REJECTED: "Nenhum sinal aprovado",
  };
  return reason
    .replace(/\b(TREND_CLEAR|SIDEWAYS|WAITING_NEXT_CYCLE|SIGNAL_REJECTED)\b/gi,
      (match) => translations[match.toUpperCase()])
    .replace(/_/g, " ")
    .trim();
}

/** Zera a memória local de rejeições exibidas (troca de usuário). */
export function clearRejectionMemory(): void {
  rejectionMemory = null;
}

function presentation(
  kind: RobotPresentationKind,
  title: string,
  detail: string | null = null,
  footer: string | null = null,
): RobotPresentation {
  return { kind, title, detail, footer, signal: null, trade: null, gale: null, direction: null, result: null };
}

function formatClock(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function countdownText(state: RobotState, now = Date.now()): string | null {
  const seconds = liveDisplayCountdownSeconds(state, now);
  const clock = seconds != null && seconds > 0 ? formatClock(seconds) : null;
  if (state.display_countdown_label) {
    return clock ? `${state.display_countdown_label} ${clock}` : state.display_countdown_label;
  }
  return clock;
}

function looksDisconnected(state: RobotState): boolean {
  if (state.connection_status_source === "cached_grace") return false;
  return (
    state.connected === false ||
    state.disconnected ||
    state.status === "ACCOUNT_DISCONNECTED" ||
    state.status === "DISCONNECTED"
  );
}

function isIdle(state: RobotState): boolean {
  return state.enabled === false && state.worker_running === false;
}

function rejectionReason(state: RobotState): string {
  return state.last_order_error ?? state.rejection_reason ?? "Ordem recusada pela corretora.";
}

function cycleResultLabel(state: RobotState): "WIN" | "LOSS" | "DRAW" | null {
  if (state.cycle_result === "WIN" || state.cycle_result === "GALE_WIN") return "WIN";
  if (state.cycle_result === "LOSS" || state.cycle_result === "GALE_LOSS") return "LOSS";
  if (state.cycle_result === "DRAW") return "DRAW";
  return null;
}

function tradeResultLabel(trade: RobotTrade | null): "WIN" | "LOSS" | "DRAW" | null {
  if (trade?.result === "WIN" || trade?.result === "LOSS" || trade?.result === "DRAW") return trade.result;
  return null;
}

function isResultStatus(status: string): boolean {
  return ["WIN", "LOSS", "DRAW", "RESULT_WIN", "RESULT_LOSS", "RESULT_RECEIVED", "GALE_RESULT_RECEIVED"].includes(status);
}

function shouldShowResult(state: RobotState, now: number): boolean {
  if (state.unseen_result) {
    const tradeResult = tradeResultLabel(state.last_trade);
    if (tradeResult || state.status === "WIN" || state.status === "LOSS" || state.status === "DRAW") {
      return true;
    }
  }
  if (state.status === "WIN" || state.status === "LOSS" || state.status === "DRAW") {
    if (!state.result_display_until) return false;
    const until = Date.parse(state.result_display_until);
    return Number.isFinite(until) && now < until;
  }
  if (isResultStatus(state.status) && !state.result_display_until) return true;
  if (!state.result_display_until) return false;
  const until = Date.parse(state.result_display_until);
  return Number.isFinite(until) && now < until;
}

function galeInfo(state: RobotState): RobotGaleInfo | null {
  const active = state.gale_active ?? state.last_trade?.active;
  const direction = state.gale_direction ?? state.last_trade?.direction;
  if (!active || !direction) return null;
  return {
    active,
    direction,
    amount: state.gale_amount ?? state.last_trade?.amount ?? null,
    step: state.gale_step ?? state.last_trade?.gale_step ?? 1,
  };
}

function isTransientStatus(state: RobotState, status: string, operating: boolean): boolean {
  if (operating || state.operation_in_progress || state.result_waiting) return true;
  return [
    "BUYING", "SENDING_ORDER", "SENDING_GALE_ORDER", "SIGNAL_FOUND", "WAITING_ENTRY",
    "WAITING_ENTRY_WINDOW", "WAITING_NEXT_CANDLE_ENTRY", "WAITING_GALE_ENTRY",
    "PENDING_GALE_RESULT", "RESULT_RECEIVED", "GALE_RESULT_RECEIVED", "ANALYZING",
    "WAITING_NEXT_CYCLE",
  ].includes(status);
}

function isFallbackInProgress(state: RobotState): boolean {
  if (
    state.order_fallback_in_progress ||
    (state.order_fallback_attempt > 0 && state.order_fallback_attempt <= state.order_fallback_max_attempts)
  ) {
    return true;
  }
  const haystack = [state.status, state.last_order_error, state.rejection_reason, state.last_rejection_reason]
    .filter(Boolean)
    .join(" ")
    .toUpperCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");
  return (
    haystack.includes("FALLBACK") ||
    haystack.includes("TRYING_NEXT_ACTIVE") ||
    haystack.includes("NEXT_BEST_ACTIVE") ||
    haystack.includes("ACTIVE_UNAVAILABLE") ||
    haystack.includes("ACTIVE UNAVAILABLE") ||
    haystack.includes("ATIVO_INDISPONIVEL") ||
    haystack.includes("ATIVO INDISPONIVEL")
  );
}

function analyzingPresentation(state: RobotState, title = "El Capo está analisando o mercado", now = Date.now()): RobotPresentation {
  const countdown = countdownText(state, now);
  const seekingOnly =
    /buscando melhor oportunidade/i.test(state.display_countdown_label ?? "") ||
    /buscando melhor oportunidade/i.test(countdown ?? "");
  if (seekingOnly) {
    return presentation("analyzing", title, "Buscando melhor oportunidade");
  }
  return countdown ? presentation("analyzing", title, countdown) : presentation("analyzing", title);
}

function noOpportunityReason(state: RobotState): boolean {
  const reason = String(state.last_rejection_reason ?? state.rejection_reason ?? "").toUpperCase();
  return ["NO_OPPORTUNITY", "NO_PATTERN_FOUND", "NO_TRADE", "NO_CANDIDATES", "NO_VALID_SIGNAL"].includes(reason);
}

function waitingNextCyclePresentation(state: RobotState, now = Date.now()): RobotPresentation {
  const footer = "Buscando melhor oportunidade";
  if (noOpportunityReason(state)) {
    return presentation(
      "analyzing",
      "El Capo está analisando o mercado",
      "Identificando uma oportunidade de operação lucrativa",
      footer,
    );
  }
  return presentation(
    "analyzing",
    "El Capo está analisando o mercado",
    "Identificando uma oportunidade de operação lucrativa",
    footer,
  );
}

/**
 * Traduz o estado bruto do robô para o texto/tono exibido no overlay.
 *
 * @param state Estado normalizado do robô (ou undefined enquanto carrega).
 * @param now Timestamp atual, usado para exibição temporária de resultados.
 */
export function getRobotStatusPresentation(
  state: RobotState | null | undefined,
  now: number,
  _options: Record<string, unknown> = {},
): RobotPresentation {
  if (!state) return presentation("loading", "Consultando robo...");
  if (looksDisconnected(state)) {
    return presentation("stopped", "Conta Bullex desconectada", "Reconecte para o robo operar");
  }
  const status = state.status;
  if (status === "INSUFFICIENT_BALANCE") {
    return presentation("stopped", "Saldo insuficiente", "Você está sem saldo para iniciar. Faça um depósito na Bullex.");
  }
  const trade = state.last_trade;
  // Só o pending_signal representa entrada travada. best_candidate durante
  // ANALYZING é telemetria e NÃO deve virar "Melhor ativo encontrado".
  const signal = state.pending_signal;
  const result = cycleResultLabel(state) ?? tradeResultLabel(trade);
  const countdown = countdownText(state, now);
  const operating =
    state.operation_in_progress ||
    state.result_waiting ||
    status === "ORDER_OPEN" ||
    status === "WAITING_RESULT" ||
    status === "PENDING_RESULT" ||
    trade?.result === "PENDING_RESULT";

  if (state.last_order_error && status === "BUY_ERROR" && !operating && !state.operation_in_progress) {
    return presentation(
      "rejected",
      "Compra REAL bloqueada",
      humanizeRobotReason(state.last_order_error),
      countdown ? `Próxima entrada em ${countdown}` : null,
    );
  }
  if (status === "BUYING" || status === "SENDING_ORDER") {
    return {
      ...presentation("operation", "Executando ordem", state.operation_message ?? "Enviando ordem..."),
      trade,
      signal: trade ? null : signal,
      direction: trade?.direction ?? signal?.direction ?? null,
    };
  }
  if (operating) {
    return {
      ...presentation("operation", "Operação aberta", "Aguardando resultado", countdown ? `Resultado em ${countdown}` : null),
      trade,
      signal: trade ? null : signal,
      direction: trade?.direction ?? signal?.direction ?? null,
    };
  }
  if (result && shouldShowResult(state, now)) {
    const title = result === "DRAW" ? "EMPATE" : result;
    return {
      ...presentation("result", title, result === "DRAW" ? "Stake devolvida. Seguindo para a próxima análise." : null),
      trade,
      signal: trade ? null : signal,
      direction: trade?.direction ?? signal?.direction ?? null,
      result,
    };
  }
  if (status === "STOPPED" || isIdle(state)) return presentation("stopped", "Robo parado");
  if (status === "STOP_WIN_HIT") return presentation("stopped", "Stop Win atingido", "Robo pausado");
  if (status === "STOP_LOSS_HIT") return presentation("stopped", "Stop Loss atingido", "Robo pausado");
  if (status === "SIGNAL_REJECTED") {
    return analyzingPresentation(state, "El Capo está analisando o mercado", now);
  }
  if (
    status === "WAITING_NEXT_CYCLE" ||
    /^Analisando por/i.test(state.display_countdown_label ?? "") ||
    /^Buscando melhor oportunidade/i.test(state.display_countdown_label ?? "")
  ) {
    return waitingNextCyclePresentation(state, now);
  }

  const rejected = status === "ORDER_REJECTED" || trade?.result === "ORDER_REJECTED";
  const transient = isTransientStatus(state, status, operating);
  if (!rejected && !transient && isFallbackInProgress(state)) {
    return presentation("analyzing", "Ativo indisponivel, tentando proximo melhor ativo...");
  }
  if (rejected && !transient) {
    const reason = rejectionReason(state);
    const key = [state.rejected_at ?? "-", trade?.order_id ?? "-", trade?.sent_at ?? "-", reason].join("|");
    if (rejectionMemory?.key !== key) rejectionMemory = { key, reason, observedAt: now };
  }
  if (!transient && rejectionMemory && now - rejectionMemory.observedAt < REJECTION_DISPLAY_MS) {
    return presentation("rejected", "Entrada rejeitada", `Motivo: ${humanizeRobotReason(rejectionMemory.reason)}`);
  }
  if (rejected && !transient) return analyzingPresentation(state, "Analisando...", now);
  if (!rejected || transient) rejectionMemory = null;

  if (status === "WAITING_GALE_ENTRY" || state.gale_pending) {
    const gale = galeInfo(state);
    return {
      ...presentation("gale", "Gale preparado", null, "Entrada no início da próxima vela"),
      gale,
      direction: gale?.direction ?? null,
    };
  }
  if (status === "SENDING_GALE_ORDER" || state.gale_in_progress) {
    const gale = galeInfo(state);
    return { ...presentation("operation", "Gale em andamento"), gale, direction: gale?.direction ?? null };
  }
  if (status === "PENDING_GALE_RESULT") {
    const gale = galeInfo(state);
    return {
      ...presentation("operation", "Aguardando resultado do Gale 1"),
      gale,
      direction: gale?.direction ?? null,
    };
  }
  if (status === "SIGNAL_FOUND" || status === "WAITING_ENTRY_WINDOW") {
    return {
      ...presentation("entry", "Melhor ativo encontrado", null, countdown ? `Entrada em ${countdown}` : null),
      signal,
      direction: signal?.direction ?? null,
    };
  }
  if (status === "WAITING_ENTRY" || status === "WAITING_NEXT_CANDLE_ENTRY") {
    return {
      ...presentation(
        "entry",
        "Melhor ativo encontrado",
        null,
        countdown ? `Entrada em ${countdown}` : "Aguardando abertura da vela...",
      ),
      signal,
      direction: signal?.direction ?? null,
    };
  }
  if (status === "SIGNAL_EXPIRED") {
    return presentation("analyzing", "Entrada perdida por atraso. Aguardando novo sinal.");
  }
  if (status === "ANALYZING" || status === "WAITING_ANALYSIS_WINDOW") {
    return analyzingPresentation(state, "El Capo está analisando o mercado", now);
  }
  if (trade && result) {
    return analyzingPresentation(state, "El Capo está analisando o mercado", now);
  }
  if (status === "ERROR") {
    return presentation(
      "analyzing",
      "Erro no robo",
      humanizeRobotReason(state.rejection_reason ?? "Nao foi possivel concluir o ciclo."),
    );
  }
  return analyzingPresentation(state, "Robo ativo", now);
}
