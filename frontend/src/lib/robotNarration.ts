import { cleanAnalysisForSpeech } from "./analysisSpeech";
import { formatMoneyForSpeech } from "./bullexConnection";
import { humanizeRobotReason } from "./robotPresentation";
import type { RobotResultVoice, RobotSignal, RobotState, RobotTrade } from "./robotState";
import { isStudyActive, studyWinSpeech } from "./studyMode";
export { SPEECH_CHUNK_MAX_CHARS, splitSpeechChunks } from "./speechChunks";

/**
 * Etapa do gale para a fala. Com "Quantidade de Gales" acima de 1 o ciclo
 * pode abrir G1, G2, G3..., e a narração dizia "Gale 1" em todas elas.
 */
function galeStep(state: RobotState): number {
  const step = state.gale_step ?? state.last_trade?.gale_step ?? 1;
  return step >= 1 ? step : 1;
}

const galeWinSpeech = (step: number): string =>
  `Gale ${step} fechou no win. Recuperação concluída.`;
const galeLossSpeech = (step: number): string =>
  `Gale ${step} fechou no los. Ciclo finalizado. Mantém o gerenciamento.`;

export const ROBOT_START_VOICEOVER_SRC = "/robot-voiceover.mp3";

/** Frases faladas ao iniciar a operação (análise contínua, sem pedir espera longa). */
export const ROBOT_START_NARRATION_LINES = [
  "El Capo está analisando o mercado.",
  "Identificando uma oportunidade de operação lucrativa.",
] as const;

export const ROBOT_START_NARRATION_TEXT = ROBOT_START_NARRATION_LINES.join(" ");

export interface RobotNarrationEvent {
  key: string;
  text: string;
  audioSrc?: string;
}

export interface RobotNarrationOptions {
  includeOpeningVoiceover?: boolean;
  suppressAnalysisUntilSequence?: number;
}

export interface AiSignalPresentation {
  statusLabel: string;
  approved: boolean | null;
  confidenceLabel: string | null;
  riskLabel: string | null;
  candleReadingLabel: string | null;
  entryReasonLabel: string | null;
  blockMessage: string | null;
  fallbackMessage: string | null;
  voiceText: string | null;
}

/** Resumo da avaliação de IA associada a um sinal (quando existir). */
export function getAiSignalPresentation(signal?: RobotSignal | null): AiSignalPresentation | null {
  if (!signal) return null;
  const blockMessage = signal.ai_block_reason
    ? `IA bloqueou entrada: ${humanizeRobotReason(signal.ai_block_reason)}`
    : null;
  const fallbackMessage = signal.ai_error ? "IA indisponivel, usando analise tecnica local." : null;
  const approved = signal.ai_approved != null ? signal.ai_approved : blockMessage ? false : null;
  return {
    statusLabel: aiStatusLabel(approved, fallbackMessage),
    approved,
    confidenceLabel: signal.ai_confidence != null ? `${Math.round(signal.ai_confidence)}%` : null,
    riskLabel: humanizeOptional(signal.ai_risk),
    candleReadingLabel: humanizeOptional(signal.ai_candle_reading),
    entryReasonLabel: humanizeOptional(signal.ai_entry_reason ?? signal.reason ?? signal.strategy_reason),
    blockMessage,
    fallbackMessage,
    voiceText: signal.ai_voice_text?.trim() ? signal.ai_voice_text.trim() : null,
  };
}

function aiStatusLabel(approved: boolean | null, fallbackMessage: string | null): string {
  if (approved === true) return "Aprovado";
  if (approved === false) return "Reprovado";
  return fallbackMessage ? "Indisponivel" : "Aguardando";
}

function humanizeOptional(value?: string | null): string | null {
  return value ? humanizeRobotReason(value) : null;
}

/**
 * Gera a fila de eventos de narração para o estado atual do robô.
 *
 * @param state Estado normalizado do robô.
 * @param secondsUntilNextCycle Segundos até a próxima análise (para avisos longos).
 * @param analysisSequence Contador de ciclos de análise já observados.
 * @param startSequence Quantas vezes o robô foi ligado nesta sessão.
 * @param currency Moeda usada para ler valores em voz alta.
 */
