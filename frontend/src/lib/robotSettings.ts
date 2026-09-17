import { formatBullExBalance, normalizeAccountCurrency } from "./bullexConnection.ts";
import { OPEN_MARKET_UNDER_MAINTENANCE } from "./openMarketMaintenance.ts";

export type RobotTimeframe = "M1" | "M5" | "M15";
export type RobotMarketMode = "OTC" | "OPEN" | "BOTH";
/** Como o Stop Win/Loss é avaliado: valor em R$ ou quantidade de WINs/LOSSes. */
export type RobotStopMode = "money" | "operations";

export interface RobotSettings {
  entryValue: number;
  stopWin: number;
  stopLoss: number;
  stopWinMode: RobotStopMode;
  stopLossMode: RobotStopMode;
  stopWinOperations: number;
  stopLossOperations: number;
  martingaleEnabled: boolean;
  martingaleSteps: number;
  martingaleMultiplier: number;
  timeframe: RobotTimeframe;
  marketMode: RobotMarketMode;
  aiAnalysisEnabled: boolean;
  aiConfirmationRequired: boolean;
  aiMinConfidence: number;
  narratorEnabled: boolean;
}

/** Mínimo operacional de stop win/loss por valor. */
export const STOP_MONEY_MIN = 5;
export const STOP_OPERATIONS_MIN = 1;
export const STOP_OPERATIONS_MAX = 500;
/** Entrada em BRL: mínimo R$ 5, sem teto. */
export const ENTRY_VALUE_MIN_BRL = 5;
/** Entrada em USD: mínimo US$ 1, sem teto. */
export const ENTRY_VALUE_MIN_USD = 1;
export const ENTRY_VALUE_MIN = ENTRY_VALUE_MIN_BRL;
/**
 * Piso absoluto do armazenamento local, válido enquanto a moeda é desconhecida.
 *
 * Não é o mínimo operacional de ninguém: é só o menor valor que alguma moeda
 * aceita (US$ 1), para o store não subir US$ 1 para 5 antes do snapshot da
 * conta chegar. O mínimo que vale de verdade é sempre o da moeda conhecida —
 * ver `entryLimitsForCurrency`.
 */
export const ENTRY_VALUE_ABSOLUTE_MIN = ENTRY_VALUE_MIN_USD;
export const ENTRY_VALUE_STEP = 0.01;
export const DEFAULT_ENTRY_VALUE = ENTRY_VALUE_MIN_BRL;

export interface EntryValueLimits {
  /** Mínimo cobrado. Com moeda desconhecida é só o piso de armazenamento. */
  min: number;
  defaultValue: number;
  /** `false` enquanto o snapshot da conta não disse a moeda. */
  currencyKnown: boolean;
}

/**
 * Mínimo de valor de entrada na moeda do saldo da corretora.
 *
 * Args:
 *   currency: Código da conta conectada (BRL ou USD). Vazio/nulo não assume BRL,
 *     para não subir US$ 1 para 5 enquanto o snapshot ainda não chegou — nesse
 *     caso vem `currencyKnown: false` e o `min` é só o piso de armazenamento,
 *     que a interface não deve anunciar como mínimo da conta.
 *
 * Returns:
 *   Mínimo e default (R$ 5 em BRL, US$ 1 em USD). Não há máximo.
 */
export function entryLimitsForCurrency(currency?: string | null): EntryValueLimits {
  const raw = String(currency ?? "").trim();
  if (!raw) {
    // Default 5 (e não 1) de propósito: quando não há valor algum, 5 é o único
    // número que serve nas duas moedas — em USD é aceito, em BRL é o mínimo.
    return {
      min: ENTRY_VALUE_ABSOLUTE_MIN,
      defaultValue: DEFAULT_ENTRY_VALUE,
      currencyKnown: false,
    };
  }
  if (normalizeAccountCurrency(raw) === "USD") {
    return {
      min: ENTRY_VALUE_MIN_USD,
      defaultValue: ENTRY_VALUE_MIN_USD,
      currencyKnown: true,
    };
  }
  return {
    min: ENTRY_VALUE_MIN_BRL,
    defaultValue: ENTRY_VALUE_MIN_BRL,
    currencyKnown: true,
  };
}

/**
 * Garante o mínimo da moeda; valores acima do mínimo são aceitos sem teto.
 */
