import { Calendar, LoaderCircle } from "lucide-react";

export interface DashboardDateFilterProps {
  days: number;
  onChange: (days: number) => void;
  isFetching?: boolean;
}

const PRESETS = [
  { label: "Hoje", days: 1 },
  { label: "Últimos 7 dias", days: 7 },
  { label: "Últimos 14 dias", days: 14 },
  { label: "Últimos 30 dias", days: 30 },
  { label: "Máximo", days: 90 },
] as const;

/** Seleciona o intervalo de dias usado nos dashboards. */
export function DashboardDateFilter({
  days,
  onChange,
  isFetching = false,
}: DashboardDateFilterProps) {
  return (
    <label className="meta-date inline-flex items-center gap-2">
      <Calendar className="h-4 w-4" aria-hidden="true" />
      <span className="sr-only">Intervalo de datas</span>
      <select
        className="meta-date-trigger rounded-lg border border-border bg-card px-3 py-2 text-sm"
        value={days}
        onChange={(event) => onChange(Number(event.target.value))}
      >
        {PRESETS.map((preset) => (
          <option key={preset.days} value={preset.days}>{preset.label}</option>
        ))}
        {!PRESETS.some((preset) => preset.days === days) ? (
          <option value={days}>{days} dias</option>
        ) : null}
      </select>
      {isFetching ? <LoaderCircle className="h-4 w-4 animate-spin" aria-label="Atualizando período" /> : null}
    </label>
  );
}