export function buildRobotNarrationEvents(
  state: RobotState,
  secondsUntilNextCycle: number | null,
  analysisSequence: number,
  startSequence: number,
  currency = "BRL",
  options: RobotNarrationOptions = {},
): RobotNarrationEvent[] {
  const includeOpeningVoiceover = options.includeOpeningVoiceover !== false;
  const suppressAnalysisUntil = options.suppressAnalysisUntilSequence ?? 1;
  const events: RobotNarrationEvent[] = [];
  const status = state.status;
  // Entrada narrada só com pending_signal travado (não best_candidate de análise).
  const signal = state.pending_signal;
  const trade = state.last_trade;
  const orderId = trade?.order_id ?? "-";
  const signalCreatedAt = signal?.created_at ?? "-";
  const finishedResult = finishedTradeResult(state, trade);
  const stopKind = stopReasonKind(state);
  const ai = getAiSignalPresentation(signal);
  const speakMoney = (value?: number | null) => formatMoneyForSpeech(value, currency);
  const isGaleCycle = state.cycle_result === "GALE_WIN" || state.cycle_result === "GALE_LOSS";
  const operating =
    state.operation_in_progress ||
    state.result_waiting ||
    ["ORDER_OPEN", "WAITING_RESULT", "BUYING", "SENDING_ORDER", "PENDING_RESULT",
      "WAITING_GALE_ENTRY", "SENDING_GALE_ORDER", "PENDING_GALE_RESULT"].includes(status) ||
    trade?.result === "PENDING_RESULT";

  if (stopKind) {
    events.push({
      key: eventKey(stopKind, orderId, signalCreatedAt, signal,
        `${state.profit}:${state.wins}:${state.losses}:${state.stop_reason ?? ""}`),
      text:
        stopKind === "STOP_WIN"
          ? `Meta de lucro atingida. Resultado atual de ${speakMoney(state.profit)}. Respeite o seu gerenciamento e siga com disciplina. Vamos em frente com o robo do Sergio Trader.`
          : `Limite de perda atingido. Resultado atual de ${speakMoney(state.profit)}. Respeite o seu gerenciamento para proteger o capital. O robo foi pausado com seguranca.`,
    });
    return events;
  }

  if (!state.enabled || status === "STOPPED") {
    events.push({
      key: "WELCOME_STOPPED",
      text: "Bem-vindo ao robô do Sérgio Trader. Para iniciar, faça login na Bullex. Depois, ligue o robô. Não esqueça de configurar o valor da entrada, o stop win, e o stop loss.",
    });
    return events;
  }

  // Modo Estudo: silêncio em análise, entrada, gale, rejeição e loss. Só o
  // win é falado, com a estratégia. Stop e boas-vindas (acima) continuam.
  if (isStudyActive(state)) return studyNarrationEvents(state);

  if (includeOpeningVoiceover && startSequence > 0) {
    events.push({
      key: `ROBOT_STARTED_VOICEOVER|${startSequence}`,
      text: ROBOT_START_NARRATION_TEXT,
    });
  }
  // A fala não nomeia a estratégia: era sempre a mesma lista do motor. Leva
  // só os detalhes da análise (ver `analysisSpeech.ts`).
  // 11/09/2026: nada de "melhor ativo encontrado" nem "entrada preparada". A
  // conferência de S/R e pavio acontece na virada da vela e cancelava 65% do
  // que era anunciado. A fala da entrada foi para o momento em que a ordem
  // sai, logo abaixo (`PENDING_RESULT`), e continua levando a análise.
  if (status === "ANALYZING" && !operating && analysisSequence > suppressAnalysisUntil) {
    events.push({
      key: `ANALYSIS_STARTED|${analysisSequence}|el-capo`,
      text: "El Capo está analisando o mercado.",
    });
    events.push({
      key: `ANALYSIS_STARTED|${analysisSequence}|seeking`,
      text: "Identificando uma oportunidade de operação lucrativa.",
    });
  }
  if (
    status === "WAITING_NEXT_CYCLE" &&
    !operating &&
    !signal &&
    analysisSequence > suppressAnalysisUntil &&
    !hasNoOpportunity(state)
  ) {
    events.push({
      key: `ANALYSIS_MONITORING|${state.cycle_id ?? analysisSequence}|el-capo`,
      text: "El Capo está analisando o mercado.",
    });
    events.push({
      key: `ANALYSIS_MONITORING|${state.cycle_id ?? analysisSequence}|seeking`,
      text: "Identificando uma oportunidade de operação lucrativa.",
    });
  }
  if (hasNoOpportunity(state) && !operating) {
    events.push({
      key: eventKey("NO_OPPORTUNITY", orderId, signalCreatedAt, signal,
        `${state.cycle_id ?? "-"}|${state.next_cycle_at ?? "-"}`),
      text: "El Capo está analisando o mercado. Identificando uma oportunidade de operação lucrativa.",
    });
  }
  if (status === "BUYING" || status === "SENDING_ORDER") {
    events.push({
      key: statusKey(status, signal, state.cycle_id, `${trade?.order_id ?? "-"}|${signalCreatedAt}`),
      text: "Janela aberta. Enviando a ordem agora.",
    });
  }
  if (status === "WAITING_GALE_ENTRY") {
    const step = galeStep(state);
    events.push({
      key: galeKey(state, orderId, "WAITING_GALE_ENTRY"),
      text:
        step <= 1
          ? "Deu los na entrada inicial. Gale 1 preparado no mesmo ativo e na mesma direção."
          : `Deu los no Gale ${step - 1}. Gale ${step} preparado no mesmo ativo e na mesma direção.`,
    });
  }
  if (status === "SENDING_GALE_ORDER") {
    events.push({
      key: galeKey(state, orderId, "SENDING_GALE_ORDER"),
      text: `Entrando no Gale ${galeStep(state)} agora.`,
    });
  }
  if (status === "PENDING_GALE_RESULT") {
    events.push({
      key: galeKey(state, orderId, "PENDING_GALE_RESULT"),
      text: `Agora é só aguardar o resultado do Gale ${galeStep(state)}.`,
    });
  }
  if (
    !finishedResult &&
    (status === "ORDER_OPEN" || status === "WAITING_RESULT" || status === "PENDING_RESULT" ||
      state.operation_in_progress || state.result_waiting)
  ) {
    const active = trade?.active;
    if (active) {
      const directionPart = trade?.direction ? ` Direção: ${speakDirection(trade.direction)}.` : "";
      // A análise entra AQUI desde 11/09: é o primeiro momento em que existe
      // entrada de verdade, e é onde o cliente ouve por que ela foi feita.
      // A voz da IA, quando existe, é a explicação preferida — era ela que
      // falava na espera pela entrada antes de 11/09.
      const analise = ai?.voiceText
        ? ` ${sanitizeForSpeech(ai.voiceText)}`
        : signal
          ? analysisSentence(signal)
          : "";
      events.push({
        key: eventKey("PENDING_RESULT", orderId, signalCreatedAt, signal),
        text: `Operação aberta em ${speakSymbol(active)}.${directionPart}${analise} Aguardando resultado.`,
      });
    }
  }
  if (status === "ORDER_REJECTED" || trade?.result === "ORDER_REJECTED") {
    const reason = state.last_order_error ?? state.rejection_reason ?? "Ordem recusada pela corretora";
    events.push({
      key: eventKey("ORDER_REJECTED", orderId, signalCreatedAt, signal, reason),
      text: `Entrada rejeitada. Motivo: ${reasonForSpeech(reason)}.`,
    });
  }
  // Canal dedicado do backend tem prioridade: chega mesmo depois de o ciclo
  // seguir para a próxima análise. Só cai no legado quando ele não vem.
  const resultEvents: RobotNarrationEvent[] = [];
  const voiceEvent = resultVoiceEvent(state.result_voice, currency);
  if (voiceEvent) {
    resultEvents.push(voiceEvent);
  } else {
    const unseen = Boolean(state.unseen_result);
    const narrateResult = !isGaleCycle && shouldNarrateResult(status, finishedResult, unseen);
    const narrateGale = shouldNarrateGaleResult(status, state.cycle_result, unseen);
    const legacyKey = (result: string) =>
      resultEventKey(result, trade?.order_id ?? state.cycle_id, state.wins, state.losses);
    if (narrateResult && finishedResult === "WIN") {
      resultEvents.push({
        key: legacyKey("WIN"),
        text: `Fechou no win. Operação com lucro. Placar: ${speakScore(state.wins, state.losses)}. Lucro atual: ${speakMoney(state.profit)}.`,
      });
    }
    if (narrateResult && finishedResult === "LOSS") {
      resultEvents.push({
        key: legacyKey("LOSS"),
        text: `Fechou no los. Operação com perda. Placar: ${speakScore(state.wins, state.losses)}. Resultado atual: ${speakMoney(state.profit)}. Seguimos no gerenciamento, com calma.`,
      });
    }
    if (narrateGale && state.cycle_result === "GALE_WIN") {
      resultEvents.push({
        key: galeKey(state, orderId, "GALE_WIN"),
        text: galeWinSpeech(galeStep(state)),
      });
    }
    if (narrateGale && state.cycle_result === "GALE_LOSS") {
      resultEvents.push({
        key: galeKey(state, orderId, "GALE_LOSS"),
        text: galeLossSpeech(galeStep(state)),
      });
    }
  }
  // Placar/resultado primeiro: se o narrador estiver ocupado, o hook preempta.
  return dedupeEvents([...resultEvents, ...events]);
}