export function clampEntryValueForCurrency(value: number, currency?: string | null): number {
  const limits = entryLimitsForCurrency(currency);
  if (!Number.isFinite(value) || value <= 0) return limits.defaultValue;
  return Math.max(limits.min, value);
}

/**
 * Texto de ajuda do campo de entrada, já na moeda da conta.
 *
 * Sem moeda conhecida não inventa número: anunciar "Mínimo R$ 1,00" (o piso de
 * armazenamento formatado no default BRL) era falso nas duas moedas e o
 * backend recusava o valor com `ENTRY_VALUE_TOO_LOW`.
 */
export function entryValueHelperText(currency?: string | null): string {
  const limits = entryLimitsForCurrency(currency);
  if (!limits.currencyKnown) return "Mínimo conforme a moeda da conta conectada";
  return `Mínimo ${formatBullExBalance(limits.min, currency)}`;
}

export const ROBOT_TIMEFRAME_OPTIONS = [
  { value: "M1" as const, label: "1 minuto", description: "Expira em 1m · monitora a cada vela" },
  { value: "M5" as const, label: "5 minutos", description: "Expira em 5m · monitora a cada vela" },
  { value: "M15" as const, label: "15 minutos", description: "Expira em 15m · monitora a cada vela" },
];

export const ROBOT_MARKET_MODE_OPTIONS = [
  { value: "OTC" as const, label: "OTC", description: "Analisa e opera os 10 pares só em OTC" },
  {
    value: "OPEN" as const,
    label: "Mercado aberto",
    description: "Analisa os mesmos 10 pares no mercado aberto (só com sessão forex aberta)",
  },
  {
    value: "BOTH" as const,
    label: "Ambos",
    description: "Com forex aberto, varre OTC e mercado aberto; com forex fechado, só OTC",
  },
];

/**
 * Sessão forex (mercado aberto): domingo 22:00 UTC → sexta 22:00 UTC.
 * Fora dessa janela a opção OPEN fica bloqueada (cadeado + contagem).
 */
export function isForexOpenMarketAvailable(now: Date = new Date()): boolean {
  const utcDay = now.getUTCDay(); // Sun=0 … Sat=6
  const minuteOfDay = now.getUTCHours() * 60 + now.getUTCMinutes();
  const fridayClose = 22 * 60;
  const sundayOpen = 22 * 60;
  if (utcDay === 6) return false; // sábado
  if (utcDay === 0) return minuteOfDay >= sundayOpen; // domingo
  if (utcDay === 5) return minuteOfDay < fridayClose; // sexta
  return true; // seg–qui
}

/**
 * Próxima abertura do mercado forex (domingo 22:00 UTC), ou `null` se já aberto.
 */
export function nextForexOpenMarketAt(now: Date = new Date()): Date | null {
  if (isForexOpenMarketAvailable(now)) return null;
  const utcDay = now.getUTCDay();
  const daysUntilSunday = utcDay === 0 ? 0 : (7 - utcDay) % 7;
  const next = new Date(
    Date.UTC(
      now.getUTCFullYear(),
      now.getUTCMonth(),
      now.getUTCDate() + daysUntilSunday,
      22,
      0,
      0,
      0,
    ),
  );
  if (next.getTime() <= now.getTime()) {
    next.setUTCDate(next.getUTCDate() + 7);
  }
  return next;
}

/** Horas restantes (arredondadas para cima) até a reabertura do mercado aberto. */
export function hoursUntilForexOpenMarket(now: Date = new Date()): number | null {
  const next = nextForexOpenMarketAt(now);
  if (!next) return null;
  const ms = next.getTime() - now.getTime();
  return Math.max(0, Math.ceil(ms / (1000 * 60 * 60)));
}

/**
 * Texto curto para o cadeado: "Abre em X horas".
 * Retorna `null` quando a sessão forex já está aberta.
 */
export function formatForexOpenCountdown(now: Date = new Date()): string | null {
  const hours = hoursUntilForexOpenMarket(now);
  if (hours == null) return null;
  if (hours <= 0) return "Abrindo em breve";
  if (hours === 1) return "Abre em 1 hora";
  if (hours < 48) return `Abre em ${hours} horas`;
  const days = Math.floor(hours / 24);
  const remHours = hours % 24;
  if (remHours === 0) {
    return days === 1 ? "Abre em 1 dia" : `Abre em ${days} dias`;
  }
  return `Abre em ${days}d ${remHours}h`;
}

