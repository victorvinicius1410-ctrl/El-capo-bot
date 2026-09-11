/** Intervalos de data no estilo do Gerenciador de Anúncios (Meta Ads). */

import {
  BRASILIA_TIMEZONE,
  brasiliaDateParts,
  brasiliaWeekday,
  formatBrasiliaDate,
  fromBrasiliaDate,
} from "./brasiliaTime.ts";

export type DateRangePresetId =
  | "today"
  | "yesterday"
  | "last_7"
  | "last_14"
  | "last_28"
  | "last_30"
  | "this_week"
  | "last_week"
  | "this_month"
  | "last_month"
  | "this_quarter"
  | "maximum"
  | "custom";

export interface DateRangeValue {
  start: Date;
  end: Date;
  presetId: DateRangePresetId;
}

export interface DateRangePreset {
  id: DateRangePresetId;
  label: string;
}

export const DATE_RANGE_PRESETS: DateRangePreset[] = [
  { id: "today", label: "Hoje" },
  { id: "yesterday", label: "Ontem" },
  { id: "last_7", label: "Últimos 7 dias" },
  { id: "last_14", label: "Últimos 14 dias" },
  { id: "last_28", label: "Últimos 28 dias" },
  { id: "last_30", label: "Últimos 30 dias" },
  { id: "this_week", label: "Esta semana" },
  { id: "last_week", label: "Semana passada" },
  { id: "this_month", label: "Este mês" },
  { id: "last_month", label: "Mês passado" },
  { id: "this_quarter", label: "Este trimestre" },
  { id: "maximum", label: "Máximo" },
  { id: "custom", label: "Personalizado" },
];

const WEEKDAY_LABELS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"] as const;

const DATE_FORMAT: Intl.DateTimeFormatOptions = {
  day: "numeric",
  month: "short",
  year: "numeric",
};

/** Início do dia civil em Brasília (00:00:00). */
export function startOfDay(value: Date): Date {
  const { year, month, day } = brasiliaDateParts(value);
  return fromBrasiliaDate(year, month, day);
}

/** Fim do dia civil em Brasília (23:59:59.999). */
export function endOfDay(value: Date): Date {
  const { year, month, day } = brasiliaDateParts(value);
  return fromBrasiliaDate(year, month, day, 23, 59, 59, 999);
}

/** Soma dias civis de Brasília. */
export function addDays(value: Date, amount: number): Date {
  const { year, month, day } = brasiliaDateParts(value);
  const utcNoon = Date.UTC(year, month, day + amount, 15, 0, 0);
  return startOfDay(new Date(utcNoon));
}

/** Segunda-feira da semana (ISO) no fuso de Brasília. */
export function startOfWeekMonday(value: Date): Date {
  const day = startOfDay(value);
  const weekday = brasiliaWeekday(day);
  const offset = weekday === 0 ? -6 : 1 - weekday;
  return addDays(day, offset);
}

/** Primeiro dia do mês civil em Brasília. */
export function startOfMonth(value: Date): Date {
  const { year, month } = brasiliaDateParts(value);
  return fromBrasiliaDate(year, month, 1);
}

/** Último dia do mês civil em Brasília. */
export function endOfMonth(value: Date): Date {
  const { year, month } = brasiliaDateParts(value);
  return addDays(fromBrasiliaDate(year, month + 1, 1), -1);
}

/** Primeiro dia do trimestre civil em Brasília. */
export function startOfQuarter(value: Date): Date {
  const { year, month } = brasiliaDateParts(value);
  const quarterStartMonth = Math.floor(month / 3) * 3;
  return fromBrasiliaDate(year, quarterStartMonth, 1);
}

/** Dias inclusivos entre início e fim (calendário de Brasília). */
export function inclusiveDayCount(start: Date, end: Date): number {
  const from = startOfDay(start).getTime();
  const to = startOfDay(end).getTime();
  return Math.max(1, Math.round((to - from) / 86_400_000) + 1);
}

/**
 * Dias que a API trailing precisa buscar (do início do intervalo até hoje em Brasília).
 */
export function trailingDaysUntilToday(start: Date, today: Date, maxDays: number): number {
  const days = inclusiveDayCount(start, startOfDay(today));
  return Math.min(maxDays, Math.max(1, days));
}

/** Intervalo trailing de N dias terminando em `today` (Brasília). */
export function rangeFromDays(
  days: number,
  today: Date,
  presetId: DateRangePresetId = "custom",
): DateRangeValue {
  const end = startOfDay(today);
  const start = addDays(end, -(Math.max(1, days) - 1));
  return { start, end, presetId };
}