/** Fala do win no Modo Estudo: ativo, direção, estratégia e análise. */
function studyNarrationEvents(state: RobotState): RobotNarrationEvent[] {
  const win = studyWinSpeech(state);
  if (!win) return [];
  const trade = win.trade;
  const parts: string[] = [
    win.cycle === "GALE_WIN" ? `Gale ${win.galeStep} fechou no win.` : "Fechou no win.",
  ];
  if (trade) {
    parts.push(`Ativo: ${speakSymbol(trade.active)}. Direção: ${speakDirection(trade.direction)}.`);
    if (trade.strategy_name) {
      parts.push(`Estratégia: ${reasonForSpeech(trade.strategy_name)}.`);
    }
    const analise = analysisSentence({
      strategy_summary: trade.strategy_summary,
      speech_preview: trade.speech_preview,
      strategy_reason: trade.strategy_reason,
      reason: trade.entry_reason ?? trade.analysis_detail,
    });
    if (analise) parts.push(analise.trim());
  }
  parts.push(`Placar: ${win.wins === 1 ? "1 win" : `${win.wins} wins`}.`);
  return [
    {
      key: resultEventKey(win.cycle, win.orderId, win.wins, win.losses),
      text: parts.join(" "),
    },
  ];
}

function hasNoOpportunity(state: RobotState): boolean {
  const status = String(state.status || "").toUpperCase();
  if (status !== "WAITING_NEXT_CYCLE" && status !== "WAITING_ANALYSIS_WINDOW" && status !== "SIGNAL_REJECTED") {
    return false;
  }
  const reason = String(state.last_rejection_reason ?? state.rejection_reason ?? "").toUpperCase();
  return ["NO_OPPORTUNITY", "NO_PATTERN_FOUND", "NO_TRADE", "NO_CANDIDATES", "NO_VALID_SIGNAL"].includes(reason);
}