/** Opções de mercado no painel (sempre as 3; OPEN pode estar bloqueada). */
export function visibleRobotMarketModeOptions(
  _now: Date = new Date(),
): typeof ROBOT_MARKET_MODE_OPTIONS {
  return ROBOT_MARKET_MODE_OPTIONS;
}

/**
 * Garante modo selecionável: OPEN vira OTC quando está travado.
 *
 * Trava por manutenção (a corretora não oferece opção fora de OTC) ou por
 * sessão forex fechada. BOTH permanece; com sessão aberta o backend varre
 * OTC + aberto.
 */
export function coerceSelectableMarketMode(
  value?: string | null,
  now: Date = new Date(),
): RobotMarketMode {
  const normalized = normalizeMarketMode(value);
  if (normalized !== "OPEN") return normalized;
  if (OPEN_MARKET_UNDER_MAINTENANCE) return "OTC";
  if (!isForexOpenMarketAvailable(now)) return "OTC";
  return normalized;
}

/** Duração da vela/ordem em minutos (cadência contínua = 1 vela). */
const CYCLE_MINUTES: Record<RobotTimeframe, number> = { M1: 1, M5: 5, M15: 15 };

export const DEFAULT_ROBOT_SETTINGS: RobotSettings = {
  entryValue: DEFAULT_ENTRY_VALUE,
  stopWin: 50,
  stopLoss: 30,
  stopWinMode: "money",
  stopLossMode: "money",
  stopWinOperations: 5,
  stopLossOperations: 3,
  martingaleEnabled: false,
  martingaleSteps: 1,
  martingaleMultiplier: 2,
  timeframe: "M1",
  marketMode: "OTC",
  aiAnalysisEnabled: false,
  aiConfirmationRequired: false,
  aiMinConfidence: 80,
  narratorEnabled: true,
};

/** Retorna a duração da vela/ordem (em minutos) para o timeframe. */
export function cycleMinutesForTimeframe(value?: string | null): number {
  return CYCLE_MINUTES[normalizeTimeframe(value)];
}

/** Converte qualquer representação de timeframe para o formato canônico. */
export function normalizeTimeframe(value?: string | null): RobotTimeframe {
  const normalized = String(value ?? "").trim().toUpperCase();
  if (normalized === "M5" || normalized === "5" || normalized === "5M") return "M5";
  if (normalized === "M15" || normalized === "15" || normalized === "15M") return "M15";
  return "M1";
}

/** Converte qualquer representação de mercado para o formato canônico. */
export function normalizeMarketMode(value?: string | null): RobotMarketMode {
  const normalized = String(value ?? "").trim().toUpperCase();
  if (["OPEN", "ABERTO", "MARKET", "MERCADO", "MERCADO_ABERTO"].includes(normalized)) return "OPEN";
  if (["BOTH", "AMBOS", "ALL", "OTC_AND_OPEN", "OPEN_AND_OTC"].includes(normalized)) return "BOTH";
  return "OTC";
}

export function timeframeLabel(value?: string | null): string {
  const normalized = normalizeTimeframe(value);
  return ROBOT_TIMEFRAME_OPTIONS.find((option) => option.value === normalized)?.label ?? normalized;
}

export function marketModeLabel(value?: string | null): string {
  const normalized = normalizeMarketMode(value);
  return ROBOT_MARKET_MODE_OPTIONS.find((option) => option.value === normalized)?.label ?? normalized;
}

/** Converte qualquer representação de modo de stop para o formato canônico. */
export function normalizeStopMode(value?: string | null): RobotStopMode {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (["operations", "ops", "count", "quantidade", "qtd", "operacoes", "operações"].includes(normalized)) {
    return "operations";
  }
  return "money";
}

export function stopModeLabel(value?: string | null): string {
  return normalizeStopMode(value) === "operations" ? "Por operações" : "Por valor";
}

type RawSettings = Partial<Record<keyof RobotSettings, unknown>> & { g1?: unknown };

/**
 * Sanitiza valores vindos do backend ou do armazenamento local.
 *
 * Args:
 *   input: Campos crus (poll, localStorage, diálogo).
 *   currency: Moeda da conta, quando já conhecida. Informe sempre que tiver —
 *     é o que faz a entrada de conta em real respeitar os R$ 5. Sem ela vale
 *     só o piso de armazenamento (`ENTRY_VALUE_ABSOLUTE_MIN`), porque o store
 *     global é compartilhado e não conhece a conta conectada.
 */
