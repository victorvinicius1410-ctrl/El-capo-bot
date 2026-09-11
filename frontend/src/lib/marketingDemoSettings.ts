/**
 * Configurações ocultas da conta marketing (Shift+O).
 *
 * Definem valor/payout/ativo/direção/resultado usados ao gerar operação
 * manual no painel. O robô em si usa as mesmas configs do cliente
 * (pop Iniciar Operação).
 */

import {
  brasiliaDateTimeParts,
  formatBrasiliaDateTime,
  fromBrasiliaDate,
} from "./brasiliaTime.ts";

export type MarketingDirectionMode = "AUTO" | "CALL" | "PUT";
export type MarketingResultMode = "AUTO" | "WIN" | "LOSS";

/** Ativos disponíveis no seletor do Shift+O (forex OTC permitidos no robô). */
export const MARKETING_ASSET_OPTIONS = [
  "EURUSD-OTC",
  "GBPUSD-OTC",
  "USDJPY-OTC",
  "AUDUSD-OTC",
  "EURGBP-OTC",
  "USDCHF-OTC",
  "EURJPY-OTC",
  "NZDUSD-OTC",
  "USDCAD-OTC",
  "AUDJPY-OTC",
  "GBPJPY-OTC",
] as const;

export type MarketingAssetOption = (typeof MARKETING_ASSET_OPTIONS)[number];

export interface MarketingDemoSettings {
  /** Valor da operação manual gerada no Shift+O. */
  amount: number;
  /** Payout percentual (0–100) da operação manual. */
  payout: number;
  /** Ativo selecionado no botão de seleção. */
  asset: string;
  /** Direção fixa ou AUTO para sortear CALL/PUT. */
  direction: MarketingDirectionMode;
  /** Resultado forçado ou AUTO para seguir a taxa de acertividade. */
  result: MarketingResultMode;
}

export const DEFAULT_MARKETING_DEMO_SETTINGS: MarketingDemoSettings = {
  amount: 10,
  payout: 85,
  asset: "EURUSD-OTC",
  direction: "AUTO",
  result: "AUTO",
};

const STORAGE_PREFIX = "elcapo.marketingDemoSettings.";

/**
 * Normaliza e limita os campos do painel Shift+O.
 */
export function normalizeMarketingDemoSettings(
  raw: Partial<MarketingDemoSettings> | null | undefined,
): MarketingDemoSettings {
  const amount = Number(raw?.amount);
  const payout = Number(raw?.payout);
  const direction = raw?.direction;
  const result = raw?.result;
  const asset = String(raw?.asset ?? DEFAULT_MARKETING_DEMO_SETTINGS.asset)
    .trim()
    .toUpperCase();

  return {
    amount:
      Number.isFinite(amount) && amount > 0
        ? Math.min(amount, 100_000)
        : DEFAULT_MARKETING_DEMO_SETTINGS.amount,
    payout:
      Number.isFinite(payout) && payout >= 0 && payout <= 100
        ? Math.round(payout)
        : DEFAULT_MARKETING_DEMO_SETTINGS.payout,
    asset: asset || DEFAULT_MARKETING_DEMO_SETTINGS.asset,
    direction:
      direction === "CALL" || direction === "PUT" || direction === "AUTO"
        ? direction
        : DEFAULT_MARKETING_DEMO_SETTINGS.direction,
    result:
      result === "WIN" || result === "LOSS" || result === "AUTO"
        ? result
        : DEFAULT_MARKETING_DEMO_SETTINGS.result,
  };
}

/**
 * Carrega as configurações persistidas por usuário (localStorage).
 */
export function loadMarketingDemoSettings(userId?: string | null): MarketingDemoSettings {
  if (!userId || typeof localStorage === "undefined") {
    return { ...DEFAULT_MARKETING_DEMO_SETTINGS };
  }
  try {
    const raw = localStorage.getItem(`${STORAGE_PREFIX}${userId}`);
    if (!raw) return { ...DEFAULT_MARKETING_DEMO_SETTINGS };
    return normalizeMarketingDemoSettings(JSON.parse(raw) as Partial<MarketingDemoSettings>);
  } catch {
    return { ...DEFAULT_MARKETING_DEMO_SETTINGS };
  }
}

/**
 * Persiste as configurações do Shift+O para o usuário autenticado.
 */
export function saveMarketingDemoSettings(
  userId: string,
  settings: Partial<MarketingDemoSettings>,
): MarketingDemoSettings {
  const normalized = normalizeMarketingDemoSettings({
    ...loadMarketingDemoSettings(userId),
    ...settings,
  });
  if (typeof localStorage !== "undefined") {
    localStorage.setItem(`${STORAGE_PREFIX}${userId}`, JSON.stringify(normalized));
  }
  return normalized;
}

/**
 * Resolve ativo/direção efetivos a partir das configs (AUTO = sorteio).
 */
export function resolveMarketingTradePreview(settings: MarketingDemoSettings): {
  amount: number;
  payout: number;
  asset: string;
  direction: "CALL" | "PUT";
  result?: "WIN" | "LOSS";
} {
  const normalized = normalizeMarketingDemoSettings(settings);
  const direction: "CALL" | "PUT" =
    normalized.direction === "AUTO"
      ? Math.random() < 0.5
        ? "CALL"
        : "PUT"
      : normalized.direction;
  const asset =
    normalized.asset ||
    MARKETING_ASSET_OPTIONS[Math.floor(Math.random() * MARKETING_ASSET_OPTIONS.length)] ||
    "EURUSD-OTC";
  return {
    amount: normalized.amount,
    payout: normalized.payout,
    asset,
    direction,
    result: normalized.result === "AUTO" ? undefined : normalized.result,
  };
}

export type MarketingTimingMode = "now" | "custom";

/**
 * Formata um instante para o valor de ``input[type=datetime-local]``.
 *
 * Args:
 *   date: Instante a exibir (padrão: agora).
 *
 * Returns:
 *   String ``YYYY-MM-DDTHH:mm`` no horário de Brasília.
 */
export function toLocalDateTimeInputValue(date: Date = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  const parts = brasiliaDateTimeParts(date);
  return [
    parts.year,
    "-",
    pad(parts.month + 1),
    "-",
    pad(parts.day),
    "T",
    pad(parts.hour),
    ":",
    pad(parts.minute),
  ].join("");
}

/**
 * Converte o valor de ``datetime-local`` (horário de Brasília) para ISO8601.
 *
 * Args:
 *   value: String ``YYYY-MM-DDTHH:mm`` ou ``YYYY-MM-DDTHH:mm:ss``.
 *
 * Returns:
 *   ISO8601 UTC, ou ``null`` se inválido/vazio.
 */
export function localDateTimeInputToIso(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(trimmed);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6] ?? "0");
  const date = fromBrasiliaDate(year, month - 1, day, hour, minute, second);
  if (Number.isNaN(date.getTime())) return null;
  const parts = brasiliaDateTimeParts(date);
  if (
    parts.year !== year ||
    parts.month !== month - 1 ||
    parts.day !== day ||
    parts.hour !== hour ||
    parts.minute !== minute ||
    parts.second !== second
  ) {
    return null;
  }
  return date.toISOString();
}

/**
 * Formata ``created_at`` ISO para exibição no painel (pt-BR, Brasília).
 *
 * Args:
 *   value: Timestamp ISO8601 ou indefinido.
 *
 * Returns:
 *   Data/hora de Brasília, ou string vazia se inválido.
 */
export function formatMarketingTradeDateTime(value?: string | null): string {
  if (!value) return "";
  return formatBrasiliaDateTime(value);
}