function dedupeEvents(events: RobotNarrationEvent[]): RobotNarrationEvent[] {
  const seen = new Set<string>();
  const result: RobotNarrationEvent[] = [];
  for (const event of events) {
    if (!seen.has(event.key)) {
      seen.add(event.key);
      result.push(event);
    }
  }
  return result;
}

function eventKey(
  kind: string,
  orderId: string,
  createdAt: string,
  signal?: RobotSignal | null,
  extra = "",
): string {
  return [kind, orderId, createdAt, signal?.symbol ?? "-", signal?.direction ?? "-", extra].join("|");
}

function statusKey(status: string, signal: RobotSignal | null, cycleId?: string | null, extra = ""): string {
  return [status, signal?.symbol ?? "-", signal?.direction ?? "-", cycleId ?? "-", extra].join("|");
}



/**
 * Chave estável de um resultado já narrado.
 *
 * Usa a ordem + placar para que a mesma operação nunca seja falada duas vezes,
 * mesmo quando o payload muda de caminho (canal de voz vs. status legado).
 */
function resultEventKey(
  result: string,
  orderId: string | null | undefined,
  wins: number,
  losses: number,
): string {
  return ["RESULT", result, orderId ?? "-", `${wins ?? 0}-${losses ?? 0}`].join("|");
}