export function normalizeRobotSettings(
  input?: RawSettings | null,
  currency?: string | null,
): RobotSettings {
  const defaults = DEFAULT_ROBOT_SETTINGS;
  const entryMin = entryLimitsForCurrency(currency).min;
  return {
    entryValue: moneyAtLeast(input?.entryValue, defaults.entryValue, entryMin),
    stopWin: moneyAtLeast(input?.stopWin, defaults.stopWin, STOP_MONEY_MIN),
    stopLoss: moneyAtLeast(input?.stopLoss, defaults.stopLoss, STOP_MONEY_MIN),
    stopWinMode: normalizeStopMode(input?.stopWinMode as string | undefined),
    stopLossMode: normalizeStopMode(input?.stopLossMode as string | undefined),
    stopWinOperations: clampInt(
      input?.stopWinOperations,
      defaults.stopWinOperations,
      STOP_OPERATIONS_MIN,
      STOP_OPERATIONS_MAX,
    ),
    stopLossOperations: clampInt(
      input?.stopLossOperations,
      defaults.stopLossOperations,
      STOP_OPERATIONS_MIN,
      STOP_OPERATIONS_MAX,
    ),
    martingaleEnabled: input?.martingaleEnabled === true || input?.g1 === true,
    martingaleSteps: Math.max(1, Math.floor(positiveNumber(input?.martingaleSteps, defaults.martingaleSteps))),
    martingaleMultiplier: positiveNumber(input?.martingaleMultiplier, defaults.martingaleMultiplier),
    timeframe: normalizeTimeframe(input?.timeframe as string | undefined),
    marketMode: normalizeMarketMode(input?.marketMode as string | undefined),
    aiAnalysisEnabled:
      typeof input?.aiAnalysisEnabled === "boolean" ? input.aiAnalysisEnabled : defaults.aiAnalysisEnabled,
    aiConfirmationRequired:
      typeof input?.aiConfirmationRequired === "boolean"
        ? input.aiConfirmationRequired
        : defaults.aiConfirmationRequired,
    aiMinConfidence: positiveNumber(input?.aiMinConfidence, defaults.aiMinConfidence),
    narratorEnabled:
      typeof input?.narratorEnabled === "boolean" ? input.narratorEnabled : defaults.narratorEnabled,
  };
}

/**
 * Interpreta o texto digitado no Stop Win/Loss.
 * Retorna `null` enquanto o campo está vazio/inválido (digitação em andamento).
 * Valores abaixo de R$ 5 mantêm o fallback (mínimo operacional).
 */
export function parseStopMoneyInput(raw: string, fallback: number): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed.replace(",", "."));
  if (!Number.isFinite(parsed)) return null;
  if (parsed < STOP_MONEY_MIN) return fallback;
  return parsed;
}

/**
 * Interpreta o texto digitado na quantidade de operações do stop.
 * Retorna `null` enquanto o campo está vazio/inválido (digitação em andamento).
 */
export function parseStopOperationsInput(raw: string, fallback: number): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed.replace(",", "."));
  if (!Number.isFinite(parsed)) return null;
  const rounded = Math.floor(parsed);
  if (rounded < STOP_OPERATIONS_MIN) return fallback;
  return Math.min(STOP_OPERATIONS_MAX, rounded);
}

/**
 * Interpreta o texto digitado no valor de entrada.
 * BRL mínimo R$ 5; USD mínimo US$ 1. Sem teto.
 */
export function parseEntryValueInput(
  raw: string,
  fallback: number,
  currency?: string | null,
): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed.replace(",", "."));
  if (!Number.isFinite(parsed)) return null;
  if (parsed <= 0) return fallback;
  return clampEntryValueForCurrency(parsed, currency);
}

function positiveNumber(value: unknown, fallback: number): number {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

/** Garante valor monetário ≥ mínimo; inválido/abaixo → mínimo ou fallback. */
function moneyAtLeast(value: unknown, fallback: number, minimum: number): number {
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return Math.max(minimum, fallback);
  }
  return Math.max(minimum, parsed);
}

function clampInt(value: unknown, fallback: number, min: number, max: number): number {
  if (value == null || value === "") return fallback;
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, Math.floor(parsed)));
}

