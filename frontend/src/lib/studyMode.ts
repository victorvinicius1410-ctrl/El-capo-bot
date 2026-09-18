/**
 * Apresentação do Modo LIVE da conta marketing.
 *
 * Durante o LIVE, o painel não anuncia entrada, análise nem LOSS. Só o WIN
 * aparece, acompanhado da estratégia e da explicação técnica. O placar vindo
 * do servidor continua inteiro: ao ativar o LIVE, perdas anteriores permanecem
 * no contador; as perdas das novas ordens LIVE não são gravadas pelo backend.
 */
import type { RobotState, RobotTrade } from "./robotState.ts";

export const STUDY_SEAL_TEXT = "ESTUDO · losses ocultos";
export const STUDY_IDLE_TITLE = "Modo LIVE";
export const STUDY_IDLE_DETAIL = "Aguardando o próximo WIN";

type StudyStateLike = Pick<RobotState, "live_demo" | "study_mode"> | null | undefined;

/** Compatibilidade de nome: a apresentação silenciosa vale para todo LIVE. */
export function isStudyActive(state: StudyStateLike): boolean {
  return state?.live_demo === true;
}

/** Texto curto da estratégia do win para o overlay (ou `null`). */
export function studyWinDetail(trade: RobotTrade | null | undefined): string | null {
  if (!trade) return null;
  const name = trade.strategy_name?.trim();
  const summary = (trade.strategy_summary || trade.speech_preview || "").trim();
  if (name && summary) return `${name} · ${summary}`;
  return name || summary || null;
}

export interface StudyWinSpeech {
  /** Mesmo formato da chave de resultado do narrador normal: nunca fala duas vezes. */
  cycle: "WIN" | "GALE_WIN";
  orderId: string | null;
  wins: number;
  losses: number;
  galeStep: number;
  /** Operação que fechou — só quando é a mesma ordem do resultado. */
  trade: RobotTrade | null;
}

/**
 * Resultado que o narrador do LIVE pode falar: só win.
 *
 * Usa o canal `result_voice` do servidor; sem ele, cai no estado legado.
 * Loss, gale loss e empate devolvem `null` (silêncio).
 */
export function studyWinSpeech(state: RobotState): StudyWinSpeech | null {
  const trade = state.last_trade;
  const voice = state.result_voice;
  if (voice) {
    const cycle = (voice.cycle_result || voice.result || "").toUpperCase();
    if (cycle !== "WIN" && cycle !== "GALE_WIN") return null;
    const sameOrder = Boolean(trade && voice.order_id && trade.order_id === voice.order_id);
    return {
      cycle,
      orderId: voice.order_id,
      wins: voice.wins,
      losses: voice.losses,
      galeStep: voice.gale_step >= 1 ? voice.gale_step : 1,
      trade: sameOrder ? trade : null,
    };
  }
  const resultStatuses = ["WIN", "RESULT_WIN", "RESULT_RECEIVED", "GALE_RESULT_RECEIVED"];
  const showing = state.unseen_result || resultStatuses.includes(state.status);
  if (!showing) return null;
  const cycle =
    state.cycle_result === "GALE_WIN"
      ? "GALE_WIN"
      : state.cycle_result === "WIN" || (!state.cycle_result && trade?.result === "WIN")
        ? "WIN"
        : null;
  if (!cycle) return null;
  const step = state.gale_step ?? trade?.gale_step ?? 1;
  return {
    cycle,
    orderId: trade?.order_id ?? state.cycle_id,
    wins: state.wins,
    losses: state.losses,
    galeStep: step >= 1 ? step : 1,
    trade,
  };
}

export interface StudyHistoryItem {
  /** Resultado já normalizado pelo histórico (GALE_LOSS vira LOSS). */
  result?: string | null;
  /** Marca gravada pelo servidor em `analysis_json.study_mode`. */
  studyMode?: boolean;
}

function isLossOrDraw(item: StudyHistoryItem): boolean {
  const value = String(item.result ?? "").toUpperCase();
  return value === "LOSS" || value === "DRAW";
}

/**
 * Histórico com os losses e empates do estudo escondidos.
 *
 * Só esconde linha marcada pelo servidor (`studyMode === true`). Com o estudo
 * desligado a lista volta inteira. `hidden` alimenta o aviso obrigatório.
 */
export function filterStudyHistory<T extends StudyHistoryItem>(
  items: T[],
  active: boolean,
): { items: T[]; hidden: number } {
  if (!active) return { items, hidden: 0 };
  const visible = items.filter((item) => !(item.studyMode === true && isLossOrDraw(item)));
  return { items: visible, hidden: items.length - visible.length };
}

/** Texto do aviso do histórico; `null` quando nada foi escondido. */
export function studyHiddenNotice(hidden: number): string | null {
  if (hidden <= 0) return null;
  return hidden === 1
    ? "1 loss antigo oculto no modo LIVE"
    : `${hidden} losses antigos ocultos no modo LIVE`;
}