/**
 * Monta a fala do placar a partir do canal `result_voice` do backend.
 *
 * @param voice Resultado publicado pelo backend (ou `null`).
 * @param currency Moeda usada para ler os valores em voz alta.
 * @returns Evento de narração, ou `null` quando não há resultado narrável
 *   (ex.: empate, que não gera fala de placar).
 */
function resultVoiceEvent(
  voice: RobotResultVoice | null | undefined,
  currency: string,
): RobotNarrationEvent | null {
  if (!voice) return null;
  const cycle = (voice.cycle_result || voice.result || "").toUpperCase();
  const key = resultEventKey(cycle, voice.order_id, voice.wins, voice.losses);
  const money = formatMoneyForSpeech(voice.profit, currency);
  const score = speakScore(voice.wins, voice.losses);
  const voiceStep = voice.gale_step >= 1 ? voice.gale_step : 1;
  if (cycle === "GALE_WIN") return { key, text: galeWinSpeech(voiceStep) };
  if (cycle === "GALE_LOSS") return { key, text: galeLossSpeech(voiceStep) };
  if (cycle === "WIN") {
    return {
      key,
      text: `Fechou no win. Operação com lucro. Placar: ${score}. Lucro atual: ${money}.`,
    };
  }
  if (cycle === "LOSS") {
    return {
      key,
      text: `Fechou no los. Operação com perda. Placar: ${score}. Resultado atual: ${money}. Seguimos no gerenciamento, com calma.`,
    };
  }
  return null;
}

function galeKey(state: RobotState, orderId: string, kind: string): string {
  return ["RESULT", state.cycle_id ?? "-", kind, state.gale_step ?? state.last_trade?.gale_step ?? "-", orderId].join("|");
}

function finishedTradeResult(state: RobotState, trade: RobotTrade | null): "WIN" | "LOSS" | null {
  if (state.status === "WIN" || state.status === "RESULT_WIN") return "WIN";
  if (state.status === "LOSS" || state.status === "RESULT_LOSS") return "LOSS";
  if (state.cycle_result === "WIN" || state.cycle_result === "GALE_WIN") return "WIN";
  if (state.cycle_result === "LOSS" || state.cycle_result === "GALE_LOSS") return "LOSS";
  if (trade?.result === "WIN" || trade?.result === "LOSS") return trade.result;
  return null;
}

function shouldNarrateResult(status: string, result: "WIN" | "LOSS" | null, unseen = false): boolean {
  if (!result) return false;
  if (unseen) return true;
  return ["WIN", "LOSS", "RESULT_WIN", "RESULT_LOSS", "RESULT_RECEIVED", "GALE_RESULT_RECEIVED"].includes(status);
}

function shouldNarrateGaleResult(status: string, cycleResult: string | null, unseen = false): boolean {
  if (cycleResult !== "GALE_WIN" && cycleResult !== "GALE_LOSS") return false;
  if (unseen) return true;
  return ["WIN", "LOSS", "RESULT_WIN", "RESULT_LOSS", "RESULT_RECEIVED", "GALE_RESULT_RECEIVED"].includes(status);
}

/** Prioridade alta = placar/resultado; o narrador preempta fala menor. */
export function narrationEventPriority(event: Pick<RobotNarrationEvent, "key">): number {
  const key = event.key;
  if (key.startsWith("RESULT|") || key.includes("|GALE_WIN") || key.includes("|GALE_LOSS")) return 100;
  if (key.includes("STOP_WIN") || key.includes("STOP_LOSS")) return 90;
  if (key.includes("ORDER_REJECTED")) return 80;
  return 0;
}

/** Detecta se o estado indica stop win/loss (para narração e pausa). */
export function stopReasonKind(state: RobotState): "STOP_WIN" | "STOP_LOSS" | null {
  const haystack = `${state.status} ${state.stop_reason ?? ""}`.toUpperCase().replace(/[\s-]+/g, "_");
  if (haystack.includes("STOP_WIN") || haystack.includes("WIN_REACHED") || haystack.includes("TAKE_PROFIT")) {
    return "STOP_WIN";
  }
  if (haystack.includes("STOP_LOSS") || haystack.includes("LOSS_REACHED") || haystack.includes("MAX_LOSS")) {
    return "STOP_LOSS";
  }
  return null;
}