// Armazenamento em memória das configurações do usuário ativo, compartilhado
// entre overlay, painel do robô e sincronização vinda do backend.
let activeUserId: string | null = null;
let activeSettings: RobotSettings = DEFAULT_ROBOT_SETTINGS;
let lastSyncedSnapshot: (RobotSettings & { userId: string | null }) | null = null;
/** Snapshot da última hidratação vinda do backend (poll /robot/state). */
let lastHydratedSnapshot: (RobotSettings & { userId: string | null }) | null = null;
/** True após edição local até salvar/sincronizar com o backend. */
let localEditPending = false;
const settingsListeners = new Set<() => void>();

function notifySettingsListeners(): void {
  settingsListeners.forEach((listener) => listener());
}

/** Lê o snapshot atual das configurações para o usuário informado. */
export function getRobotSettingsSnapshot(userId?: string | null): RobotSettings {
  return userId && activeUserId === userId ? activeSettings : DEFAULT_ROBOT_SETTINGS;
}

/** Assina alterações no snapshot compartilhado de configurações. */
export function subscribeRobotSettings(listener: () => void): () => void {
  settingsListeners.add(listener);
  return () => {
    settingsListeners.delete(listener);
  };
}

/** Substitui as configurações locais do usuário (sem sincronizar backend). */
export function setRobotSettingsForUser(userId: string, settings: RawSettings): void {
  activeUserId = userId;
  activeSettings = normalizeRobotSettings(settings);
  localEditPending = true;
  notifySettingsListeners();
}

/** Registra as configurações confirmadas pelo backend para o usuário. */
export function markRobotSettingsSynced(userId: string, settings: RawSettings): void {
  activeUserId = userId;
  activeSettings = normalizeRobotSettings(settings);
  lastSyncedSnapshot = { userId, ...activeSettings };
  lastHydratedSnapshot = { userId, ...activeSettings };
  localEditPending = false;
  notifySettingsListeners();
}

/** Compara os campos operacionais que o usuário configura no painel/diálogo. */
function operationalSettingsEqual(a: RobotSettings, b: RobotSettings): boolean {
  return (
    a.entryValue === b.entryValue &&
    a.stopWin === b.stopWin &&
    a.stopLoss === b.stopLoss &&
    a.stopWinMode === b.stopWinMode &&
    a.stopLossMode === b.stopLossMode &&
    a.stopWinOperations === b.stopWinOperations &&
    a.stopLossOperations === b.stopLossOperations &&
    a.martingaleEnabled === b.martingaleEnabled &&
    a.martingaleSteps === b.martingaleSteps &&
    a.martingaleMultiplier === b.martingaleMultiplier &&
    a.timeframe === b.timeframe &&
    a.marketMode === b.marketMode &&
    a.aiAnalysisEnabled === b.aiAnalysisEnabled &&
    a.aiConfirmationRequired === b.aiConfirmationRequired &&
    a.aiMinConfidence === b.aiMinConfidence
  );
}

/**
 * Extrai só os campos presentes no payload do backend.
 * Campos `null`/`undefined`/vazios são ignorados para não resetar a UI para
 * defaults (M1/OTC) quando o poll devolve estado incompleto.
 */
