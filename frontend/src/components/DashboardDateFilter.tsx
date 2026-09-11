import { Calendar, ChevronDown, ChevronLeft, ChevronRight, LoaderCircle } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import {
  DATE_RANGE_PRESETS,
  addDays,
  formatChipDate,
  formatRangeLabel,
  isDateInRange,
  matchPreset,
  monthCells,
  rangeFromDays,
  rangeFromPreset,
  startOfDay,
  startOfMonth,
  trailingDaysUntilToday,
  weekdayLabels,
  type DateRangePresetId,
  type DateRangeValue,
} from "@/lib/dateRange";
import { brasiliaDateParts, formatBrasiliaDate, fromBrasiliaDate } from "@/lib/brasiliaTime";

export interface DashboardDateFilterProps {
  days: number;
  onChange: (days: number, range: DateRangeValue) => void;
  value?: DateRangeValue;
  isFetching?: boolean;
  maxDays?: number;
}

/** Filtro de período no layout do Gerenciador de Anúncios da Meta. */
export function DashboardDateFilter({
  days,
  onChange,
  value,
  isFetching = false,
  maxDays = 90,
}: DashboardDateFilterProps) {
  const labelId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [applied, setApplied] = useState<DateRangeValue>(
    () => value ?? rangeFromDays(days, new Date(), guessPreset(days)),
  );
  const [draft, setDraft] = useState<DateRangeValue>(applied);
  const [pickingEnd, setPickingEnd] = useState(false);
  const [viewMonth, setViewMonth] = useState(() => startOfMonth(applied.end));
  const [panelStyle, setPanelStyle] = useState<CSSProperties>({});

  useEffect(() => {
    if (value && !open) setApplied(value);
  }, [value, open]);

  useEffect(() => {
    if (!open) return undefined;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    function onPointer(event: MouseEvent) {
      const target = event.target as Node | null;
      if (!target) return;
      if (triggerRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointer);
    };
  }, [open]);

  function openPanel() {
    const today = new Date();
    const current = {
      ...applied,
      presetId: matchPreset(applied, today, maxDays),
    };
    setDraft(current);
    setPickingEnd(false);
    setViewMonth(startOfMonth(current.end));
    const rect = triggerRef.current?.getBoundingClientRect();
    if (rect) {
      const width = Math.min(736, window.innerWidth - 20);
      let left = rect.right - width;
      if (left < 10) left = 10;
      let top = rect.bottom + 8;
      if (top + 420 > window.innerHeight) {
        top = Math.max(10, rect.top - 8 - Math.min(520, window.innerHeight - 20));
      }
      setPanelStyle({ top, left, width });
    }
    setOpen(true);
  }

  function applyDraft() {
    const today = new Date();
    const start = draft.start <= draft.end ? draft.start : draft.end;
    const end = draft.start <= draft.end ? draft.end : draft.start;
    const presetId = matchPreset({ start, end }, today, maxDays);
    const next = { start, end, presetId };
    const nextDays = trailingDaysUntilToday(start, today, maxDays);
    setApplied(next);
    setOpen(false);
    onChange(nextDays, next);
  }

  function selectPreset(presetId: DateRangePresetId) {
    const next = rangeFromPreset(presetId, new Date(), maxDays);
    setDraft(next);
    setPickingEnd(false);
    setViewMonth(startOfMonth(next.end));
  }

  function pickDay(date: Date) {
    const today = startOfDay(new Date());
    const earliest = addDays(today, -(maxDays - 1));
    if (date > today || date < earliest) return;
    if (!pickingEnd) {
      setDraft({ start: date, end: date, presetId: "custom" });
      setPickingEnd(true);
      return;
    }
    if (date < draft.start) {
      setDraft({ start: date, end: date, presetId: "custom" });
      setPickingEnd(true);
      return;
    }
    setDraft({ start: draft.start, end: date, presetId: "custom" });
    setPickingEnd(false);
  }

  const leftMonth = viewMonth;
  const leftParts = brasiliaDateParts(viewMonth);
  const rightMonth = fromBrasiliaDate(leftParts.year, leftParts.month + 1, 1);
  const today = startOfDay(new Date());
  const canApply = Boolean(draft.start && draft.end);

  return (
    <div className="meta-date">
      <button
        ref={triggerRef}
        type="button"
        className={`meta-date-trigger${open ? " meta-date-trigger-open" : ""}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-labelledby={labelId}
        onClick={() => (open ? setOpen(false) : openPanel())}
      >
        <Calendar className="meta-date-cal" aria-hidden="true" />
        <span id={labelId} className="meta-date-trigger-text">
          {formatRangeLabel(applied.start, applied.end)}
        </span>
        <ChevronDown className={`meta-date-chevron${open ? " meta-date-chevron-open" : ""}`} />
      </button>
      {isFetching ? (
        <LoaderCircle className="meta-date-spinner animate-spin" aria-label="Atualizando período" />
      ) : null}

      {open
        ? createPortal(
            <div
              ref={panelRef}
              className="meta-date-popover"
              style={panelStyle}
              role="dialog"
              aria-label="Selecionar intervalo de datas"
            >
              <div className="meta-date-layout">
                <aside className="meta-date-aside">
                  <p className="meta-date-aside-title">Intervalos predefinidos</p>
                  <ul className="meta-date-list">
                    {DATE_RANGE_PRESETS.map((preset) => {
                      const active = draft.presetId === preset.id;
                      return (
                        <li key={preset.id}>
                          <button
                            type="button"
                            className={`meta-date-option${active ? " meta-date-option-active" : ""}`}
                            onClick={() => selectPreset(preset.id)}
                          >
                            <span className={`meta-date-radio${active ? " meta-date-radio-on" : ""}`} />
                            <span className="meta-date-option-label">{preset.label}</span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </aside>

                <div className="meta-date-main">
                  <div className="meta-date-calendars">
                    <MonthGrid
                      month={leftMonth}
                      range={draft}
                      today={today}
                      maxDays={maxDays}
                      onPick={pickDay}
                      onPrev={() => {
                        const parts = brasiliaDateParts(viewMonth);
                        setViewMonth(fromBrasiliaDate(parts.year, parts.month - 1, 1));
                      }}
                    />
                    <MonthGrid
                      month={rightMonth}
                      range={draft}
                      today={today}
                      maxDays={maxDays}
                      onPick={pickDay}
                      onNext={() => {
                        const parts = brasiliaDateParts(viewMonth);
                        setViewMonth(fromBrasiliaDate(parts.year, parts.month + 1, 1));
                      }}
                    />
                  </div>

                  <div className="meta-date-range-row">
                    <span className="meta-date-chip">
                      <Calendar className="meta-date-chip-icon" />
                      Personalizado
                      <ChevronDown className="meta-date-chip-chevron" />
                    </span>
                    <div className="meta-date-range-fields">
                      <span className="meta-date-range-value">{formatChipDate(draft.start)}</span>
                      <span className="meta-date-range-dash">–</span>
                      <span className="meta-date-range-value">{formatChipDate(draft.end)}</span>
                    </div>
                  </div>

                  <div className="meta-date-footer">
                    <p className="meta-date-tz">Horário de Brasília (GMT-3)</p>
                    <div className="meta-date-actions">
                      <button type="button" className="meta-date-cancel" onClick={() => setOpen(false)}>
                        Cancelar
                      </button>
                      <button
                        type="button"
                        className="meta-date-update"
                        disabled={!canApply}
                        onClick={applyDraft}
                      >
                        Atualizar
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}

function guessPreset(days: number): DateRangePresetId {
  if (days === 1) return "today";
  if (days === 7) return "last_7";
  if (days === 14) return "last_14";
  if (days === 28) return "last_28";
  if (days === 30) return "last_30";
  return "custom";
}

function MonthGrid({
  month,
  range,
  today,
  maxDays,
  onPick,
  onPrev,
  onNext,
}: {
  month: Date;
  range: DateRangeValue;
  today: Date;
  maxDays: number;
  onPick: (date: Date) => void;
  onPrev?: () => void;
  onNext?: () => void;
}) {
  const monthParts = brasiliaDateParts(month);
  const cells = useMemo(
    () => monthCells(monthParts.year, monthParts.month),
    [monthParts.year, monthParts.month],
  );
  const earliest = addDays(today, -(maxDays - 1));
  const title = formatBrasiliaDate(fromBrasiliaDate(monthParts.year, monthParts.month, 1), {
    month: "long",
    year: "numeric",
  });

  return (
    <div className="meta-cal">
      <div className="meta-cal-head">
        {onPrev ? (
          <button type="button" className="meta-cal-nav" onClick={onPrev} aria-label="Mês anterior">
            <ChevronLeft className="h-4 w-4" />
          </button>
        ) : (
          <span className="meta-cal-nav-spacer" />
        )}
        <p className="meta-cal-title">{title}</p>
        {onNext ? (
          <button type="button" className="meta-cal-nav" onClick={onNext} aria-label="Próximo mês">
            <ChevronRight className="h-4 w-4" />
          </button>
        ) : (
          <span className="meta-cal-nav-spacer" />
        )}
      </div>
      <div className="meta-cal-weekdays">
        {weekdayLabels().map((label) => (
          <span key={label}>{label}</span>
        ))}
      </div>
      <div className="meta-cal-grid">
        {cells.map((cell) => {
          const disabled = cell.date > today || cell.date < earliest;
          const inRange = isDateInRange(cell.date, range.start, range.end);
          const isStart = startOfDay(cell.date).getTime() === startOfDay(range.start).getTime();
          const isEnd = startOfDay(cell.date).getTime() === startOfDay(range.end).getTime();
          const isToday = startOfDay(cell.date).getTime() === today.getTime();
          const classes = [
            "meta-cal-day",
            cell.inMonth ? "" : "meta-cal-day-muted",
            disabled ? "meta-cal-day-disabled" : "",
            inRange ? "meta-cal-day-inrange" : "",
            isStart || isEnd ? "meta-cal-day-endpoint" : "",
            isToday ? "meta-cal-day-today" : "",
          ]
            .filter(Boolean)
            .join(" ");
          return (
            <button
              key={`${brasiliaDateParts(cell.date).year}-${brasiliaDateParts(cell.date).month}-${brasiliaDateParts(cell.date).day}-${cell.inMonth ? "in" : "out"}`}
              type="button"
              className={classes}
              disabled={disabled}
              onClick={() => onPick(cell.date)}
            >
              {brasiliaDateParts(cell.date).day}
            </button>
          );
        })}
      </div>
    </div>
  );
}