const CURRENCY_NAMES: Record<string, string> = {
  EUR: "euro",
  USD: "dólar",
  GBP: "libra",
  JPY: "iene",
  AUD: "dólar australiano",
  CAD: "dólar canadense",
  CHF: "franco suíço",
  NZD: "dólar neozelandês",
  BRL: "real",
  BTC: "bitcoin",
  ETH: "ethereum",
};

/** Converte um símbolo de ativo (ex.: EURUSD-OTC) para leitura em voz alta. */
export function speakSymbol(symbol: string): string {
  const original = symbol.trim();
  const cleaned = original
    .toUpperCase()
    .replace(/\s+/g, "")
    .replace(/[_-]OTC$/, "")
    .replace(/[^A-Z0-9/]/g, "");
  const isOtc = /(^|[_\-/\s])OTC$/i.test(original);
  const parts = cleaned.includes("/") ? cleaned.split("/").filter(Boolean) : splitCurrencyPair(cleaned);
  const spoken = parts.map((part) => CURRENCY_NAMES[part] ?? spellOut(part)).join(" ");
  return isOtc ? `${spoken}, OTC` : spoken || original;
}

function splitCurrencyPair(symbol: string): string[] {
  const known = Object.keys(CURRENCY_NAMES).sort((a, b) => b.length - a.length);
  for (const prefix of known) {
    if (!symbol.startsWith(prefix)) continue;
    const rest = symbol.slice(prefix.length);
    if (rest && CURRENCY_NAMES[rest]) return [prefix, rest];
  }
  return symbol.length === 6 ? [symbol.slice(0, 3), symbol.slice(3)] : [symbol];
}

function spellOut(text: string): string {
  return text.split("").join(" ");
}

function translateDirections(text?: string | null): string {
  if (!text) return "sem motivo informado";
  return text
    .replace(/\bCALL\b/gi, "compra")
    .replace(/\bPUT\b/gi, "venda")
    .replace(/_/g, " ")
    .trim();
}

function reasonForSpeech(reason: string): string {
  return sanitizeForSpeech(translateDirections(humanizeRobotReason(reason))).replace(/[.!?]+$/, "");
}

function speakScore(wins: number, losses: number): string {
  const winsPart = wins === 1 ? "1 win" : `${wins} wins`;
  const lossesPart = losses === 1 ? "1 los" : `${losses} los`;
  return `${winsPart} e ${lossesPart}`;
}

/**
 * Nome da marca na voz (SpeechSynthesis pt-BR lê "Capo" como "Cepo").
 * Na UI o texto continua "El Capo"; só a fala usa fonética.
 */
export const BRAND_NAME_SPEECH = "El Kápo";

/** Ajusta siglas e termos para pronúncia natural em pt-BR. */
export function sanitizeForSpeech(text: string): string {
  return text
    .replace(/\bEl Capo\b/gi, BRAND_NAME_SPEECH)
    .replace(/\bCapo\b/g, "Kápo")
    .replace(/\blosses\b/gi, "los")
    .replace(/\bLOSS\b/g, "los")
    .replace(/\bLoss\b/g, "los")
    .replace(/\bRSI\b/gi, "R S I")
    .replace(/\bEMA\b/gi, "E M A")
    .replace(/\bATR\b/gi, "A T R")
    .replace(/\bOTC\b/gi, "O T C")
    .trim();
}

function speakDirection(direction?: string | null): string {
  const normalized = String(direction || "").trim().toUpperCase();
  if (normalized === "CALL") return "CALL, compra";
  if (normalized === "PUT") return "PUT, venda";
  return normalized || "não definida";
}


/**
 * Frase " Análise: ..." da entrada, ou vazio quando nenhum texto tem medida.
 *
 * Usa o primeiro campo que ainda tenha conteúdo depois da limpeza. Antes, o
 * `speech_preview` era falado também depois do motivo — quando os dois eram a
 * mesma narrativa, a análise saía duas vezes seguidas.
 */
