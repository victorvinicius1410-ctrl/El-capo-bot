import { useState } from "react";
import { currencySymbol } from "@/lib/bullexConnection";

interface MoneyInputProps {
  label: string;
  value: number;
  onChange: (value: string) => void;
  currency?: string | null;
  disabled?: boolean;
  min?: number;
  max?: number;
  step?: number | string;
  helperText?: string;
  size?: "default" | "compact";
}

/**
 * Campo numérico com prefixo da moeda.
 *
 * Mantém um rascunho de texto enquanto o campo está focado para a pessoa
 * conseguir apagar e digitar valores baixos (ex.: 5) sem o controlled input
 * resetar para o valor anterior a cada tecla.
 */
export function MoneyInput({
  label,
  value,
  onChange,
  currency = null,
  disabled = false,
  min = 0,
  max,
  step = 1,
  helperText,
  size = "default",
}: MoneyInputProps) {
  const symbol = currencySymbol(currency);
  const compact = size === "compact";
  const [draft, setDraft] = useState<string | null>(null);
  const focused = draft !== null;
  const display = focused ? draft : Number.isFinite(value) ? String(value) : "";

  return (
    <label className={compact ? "block" : "block space-y-1.5 text-sm"}>
      <span className={compact ? "mb-1 block text-xs font-medium text-muted-foreground" : "font-medium"}>
        {label}
      </span>
      <div
        className={`flex items-center overflow-hidden border border-border bg-background/40 ring-primary focus-within:ring-2 ${compact ? "rounded-lg" : "rounded-xl"} ${disabled ? "opacity-50" : ""}`}
      >
        <span
          className={`shrink-0 border-r border-border font-semibold text-muted-foreground ${compact ? "px-2 py-2 text-xs" : "px-3 py-2 text-sm"}`}
          aria-hidden
        >
          {symbol}
        </span>
        <input
          type="text"
          inputMode="decimal"
          min={min}
          max={max}
          step={step}
          value={display}
          disabled={disabled}
          onFocus={() => setDraft(Number.isFinite(value) ? String(value) : "")}
          onBlur={() => setDraft(null)}
          onChange={(event) => {
            const next = event.target.value;
            setDraft(next);
            onChange(next);
          }}
          className={`w-full bg-transparent outline-none disabled:cursor-not-allowed ${compact ? "px-2 py-2 text-xs font-semibold" : "px-3 py-2 text-sm font-semibold"}`}
          aria-label={`${label} em ${symbol}`}
        />
      </div>
      {helperText ? (
        <span
          className={`mt-1 block whitespace-pre-line text-muted-foreground ${compact ? "text-[11px]" : "text-xs"}`}
        >
          {helperText}
        </span>
      ) : null}
    </label>
  );
}
