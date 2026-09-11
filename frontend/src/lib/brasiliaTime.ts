/** Fuso civil do El Capo: meia-noite a meia-noite em Brasília (UTC−3). */

export const BRASILIA_TIMEZONE = "America/Sao_Paulo";

/** Offset fixo desde 2019 (Brasil sem horário de verão). */
const BRASILIA_OFFSET = "-03:00";

export interface BrasiliaDateParts {
  year: number;
  month: number;
  day: number;
}

export interface BrasiliaDateTimeParts extends BrasiliaDateParts {
  hour: number;
  minute: number;
  second: number;
}

function pad(value: number, width = 2): string {
  return String(value).padStart(width, "0");
}

function readPart(parts: Intl.DateTimeFormatPart[], type: string): number {
  return Number(parts.find((part) => part.type === type)?.value);
}

/**
 * Quebra um instante na data civil de Brasília.
 *
 * @param value Instante qualquer (UTC ou local).
 * @returns Ano, mês (0–11) e dia em `America/Sao_Paulo`.
 */
export function brasiliaDateParts(value: Date): BrasiliaDateParts {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: BRASILIA_TIMEZONE,
    year: "numeric",
    month: "numeric",
    day: "numeric",
  }).formatToParts(value);
  return {
    year: readPart(parts, "year"),
    month: readPart(parts, "month") - 1,
    day: readPart(parts, "day"),
  };
}

/**
 * Monta um instante a partir do relógio civil de Brasília.
 *
 * @param year Ano civil.
 * @param month Mês 0-indexado.
 * @param day Dia do mês.
 * @param hour Hora 0–23.
 * @param minute Minuto.
 * @param second Segundo.
 * @param millisecond Milissegundo.
 * @returns `Date` no instante equivalente em UTC.
 */
export function fromBrasiliaDate(
  year: number,
  month: number,
  day: number,
  hour = 0,
  minute = 0,
  second = 0,
  millisecond = 0,
): Date {
  return new Date(
    `${year}-${pad(month + 1)}-${pad(day)}T${pad(hour)}:${pad(minute)}:${pad(second)}.${pad(millisecond, 3)}${BRASILIA_OFFSET}`,
  );
}

/** Chave `YYYY-MM-DD` do dia civil em Brasília. */
export function brasiliaDateKey(value: Date): string {
  const { year, month, day } = brasiliaDateParts(value);
  return `${year}-${pad(month + 1)}-${pad(day)}`;
}

/**
 * Quebra um instante em data e hora civis de Brasília.
 *
 * @param value Instante qualquer.
 * @returns Componentes de relógio em `America/Sao_Paulo`.
 */
export function brasiliaDateTimeParts(value: Date): BrasiliaDateTimeParts {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: BRASILIA_TIMEZONE,
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "numeric",
    second: "numeric",
    hourCycle: "h23",
  }).formatToParts(value);
  return {
    year: readPart(parts, "year"),
    month: readPart(parts, "month") - 1,
    day: readPart(parts, "day"),
    hour: readPart(parts, "hour"),
    minute: readPart(parts, "minute"),
    second: readPart(parts, "second"),
  };
}

/**
 * Dia da semana em Brasília (0 = domingo … 6 = sábado).
 *
 * @param value Instante qualquer.
 * @returns Índice do weekday no fuso de Brasília.
 */
export function brasiliaWeekday(value: Date): number {
  const label = new Intl.DateTimeFormat("en-US", {
    timeZone: BRASILIA_TIMEZONE,
    weekday: "short",
  }).format(value);
  const map: Record<string, number> = {
    Sun: 0,
    Mon: 1,
    Tue: 2,
    Wed: 3,
    Thu: 4,
    Fri: 5,
    Sat: 6,
  };
  return map[label] ?? 0;
}

/**
 * Formata só a data (pt-BR) no fuso de Brasília.
 *
 * @param value Date ou ISO-8601.
 * @param options Opções extras do `Intl.DateTimeFormat`.
 * @returns Data legível, ou string vazia se inválida.
 */
export function formatBrasiliaDate(
  value: Date | string,
  options: Intl.DateTimeFormatOptions = {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  },
): string {
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("pt-BR", { timeZone: BRASILIA_TIMEZONE, ...options }).format(date);
}

/**
 * Formata data e hora (pt-BR) no fuso de Brasília.
 *
 * @param value Date ou ISO-8601.
 * @returns Data/hora legível, ou string vazia se inválida.
 */
export function formatBrasiliaDateTime(value: Date | string): string {
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("pt-BR", {
    timeZone: BRASILIA_TIMEZONE,
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
