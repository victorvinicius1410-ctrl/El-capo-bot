/**
 * Painel "Resumo" do pop-up de Iniciar operação.
 *
 * Diz, em frases curtas, o que o robô vai fazer com a configuração da tela:
 * quanto entra em cada operação e quando ele para. É conferência antes do
 * clique, então sai sempre dos mesmos valores que vão para o servidor.
 * Lógica pura (sem JSX), coberta por `startOperationSummary.test.ts`.
 */

import { formatBullExBalance, formatProfitAmount } from "./bullexConnection.ts";
import type { MasanielloFormView } from "./masanielloPresentation.ts";
import { cycleMinutesForTimeframe, type RobotSettings } from "./robotSettings.ts";

export type SummaryTone = "positive" | "negative" | "neutral";

export interface OperationSummaryRow {
  label: string;
  value: string;
  tone: SummaryTone;
}

/** Nome curto do mercado, o mesmo dos botões do seletor. */
export const MARKET_SHORT_LABEL: Record<string, string> = {
  OTC: "OTC",
  OPEN: "Aberto",
  BOTH: "Ambos",
};

export type OperationSummaryConfig = Pick<
  RobotSettings,
  | "timeframe"
  | "marketMode"
  | "entryValue"
  | "stopWin"
  | "stopLoss"
  | "stopWinMode"
  | "stopLossMode"
  | "stopWinOperations"
  | "stopLossOperations"
  | "martingaleEnabled"
  | "martingaleSteps"
  | "martingaleMultiplier"
  | "masanielloCapital"
>;

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/**
 * Linhas do resumo.
 *
 * Args:
 *   config: Configuração como está na tela.
 *   currency: Moeda da conta.
 *   plan: Números do Gerenciamento Consistente quando ele está valendo nesta
 *     partida (`null` = valor fixo, com ou sem gale).
 */
export function operationSummaryRows(
  config: OperationSummaryConfig,
  currency: string | null | undefined,
  plan: MasanielloFormView | null,
): OperationSummaryRow[] {
  const money = (value: number) => formatBullExBalance(value, currency);
  const rows: OperationSummaryRow[] = [
    {
      label: "Operação",
      value: `${cycleMinutesForTimeframe(config.timeframe)} min · ${MARKET_SHORT_LABEL[config.marketMode] ?? config.marketMode}`,
      tone: "neutral",
    },
  ];
  if (plan) {
    rows.push(
      { label: "Capital do ciclo", value: money(config.masanielloCapital), tone: "neutral" },
      {
        label: `Meta (${plural(plan.wins, "acerto", "acertos")})`,
        value: formatProfitAmount(plan.summary.target_profit, currency),
        tone: "positive",
      },
      {
        label: `Limite (${plural(plan.summary.max_errors + 1, "erro", "erros")})`,
        value: `−${money(config.masanielloCapital)}`,
        tone: "negative",
      },
      { label: "1ª entrada", value: money(plan.summary.first_stake), tone: "neutral" },
      { label: "Maior entrada", value: money(plan.summary.max_stake), tone: "neutral" },
    );
    return rows;
  }
  rows.push(
    { label: "Cada entrada", value: money(config.entryValue), tone: "neutral" },
    {
      label: "Para ao ganhar",
      value:
        config.stopWinMode === "operations"
          ? plural(config.stopWinOperations, "WIN", "WINs")
          : formatProfitAmount(config.stopWin, currency),
      tone: "positive",
    },
    {
      label: "Para ao perder",
      value:
        config.stopLossMode === "operations"
          ? plural(config.stopLossOperations, "LOSS", "LOSSes")
          : `−${money(config.stopLoss)}`,
      tone: "negative",
    },
    {
      label: "Gale",
      value: config.martingaleEnabled
        ? `até ${config.martingaleSteps} · ${String(config.martingaleMultiplier).replace(".", ",")}x`
        : "Desligado",
      tone: "neutral",
    },
  );
  return rows;
}