/** Monta o intervalo de um preset Meta em dias civis de Brasília. */
export function rangeFromPreset(
  presetId: DateRangePresetId,
  today: Date,
  maxDays: number,
): DateRangeValue {
  const now = startOfDay(today);
  const clampStart = (start: Date, end: Date): DateRangeValue => {
    const earliest = addDays(now, -(maxDays - 1));
    const clipped = start < earliest ? earliest : start;
    const safeEnd = end > now ? now : end;
    return { start: clipped, end: safeEnd < clipped ? clipped : safeEnd, presetId };
  };

  switch (presetId) {
    case "today":
      return { start: now, end: now, presetId };
    case "yesterday":
      return clampStart(addDays(now, -1), addDays(now, -1));
    case "last_7":
      return clampStart(addDays(now, -6), now);
    case "last_14":
      return clampStart(addDays(now, -13), now);
    case "last_28":
      return clampStart(addDays(now, -27), now);
    case "last_30":
      return clampStart(addDays(now, -29), now);
    case "this_week":
      return clampStart(startOfWeekMonday(now), now);
    case "last_week": {
      const thisMonday = startOfWeekMonday(now);
      const lastMonday = addDays(thisMonday, -7);
      return clampStart(lastMonday, addDays(lastMonday, 6));
    }
    case "this_month":
      return clampStart(startOfMonth(now), now);
    case "last_month": {
      const firstThisMonth = startOfMonth(now);
      const lastMonthEnd = addDays(firstThisMonth, -1);
      return clampStart(startOfMonth(lastMonthEnd), lastMonthEnd);
    }
    case "this_quarter":
      return clampStart(startOfQuarter(now), now);
    case "maximum":
      return clampStart(addDays(now, -(maxDays - 1)), now);
    case "custom":
      return clampStart(addDays(now, -29), now);
  }
}

function sameDay(left: Date, right: Date): boolean {
  const a = brasiliaDateParts(left);
  const b = brasiliaDateParts(right);
  return a.year === b.year && a.month === b.month && a.day === b.day;
}

/** Identifica o preset que corresponde ao intervalo, ou `custom`. */
export function matchPreset(
  range: Pick<DateRangeValue, "start" | "end">,
  today: Date,
  maxDays: number,
): DateRangePresetId {
  for (const preset of DATE_RANGE_PRESETS) {
    if (preset.id === "custom") continue;
    const candidate = rangeFromPreset(preset.id, today, maxDays);
    if (sameDay(candidate.start, range.start) && sameDay(candidate.end, range.end)) {
      return preset.id;
    }
  }
  return "custom";
}

/** Rótulo curto de uma data (pt-BR, Brasília). */
export function formatChipDate(value: Date): string {
  return formatBrasiliaDate(value, DATE_FORMAT);
}

/** Texto do botão: `15 de jul. de 2026 – 14 de ago. de 2026`. */
export function formatRangeLabel(start: Date, end: Date): string {
  return `${formatChipDate(start)} – ${formatChipDate(end)}`;
}

export function weekdayLabels(): readonly string[] {
  return WEEKDAY_LABELS;
}

export interface CalendarCell {
  date: Date;
  inMonth: boolean;
}

/** Grade de 6 semanas começando na segunda, em Brasília. */
export function monthCells(year: number, month: number): CalendarCell[] {
  const first = fromBrasiliaDate(year, month, 1);
  const gridStart = startOfWeekMonday(first);
  const cells: CalendarCell[] = [];
  for (let index = 0; index < 42; index += 1) {
    const date = addDays(gridStart, index);
    cells.push({ date, inMonth: brasiliaDateParts(date).month === month });
  }
  return cells;
}

export function isDateInRange(value: Date, start: Date, end: Date): boolean {
  const time = startOfDay(value).getTime();
  return time >= startOfDay(start).getTime() && time <= startOfDay(end).getTime();
}

/** Filtra operações cujo timestamp cai no intervalo civil de Brasília. */
export function isStampInRange(
  stamp: string | null | undefined,
  start: Date,
  end: Date,
): boolean {
  if (!stamp) return false;
  const parsed = new Date(stamp);
  if (Number.isNaN(parsed.getTime())) return false;
  return parsed.getTime() >= startOfDay(start).getTime() && parsed.getTime() <= endOfDay(end).getTime();
}

export { BRASILIA_TIMEZONE };