function analysisSentence(
  signal: Partial<
    Pick<RobotSignal, "strategy_summary" | "speech_preview" | "ai_entry_reason" | "strategy_reason" | "reason">
  >,
): string {
  const sources = [
    signal.strategy_summary,
    signal.speech_preview,
    signal.ai_entry_reason,
    signal.strategy_reason,
    signal.reason,
  ];
  for (const source of sources) {
    const cleaned = cleanAnalysisForSpeech(source);
    if (cleaned) return ` Análise: ${reasonForSpeech(cleaned)}.`;
  }
  return "";
}

/**
 * Sinais de voz masculina (Windows / Chrome / macOS Safari).
 * Inclui Eloquence da Apple (Reed, Eddy, Rocko, Grandpa) e Felipe (pt-BR).
 */
const MALE_VOICE_HINTS = [
  "antonio", "antônio", "thalysson", "daniel", "ricardo", "hector", "jorge", "bruno",
  "pedro", "cristiano", "frederico", "felipe", "luciano", "otavio", "otávio", "nicolas",
  "henrique", "rafael", "diego", "carlos", "juan", "sergio", "sérgio", "paulo", "marcos",
  "reed", "eddy", "rocko", "grandpa", "alex", "tom", "fred", "ralph", "bruce", "junior",
  "male", "masculino", "man ", " man",
];

/** Sinais de voz feminina — checados depois de exceções masculinas (ex.: Luciano). */
const FEMALE_VOICE_HINTS = [
  "luciana", "maria", "francisca", "heloisa", "helena", "lucia", "lúcia", "camila",
  "joana", "fernanda", "isabela", "paulina", "monica", "mônica", "ana ", "sofia",
  "vitória", "vitoria", "paloma", "sabina", "melissa", "zira", "flo", "sandy", "shelly",
  "grandma", "samantha", "karen", "moira", "tessa", "fiona", "veena", "kyoko", "yuna",
  "female", "feminina", "woman", "girl",
];

const BLOCKED_VOICE_HINTS = ["tradutor", "translate", "google traduzir"];

/** Pitch padrão (masculino / preferido). */
export const SPEECH_PITCH_MALE = 0.85;
/** Pitch mais grave quando só há voz feminina/desconhecida (Mac Safari stock). */
export const SPEECH_PITCH_FALLBACK = 0.68;

/**
 * Identidade completa da voz (name + voiceURI).
 * No Safari/macOS o `name` às vezes é genérico ("Portuguese (Brazil)")
 * enquanto o `voiceURI` ainda carrega "Luciana" — por isso os dois entram.
 */
export function voiceIdentity(voice: Pick<SpeechSynthesisVoice, "name" | "voiceURI">): string {
  return `${voice.name ?? ""} ${voice.voiceURI ?? ""}`.toLowerCase();
}

/**
 * Classifica o gênero da voz a partir do nome/URI.
 *
 * Args:
 *   identity: string já em minúsculas (use `voiceIdentity`).
 *
 * Returns:
 *   "male" | "female" | "unknown"
 */
export function voiceGender(identity: string): "male" | "female" | "unknown" {
  const lowered = identity.toLowerCase();
  // Luciano é masculino; não pode cair no hint "lucia"/"luciana".
  if (lowered.includes("luciano")) return "male";
  if (FEMALE_VOICE_HINTS.some((hint) => lowered.includes(hint))) return "female";
  if (MALE_VOICE_HINTS.some((hint) => lowered.includes(hint))) return "male";
  return "unknown";
}

/** Vozes bloqueadas: motores de tradução (não vozes nativas do Chrome/Safari). */
export function isBlockedNarratorVoice(voice: SpeechSynthesisVoice): boolean {
  const identity = voiceIdentity(voice);
  return BLOCKED_VOICE_HINTS.some((hint) => identity.includes(hint));
}

function isPortugueseVoice(voice: SpeechSynthesisVoice): boolean {
  return (voice.lang || "").toLowerCase().startsWith("pt");
}

