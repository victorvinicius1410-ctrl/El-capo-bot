/**
 * Textos e validação do Gerenciamento Consistente no painel.
 *
 * Tudo que o cliente lê sobre o plano sai daqui, para o diálogo de Iniciar e a
 * página de Configurações dizerem exatamente a mesma coisa. Lógica pura (sem
 * JSX), coberta por `masanielloPresentation.test.ts`.
 */

import { formatBullExBalance, formatProfitAmount } from "./bullexConnection.ts";
import {
  MASANIELLO_DEFAULT_PAYOUT_REF,
  MASANIELLO_MAX_OPERATIONS,
  MASANIELLO_PROFILES,
  type MasanielloCycle,
  type MasanielloPlanSummary,
  type MasanielloProfile,
  type MasanielloRow,
  masanielloPlanForProfile,
  masanielloPlanSummary,
  validMasanielloPlan,
} from "./masaniello.ts";
import { entryLimitsForCurrency } from "./robotSettings.ts";

export const CONSISTENT_MANAGEMENT_LABEL = "Gerenciamento Consistente";

export interface MasanielloProfileOption {
  value: MasanielloProfile;
  label: string;
  description: string;
  /** Versão curta para os botões de perfil: "10 op · 4 acertos". */
  short: string;
}

function profileDescription(profile: Exclude<MasanielloProfile, "personalizado">): string {
  const [operations, wins] = MASANIELLO_PROFILES[profile];
  return `${operations} operações · ${wins} acertos`;
}

function profileShort(profile: Exclude<MasanielloProfile, "personalizado">): string {
  const [operations, wins] = MASANIELLO_PROFILES[profile];
  return `${wins} de ${operations}`;
}

export const MASANIELLO_PROFILE_OPTIONS: MasanielloProfileOption[] = [
  {
    value: "conservador",
    label: "Conservador",
    description: profileDescription("conservador"),
    short: profileShort("conservador"),
  },
  {
    value: "moderado",
    label: "Moderado",
    description: profileDescription("moderado"),
    short: profileShort("moderado"),
  },
  {
    value: "agressivo",
    label: "Agressivo",
    description: profileDescription("agressivo"),
    short: profileShort("agressivo"),
  },
  {
    value: "personalizado",
    label: "Personalizado",
    description: "Você define",
    short: "você define",
  },
];