function pickPresentRobotSettings(
  input?: RawSettings | null,
  currency?: string | null,
): Partial<RobotSettings> {
  if (!input) return {};
  const present: Partial<RobotSettings> = {};
  if (input.entryValue != null && input.entryValue !== "") {
    present.entryValue = moneyAtLeast(
      input.entryValue,
      DEFAULT_ENTRY_VALUE,
      entryLimitsForCurrency(currency).min,
    );
  }
  if (input.stopWin != null && input.stopWin !== "") {
    present.stopWin = moneyAtLeast(input.stopWin, DEFAULT_ROBOT_SETTINGS.stopWin, STOP_MONEY_MIN);
  }
  if (input.stopLoss != null && input.stopLoss !== "") {
    present.stopLoss = moneyAtLeast(input.stopLoss, DEFAULT_ROBOT_SETTINGS.stopLoss, STOP_MONEY_MIN);
  }
  if (input.stopWinMode != null && String(input.stopWinMode).trim() !== "") {
    present.stopWinMode = normalizeStopMode(input.stopWinMode as string);
  }
  if (input.stopLossMode != null && String(input.stopLossMode).trim() !== "") {
    present.stopLossMode = normalizeStopMode(input.stopLossMode as string);
  }
  if (input.stopWinOperations != null && input.stopWinOperations !== "") {
    present.stopWinOperations = clampInt(
      input.stopWinOperations,
      DEFAULT_ROBOT_SETTINGS.stopWinOperations,
      STOP_OPERATIONS_MIN,
      STOP_OPERATIONS_MAX,
    );
  }
  if (input.stopLossOperations != null && input.stopLossOperations !== "") {
    present.stopLossOperations = clampInt(
      input.stopLossOperations,
      DEFAULT_ROBOT_SETTINGS.stopLossOperations,
      STOP_OPERATIONS_MIN,
      STOP_OPERATIONS_MAX,
    );
  }
  if (typeof input.martingaleEnabled === "boolean" || input.g1 === true) {
    present.martingaleEnabled = input.martingaleEnabled === true || input.g1 === true;
  }
  if (input.martingaleSteps != null && input.martingaleSteps !== "") {
    present.martingaleSteps = Math.max(1, Math.floor(positiveNumber(input.martingaleSteps, 1)));
  }
  if (input.martingaleMultiplier != null && input.martingaleMultiplier !== "") {
    present.martingaleMultiplier = positiveNumber(
      input.martingaleMultiplier,
      DEFAULT_ROBOT_SETTINGS.martingaleMultiplier,
    );
  }
  if (input.timeframe != null && String(input.timeframe).trim() !== "") {
    present.timeframe = normalizeTimeframe(input.timeframe as string);
  }
  if (input.marketMode != null && String(input.marketMode).trim() !== "") {
    present.marketMode = normalizeMarketMode(input.marketMode as string);
  }
  if (typeof input.aiAnalysisEnabled === "boolean") {
    present.aiAnalysisEnabled = input.aiAnalysisEnabled;
  }
  if (typeof input.aiConfirmationRequired === "boolean") {
    present.aiConfirmationRequired = input.aiConfirmationRequired;
  }
  if (input.aiMinConfidence != null && input.aiMinConfidence !== "") {
    present.aiMinConfidence = positiveNumber(input.aiMinConfidence, DEFAULT_ROBOT_SETTINGS.aiMinConfidence);
  }
  if (typeof input.narratorEnabled === "boolean") {
    present.narratorEnabled = input.narratorEnabled;
  }
  return present;
}

function hasUnsavedRobotSettingsEdits(userId: string): boolean {
  if (activeUserId !== userId) return false;
  if (localEditPending) return true;
  const baseline =
    lastSyncedSnapshot?.userId === userId
      ? lastSyncedSnapshot
      : lastHydratedSnapshot?.userId === userId
        ? lastHydratedSnapshot
        : null;
  if (!baseline) return false;
  return !operationalSettingsEqual(activeSettings, baseline);
}

/**
 * Hidrata as configurações locais a partir do estado do robô retornado pelo
 * backend, preservando edições ainda não salvas e ignorando campos vazios do poll.
 */
export function rememberRobotSettingsFromState(userId: string, settings: RawSettings): void {
  const present = pickPresentRobotSettings(settings);
  if (Object.keys(present).length === 0) return;

  // Edições locais (painel/diálogo) não devem ser sobrescritas pelo poll.
  if (hasUnsavedRobotSettingsEdits(userId)) return;

  const base = activeUserId === userId ? activeSettings : DEFAULT_ROBOT_SETTINGS;
  const merged = normalizeRobotSettings({
    ...base,
    ...present,
    narratorEnabled:
      typeof present.narratorEnabled === "boolean" ? present.narratorEnabled : activeSettings.narratorEnabled,
  });
  if (
    activeUserId === userId &&
    operationalSettingsEqual(activeSettings, merged) &&
    activeSettings.narratorEnabled === merged.narratorEnabled
  ) {
    lastHydratedSnapshot = { userId, ...merged };
    return;
  }
  activeUserId = userId;
  activeSettings = merged;
  lastHydratedSnapshot = { userId, ...merged };
  notifySettingsListeners();
}

/** Limpa as configurações locais do usuário informado (logout/troca de conta). */
export function resetRobotSettingsForUser(userId?: string | null): void {
  if (userId != null && activeUserId !== userId) return;
  resetRobotSettingsState();
}

/** Restaura o estado global de configurações para os padrões. */
export function resetRobotSettingsState(): void {
  activeUserId = null;
  activeSettings = DEFAULT_ROBOT_SETTINGS;
  lastSyncedSnapshot = null;
  lastHydratedSnapshot = null;
  localEditPending = false;
  notifySettingsListeners();
}