function scoreVoice(voice: SpeechSynthesisVoice): number {
  if (isBlockedNarratorVoice(voice) || !isPortugueseVoice(voice)) return Number.NEGATIVE_INFINITY;
  const identity = voiceIdentity(voice);
  const lang = (voice.lang || "").toLowerCase();
  const gender = voiceGender(identity);
  // Preferência forte por masculino explícito.
  if (gender !== "male") return Number.NEGATIVE_INFINITY;
  let score = 120;
  if (lang === "pt-br") score += 40;
  else score += 10;
  if (identity.includes("natural") || identity.includes("neural") || identity.includes("premium") || identity.includes("enhanced")) {
    score += 80;
  }
  if (identity.includes("online")) score += 35;
  if (identity.includes("felipe")) score += 55;
  if (identity.includes("thalysson")) score += 50;
  if (identity.includes("antonio") || identity.includes("antônio")) score += 40;
  if (identity.includes("reed") || identity.includes("eddy") || identity.includes("rocko")) score += 25;
  if (identity.includes("microsoft")) score += 15;
  if (identity.includes("compact") || identity.includes("desktop")) score -= 15;
  return score;
}

/**
 * Fallback quando não há voz masculina instalada (Safari macOS stock = Luciana).
 * Prefere pt-BR; aceita female/unknown para não ficar em silêncio no MacBook.
 */
function scoreVoiceFallback(voice: SpeechSynthesisVoice): number {
  if (isBlockedNarratorVoice(voice) || !isPortugueseVoice(voice)) return Number.NEGATIVE_INFINITY;
  const identity = voiceIdentity(voice);
  const lang = (voice.lang || "").toLowerCase();
  const gender = voiceGender(identity);
  let score = 10;
  if (lang === "pt-br") score += 40;
  else score += 10;
  if (gender === "unknown") score += 15;
  if (gender === "female") score += 5;
  // Chrome Mac/Android: "Google português do Brasil" é a voz nativa útil.
  if (identity.includes("google") && lang.startsWith("pt")) score += 20;
  if (identity.includes("natural") || identity.includes("neural") || identity.includes("premium")) score += 25;
  if (identity.includes("luciana")) score -= 5;
  return score;
}

/**
 * Pitch adequado à voz escolhida (grave no fallback feminino).
 *
 * @param voice Voz selecionada ou `null`.
 * @returns Pitch para `SpeechSynthesisUtterance.pitch`.
 */
export function speechPitchForVoice(voice: SpeechSynthesisVoice | null): number {
  if (!voice) return SPEECH_PITCH_MALE;
  const gender = voiceGender(voiceIdentity(voice));
  return gender === "male" ? SPEECH_PITCH_MALE : SPEECH_PITCH_FALLBACK;
}

/**
 * Escolhe a melhor voz pt-BR para o narrador.
 * 1) Masculina explícita (Felipe, Antonio, Thalysson, Eloquence…).
 * 2) Fallback: qualquer pt-BR disponível (evita silêncio no MacBook/Safari).
 */
export function pickNarratorVoice(voices: SpeechSynthesisVoice[]): SpeechSynthesisVoice | null {
  const maleRanked = voices
    .map((voice) => ({ voice, score: scoreVoice(voice) }))
    .filter((entry) => Number.isFinite(entry.score) && entry.score > Number.NEGATIVE_INFINITY)
    .sort((a, b) => b.score - a.score);
  if (maleRanked[0]?.voice) return maleRanked[0].voice;

  const fallbackRanked = voices
    .map((voice) => ({ voice, score: scoreVoiceFallback(voice) }))
    .filter((entry) => Number.isFinite(entry.score) && entry.score > Number.NEGATIVE_INFINITY)
    .sort((a, b) => b.score - a.score);
  return fallbackRanked[0]?.voice ?? null;
}

/**
 * Desbloqueia o SpeechSynthesis após gesto do usuário (obrigatório no
 * Safari/iOS e em vários Chrome/macOS — sem isso o Capo fica mudo).
 */
export function unlockSpeechSynthesis(): void {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  try {
    const synth = window.speechSynthesis;
    synth.resume();
    const warm = new SpeechSynthesisUtterance(" ");
    warm.volume = 0;
    warm.rate = 2;
    warm.lang = "pt-BR";
    synth.speak(warm);
    synth.cancel();
  } catch {
    // ignore — unlock best-effort
  }
}