/** Payout de referência do plano: o piso que o robô aceita (default 80%). */
export function masanielloPayoutRef(minPayout?: number | null): number {
  const value = Number(minPayout);
  return Number.isFinite(value) && value > 0 ? value : MASANIELLO_DEFAULT_PAYOUT_REF;
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

export interface MasanielloFormInput {
  capital: number;
  profile: MasanielloProfile;
  operations: number;
  wins: number;
  payoutRef?: number | null;
  currency?: string | null;
  balance?: number | null;
  /** O "Iniciar" vai continuar um ciclo em andamento (não exige o capital inteiro). */
  continuing?: boolean;
}

export interface MasanielloFormView {
  operations: number;
  wins: number;
  payoutRef: number;
  minEntry: number;
  summary: MasanielloPlanSummary;
  /** "4 acertos = +R$ 10,58" */
  targetLabel: string;
  /** "7 erros = −R$ 100,00" */
  limitLabel: string;
  /** Impede salvar/iniciar. */
  error: string | null;
  /** Avisos que não impedem, mas o cliente precisa ler antes de ligar. */
  notices: string[];
}

/**
 * Números e avisos do plano para o formulário.
 *
 * O capital mínimo é o que faz a 1ª entrada alcançar o mínimo da corretora —
 * a mesma regra que o `POST /robot/config` valida.
 */
export function masanielloFormView(input: MasanielloFormInput): MasanielloFormView {
  const { operations, wins } = masanielloPlanForProfile(
    input.profile,
    input.operations,
    input.wins,
  );
  const payoutRef = masanielloPayoutRef(input.payoutRef);
  const minEntry = entryLimitsForCurrency(input.currency).min;
  const capital = Number.isFinite(input.capital) && input.capital > 0 ? input.capital : 0;
  const summary = masanielloPlanSummary(capital, operations, wins, payoutRef, minEntry);
  const money = (value: number) => formatBullExBalance(value, input.currency);
  const errorsToLose = summary.max_errors + 1;

  let error: string | null = null;
  const requestedPlanValid =
    input.profile !== "personalizado" ||
    validMasanielloPlan(Math.trunc(input.operations), Math.trunc(input.wins));
  if (!requestedPlanValid) {
    error =
      input.operations > MASANIELLO_MAX_OPERATIONS
        ? `O plano aceita no máximo ${MASANIELLO_MAX_OPERATIONS} operações.`
        : "O número de acertos tem que ser menor que o número de operações.";
  } else if (capital < summary.min_capital) {
    error =
      `Capital mínimo para este plano: ${money(summary.min_capital)}. ` +
      `Abaixo disso a primeira entrada fica menor que o mínimo da corretora (${money(minEntry)}).`;
  } else if (input.balance != null && Number.isFinite(input.balance) && input.balance <= 0) {
    error = "Você está sem saldo para iniciar. Faça um depósito na Bullex.";
  } else if (
    !input.continuing &&
    input.balance != null &&
    Number.isFinite(input.balance) &&
    capital > input.balance
  ) {
    error = `Seu saldo (${money(input.balance)}) é menor que o capital do ciclo.`;
  }

  const notices: string[] = [];
  if (!error && summary.adjusts_to_min) {
    notices.push(
      `Com este capital, parte das entradas do plano fica abaixo do mínimo da corretora ` +
        `(${money(minEntry)}) e sobe para o mínimo. Por isso o resultado final pode ficar um pouco ` +
        `acima ou abaixo da meta. A partir de ${money(summary.full_plan_capital)} o plano roda sem ajuste.`,
    );
  }

  return {
    operations,
    wins,
    payoutRef,
    minEntry,
    summary,
    targetLabel: `${plural(wins, "acerto", "acertos")} = ${formatProfitAmount(summary.target_profit, input.currency)}`,
    limitLabel: `${plural(errorsToLose, "erro", "erros")} = −${money(capital)}`,
    error,
    notices,
  };
}

export type MasanielloTone = "positive" | "negative" | "neutral";

export interface MasanielloCycleHeadline {
  title: string;
  detail: string;
  tone: MasanielloTone;
}

/** Título e explicação do estado do ciclo (cartão da calculadora). */
export function masanielloCycleHeadline(
  cycle: MasanielloCycle,
  currency?: string | null,
): MasanielloCycleHeadline {
  const balance = cycle.capital_atual - cycle.capital_inicial;
  const balanceLabel = formatProfitAmount(balance, currency);
  if (cycle.status === "TARGET_HIT") {
    return {
      title: "Meta do ciclo batida",
      detail: `${plural(cycle.wins, "acerto", "acertos")} em ${plural(cycle.wins + cycle.losses, "operação", "operações")}. Resultado do ciclo: ${balanceLabel}.`,
      tone: "positive",
    };
  }
  if (cycle.status === "BUST") {
    return {
      title:
        cycle.end_reason === "NO_CAPITAL"
          ? "Ciclo encerrado: o capital que sobrou não paga o mínimo da corretora"
          : "Erros esgotados: o capital do ciclo acabou",
      detail: `Resultado do ciclo: ${balanceLabel}.`,
      tone: "negative",
    };
  }
  if (cycle.status === "ABANDONED") {
    return {
      title:
        cycle.end_reason === "RESULT_UNKNOWN"
          ? "Ciclo encerrado sem o resultado da última ordem"
          : "Ciclo encerrado antes do fim",
      detail: `Resultado até ali: ${balanceLabel}.`,
      tone: "neutral",
    };
  }
  if (cycle.pending?.unknown) {
    return {
      title: "Aguardando o resultado da última ordem",
      detail:
        "O resultado não chegou no prazo. Confira a operação na corretora: sem ele o ciclo não sabe o capital atual.",
      tone: "neutral",
    };
  }
  return {
    title: "Ciclo em andamento",
    detail: `${cycle.wins} de ${plural(cycle.w, "acerto", "acertos")} · ${plural(cycle.losses, "erro", "erros")} de ${cycle.max_errors} aceitos.`,
    tone: "neutral",
  };
}

/** Balanço do ciclo em valor e em percentual do capital inicial. */
export function masanielloCycleBalance(cycle: MasanielloCycle): {
  amount: number;
  percent: number;
} {
  const amount = Math.round((cycle.capital_atual - cycle.capital_inicial) * 100) / 100;
  const percent =
    cycle.capital_inicial > 0 ? Math.round((amount / cycle.capital_inicial) * 1000) / 10 : 0;
  return { amount, percent };
}

/** Coluna "Observação" de uma linha da calculadora. */
export function masanielloRowNote(
  row: MasanielloRow,
  cycle: Pick<MasanielloCycle, "status" | "rows" | "end_reason">,
): string {
  const last = cycle.rows[cycle.rows.length - 1] === row;
  if (row.result === "DRAW") return "Empate: valor devolvido, não conta";
  if (last && cycle.status === "TARGET_HIT") return "Meta batida";
  if (last && cycle.status === "BUST" && cycle.end_reason !== "NO_CAPITAL") {
    return "Erros esgotados";
  }
  const left = plural(row.errors_left, "erro ainda aceito", "erros ainda aceitos");
  return row.adjusted_to_min ? `Entrada ajustada ao mínimo · ${left}` : left;
}

/** Resumo de uma linha para o overlay do robô (ou `null` sem ciclo ativo). */
export function masanielloOverlayLine(
  cycle: MasanielloCycle | null | undefined,
  currency?: string | null,
): string | null {
  if (!cycle || cycle.status !== "ACTIVE") return null;
  const next =
    cycle.next_stake != null ? ` · próxima ${formatBullExBalance(cycle.next_stake, currency)}` : "";
  return `Ciclo ${cycle.wins}/${cycle.w} acertos · ${cycle.losses}/${cycle.max_errors} erros${next}`;
}
