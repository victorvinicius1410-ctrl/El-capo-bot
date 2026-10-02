/**
 * Gerenciamento Consistente: o Algoritmo de Masaniello no painel.
 *
 * Gêmeo de `backend/backend/masaniello.py`. Serve para a PRÉVIA do plano (o
 * cliente simula W/L antes de ligar) e para os números do formulário. Quem
 * decide o valor de cada ordem de verdade é o backend; este arquivo só não
 * pode mostrar um número diferente do que o robô vai usar.
 *
 * Os dois motores são testados contra os MESMOS vetores
 * (`backend/tests/fixtures/masaniello_vectors.json`). Mudou a conta lá, muda
 * aqui — inclusive os arredondamentos, que são escritos à mão dos dois lados
 * para dar o mesmo resultado bit a bit.
 *
 * Sem dependências e só com imports relativos `.ts`: é importado por teste
 * que roda em `node --experimental-strip-types`.
 */

export type MasanielloStatus = "ACTIVE" | "TARGET_HIT" | "BUST" | "ABANDONED";
export type MasanielloResult = "WIN" | "LOSS" | "DRAW";
export type MasanielloProfile = "conservador" | "moderado" | "agressivo" | "personalizado";

export interface MasanielloRow {
  /** Número da operação no ciclo; `null` no empate (não conta). */
  seq: number | null;
  order_id: string;
  at: string | null;
  asset: string | null;
  result: MasanielloResult;
  stake: number;
  stake_planned: number;
  adjusted_to_min: boolean;
  payout: number | null;
  profit: number;
  capital_after: number;
  hit_rate: number;
  errors_left: number;
}

export interface MasanielloPending {
  order_id: string;
  stake: number;
  stake_planned: number;
  adjusted_to_min: boolean;
  asset: string | null;
  payout: number | null;
  at: string | null;
  unknown: boolean;
}

export interface MasanielloCycle {
  id: string;
  rev: number;
  status: MasanielloStatus;
  end_reason: string | null;
  started_at: string | null;
  ended_at: string | null;
  capital_inicial: number;
  capital_atual: number;
  n: number;
  w: number;
  payout_ref: number;
  min_entry: number;
  target: number;
  max_errors: number;
  wins: number;
  losses: number;
  pending: MasanielloPending | null;
  next_stake: number | null;
  next_stake_adjusted: boolean;
  rows: MasanielloRow[];
}

export interface MasanielloPlanSummary {
  target: number;
  target_profit: number;
  target_percent: number;
  max_errors: number;
  first_stake: number;
  min_stake: number;
  max_stake: number;
  adjusts_to_min: boolean;
  min_capital: number;
  full_plan_capital: number;
}

export const MASANIELLO_PROFILE_CUSTOM = "personalizado" as const;
export const MASANIELLO_DEFAULT_PROFILE = "conservador" as const;
/** perfil -> [operações, acertos necessários] */
export const MASANIELLO_PROFILES: Record<
  Exclude<MasanielloProfile, "personalizado">,
  readonly [number, number]
> = {
  conservador: [10, 4],
  moderado: [10, 5],
  agressivo: [10, 6],
};
export const MASANIELLO_MAX_OPERATIONS = 100;
export const MASANIELLO_DEFAULT_PAYOUT_REF = 80;
export const MASANIELLO_DEFAULT_CAPITAL = 100;

/** Arredonda para centavos (meio para cima). Igual ao `cents` do Python. */
export function cents(value: number): number {
  return Math.floor(Number(value) * 100 + 0.5) / 100;
}

/** Trunca para centavos, sem nunca passar do valor. */
export function floorCents(value: number): number {
  return Math.floor(Number(value) * 100 + 1e-6) / 100;
}

function ceilCents(value: number): number {
  return Math.ceil(Number(value) * 100 - 1e-6) / 100;
}

/** Arredonda para uma casa (percentual de acerto). */
export function tenths(value: number): number {
  return Math.floor(Number(value) * 10 + 0.5) / 10;
}

/** Potência por multiplicação repetida: mesmo resultado do backend. */
function power(base: number, exponent: number): number {
  let result = 1;
  for (let index = 0; index < exponent; index += 1) result *= base;
  return result;
}

/** Converte payout em percentual (80) na cotação da matriz (1,80). */
export function oddsFromPayout(payoutPercent: unknown): number {
  let payout = Number(payoutPercent);
  if (!Number.isFinite(payout) || payout <= 0) payout = MASANIELLO_DEFAULT_PAYOUT_REF;
  return 1 + payout / 100;
}

export function normalizeMasanielloProfile(profile: unknown): MasanielloProfile {
  const text = String(profile ?? "")
    .trim()
    .toLowerCase();
  if (text === "conservador" || text === "moderado" || text === "agressivo") return text;
  if (text === MASANIELLO_PROFILE_CUSTOM) return MASANIELLO_PROFILE_CUSTOM;
  return text ? MASANIELLO_PROFILE_CUSTOM : MASANIELLO_DEFAULT_PROFILE;
}

/** Plano aceito: `1 <= W < N <= MAX` (ao menos 1 erro aceito). */
export function validMasanielloPlan(operations: unknown, wins: unknown): boolean {
  const n = Number(operations);
  const w = Number(wins);
  if (!Number.isInteger(n) || !Number.isInteger(w)) return false;
  return w >= 1 && w < n && n <= MASANIELLO_MAX_OPERATIONS;
}

/** `N` e `W` que valem para o perfil (no personalizado, os informados). */
export function masanielloPlanForProfile(
  profile: MasanielloProfile,
  operations: number,
  wins: number,
): { operations: number; wins: number } {
  if (profile !== MASANIELLO_PROFILE_CUSTOM) {
    const [n, w] = MASANIELLO_PROFILES[profile];
    return { operations: n, wins: w };
  }
  const n = Math.max(2, Math.min(MASANIELLO_MAX_OPERATIONS, Math.trunc(Number(operations) || 0)));
  const w = Math.max(1, Math.min(n - 1, Math.trunc(Number(wins) || 0)));
  return { operations: n, wins: w };
}

type Matrix = (number | null)[][];

/** Matriz do Masaniello: `M[m][w]` com `m` operações feitas e `w` acertos. */
export function masanielloMatrix(operations: number, wins: number, odds: number): Matrix {
  const table: Matrix = [];
  for (let done = 0; done < operations + 2; done += 1) {
    table.push(new Array<number | null>(wins + 2).fill(null));
  }
  for (let done = operations; done >= 0; done -= 1) {
    for (let won = 0; won <= wins; won += 1) {
      const needed = wins - won;
      const left = operations - done;
      if (needed === 0) table[done][won] = 1;
      else if (needed === left) table[done][won] = power(odds, left);
      else if (needed > left) table[done][won] = null;
      else {
        const a = table[done + 1][won] as number;
        const b = table[done + 1][won + 1] as number;
        table[done][won] = (odds * a * b) / (a + (odds - 1) * b);
      }
    }
  }
  return table;
}

/** Fração do capital atual que entra na próxima operação. */
export function stakeFraction(table: Matrix, done: number, won: number, odds: number): number {
  const a = table[done + 1][won];
  const b = table[done + 1][won + 1];
  if (a === null || b === null) return 1;
  return 1 - (odds * b) / (a + (odds - 1) * b);
}

/** Primeira, menor e maior entrada do plano, como fração do capital inicial. */
export function stakeRange(
  operations: number,
  wins: number,
  payoutRef: number,
): [number, number, number] {
  const odds = oddsFromPayout(payoutRef);
  const table = masanielloMatrix(operations, wins, odds);
  const start = table[0][0] ?? 1;
  let smallest = Infinity;
  let largest = 0;
  for (let done = 0; done < operations; done += 1) {
    for (let won = 0; won <= Math.min(done, wins - 1); won += 1) {
      const node = table[done][won];
      if (node === null || done - won > operations - wins) continue;
      const fraction = (stakeFraction(table, done, won, odds) * start) / node;
      smallest = Math.min(smallest, fraction);
      largest = Math.max(largest, fraction);
    }
  }
  return [stakeFraction(table, 0, 0, odds), smallest, largest];
}

/** Menor capital em que a 1ª entrada já alcança o mínimo da corretora. */
export function masanielloMinCapital(
  operations: number,
  wins: number,
  payoutRef: number,
  minEntry: number,
): number {
  const [first] = stakeRange(operations, wins, payoutRef);
  if (first <= 0) return minEntry;
  return ceilCents(minEntry / first);
}

/** Capital a partir do qual NENHUMA entrada precisa ser puxada ao mínimo. */
export function masanielloFullPlanCapital(
  operations: number,
  wins: number,
  payoutRef: number,
  minEntry: number,
): number {
  const [, smallest] = stakeRange(operations, wins, payoutRef);
  if (smallest <= 0 || !Number.isFinite(smallest)) return minEntry;
  return ceilCents(minEntry / smallest);
}

/** Números do plano antes da 1ª ordem (meta, limites e tamanho das entradas). */
export function masanielloPlanSummary(
  capital: number,
  operations: number,
  wins: number,
  payoutRef: number,
  minEntry = 0,
): MasanielloPlanSummary {
  const odds = oddsFromPayout(payoutRef);
  const start = masanielloMatrix(operations, wins, odds)[0][0] ?? 1;
  const [first, smallest, largest] = stakeRange(operations, wins, payoutRef);
  const minimum = Number(minEntry) || 0;
  return {
    target: cents(capital * start),
    target_profit: cents(capital * start - capital),
    target_percent: cents((start - 1) * 100),
    max_errors: operations - wins,
    first_stake: cents(first * capital),
    min_stake: cents(smallest * capital),
    max_stake: cents(largest * capital),
    adjusts_to_min: minimum > 0 && cents(smallest * capital) < minimum,
    min_capital: minimum > 0 ? masanielloMinCapital(operations, wins, payoutRef, minimum) : 0,
    full_plan_capital:
      minimum > 0 ? masanielloFullPlanCapital(operations, wins, payoutRef, minimum) : 0,
  };
}

export interface MasanielloNextStake {
  stake: number;
  stake_planned: number;
  adjusted_to_min: boolean;
}

/** Valor da próxima ordem do ciclo; `null` se não há (encerrado ou sem capital). */
export function masanielloNextStake(
  cycle: MasanielloCycle | null | undefined,
  minEntry?: number,
): MasanielloNextStake | null {
  if (!cycle || cycle.status !== "ACTIVE") return null;
  const minimum = minEntry === undefined ? Number(cycle.min_entry) || 0 : Number(minEntry) || 0;
  const capital = Number(cycle.capital_atual) || 0;
  const available = floorCents(capital);
  if (available <= 0 || available < minimum) return null;
  if (cycle.wins >= cycle.w || cycle.losses > cycle.n - cycle.w) return null;
  const odds = oddsFromPayout(cycle.payout_ref);
  const table = masanielloMatrix(cycle.n, cycle.w, odds);
  const planned = cents(
    stakeFraction(table, cycle.wins + cycle.losses, cycle.wins, odds) * capital,
  );
  let stake = planned;
  let adjusted = false;
  if (stake < minimum) {
    stake = minimum;
    adjusted = true;
  }
  if (stake > available) stake = available;
  return { stake, stake_planned: planned, adjusted_to_min: adjusted };
}

function withNextStake(cycle: MasanielloCycle): MasanielloCycle {
  const next = masanielloNextStake(cycle);
  return {
    ...cycle,
    next_stake: next ? next.stake : null,
    next_stake_adjusted: Boolean(next?.adjusted_to_min),
  };
}

export function newMasanielloCycle(
  capital: number,
  operations: number,
  wins: number,
  payoutRef: number,
  minEntry = 0,
): MasanielloCycle {
  const summary = masanielloPlanSummary(capital, operations, wins, payoutRef);
  return withNextStake({
    id: "simulacao",
    rev: 1,
    status: "ACTIVE",
    end_reason: null,
    started_at: null,
    ended_at: null,
    capital_inicial: cents(capital),
    capital_atual: cents(capital),
    n: operations,
    w: wins,
    payout_ref: cents(payoutRef),
    min_entry: Number(minEntry) || 0,
    target: summary.target,
    max_errors: summary.max_errors,
    wins: 0,
    losses: 0,
    pending: null,
    next_stake: null,
    next_stake_adjusted: false,
    rows: [],
  });
}

function closeCycle(cycle: MasanielloCycle, status: MasanielloStatus, reason: string) {
  return withNextStake({ ...cycle, status, end_reason: reason });
}

function applySimulatedResult(
  cycle: MasanielloCycle,
  orderId: string,
  result: MasanielloResult,
  profit: number,
  order: MasanielloNextStake,
  payout: number,
): MasanielloCycle {
  let won = cycle.wins;
  let lost = cycle.losses;
  let capital = cycle.capital_atual;
  let rowProfit = 0;
  if (result === "WIN") {
    const gain = profit > 0 ? profit : 0;
    capital = cents(capital + gain);
    won += 1;
    rowProfit = cents(gain);
  } else if (result === "LOSS") {
    const loss = profit < 0 ? profit : -order.stake;
    capital = cents(Math.max(0, capital + loss));
    lost += 1;
    rowProfit = cents(loss);
  }
  const total = won + lost;
  const row: MasanielloRow = {
    seq: result === "DRAW" ? null : total,
    order_id: orderId,
    at: null,
    asset: null,
    result,
    stake: cents(order.stake),
    stake_planned: cents(order.stake_planned),
    adjusted_to_min: order.adjusted_to_min,
    payout,
    profit: rowProfit,
    capital_after: capital,
    hit_rate: total ? tenths((won / total) * 100) : 0,
    errors_left: Math.max(0, cycle.n - cycle.w - lost),
  };
  let updated: MasanielloCycle = {
    ...cycle,
    rows: [...cycle.rows, row],
    wins: won,
    losses: lost,
    capital_atual: capital,
    rev: cycle.rev + 1,
  };
  if (won >= cycle.w) updated = { ...updated, status: "TARGET_HIT", end_reason: "TARGET" };
  else if (lost > cycle.n - cycle.w) updated = { ...updated, status: "BUST", end_reason: "ERRORS" };
  return withNextStake(updated);
}

const RESULT_ALIASES: Record<string, MasanielloResult> = {
  W: "WIN",
  L: "LOSS",
  D: "DRAW",
  WIN: "WIN",
  LOSS: "LOSS",
  DRAW: "DRAW",
};

/**
 * Roda um ciclo inteiro em cima de uma sequência de resultados.
 *
 * É a prévia do painel. O lucro de cada WIN usa `payoutReal` (default: o de
 * referência, que é o pior caso que o robô aceita).
 */
export function simulateMasaniello(
  capital: number,
  operations: number,
  wins: number,
  payoutRef: number,
  results: readonly string[],
  options: { minEntry?: number; payoutReal?: number | null } = {},
): MasanielloCycle {
  let cycle = newMasanielloCycle(capital, operations, wins, payoutRef, options.minEntry ?? 0);
  const paid = Number(options.payoutReal ?? payoutRef) / 100;
  for (let index = 0; index < results.length; index += 1) {
    const order = masanielloNextStake(cycle);
    if (order === null) {
      if (cycle.status === "ACTIVE") cycle = closeCycle(cycle, "BUST", "NO_CAPITAL");
      break;
    }
    const result = RESULT_ALIASES[String(results[index]).trim().toUpperCase()];
    if (!result) continue;
    let profit = 0;
    if (result === "WIN") profit = cents(order.stake * paid);
    else if (result === "LOSS") profit = -order.stake;
    cycle = applySimulatedResult(
      cycle,
      `sim-${index + 1}`,
      result,
      profit,
      order,
      cents(paid * 100),
    );
  }
  if (cycle.status === "ACTIVE" && masanielloNextStake(cycle) === null) {
    cycle = closeCycle(cycle, "BUST", "NO_CAPITAL");
  }
  return cycle;
}

function numberOr(value: unknown, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function nullableNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeStatus(value: unknown): MasanielloStatus {
  const text = String(value ?? "").toUpperCase();
  return text === "TARGET_HIT" || text === "BUST" || text === "ABANDONED" ? text : "ACTIVE";
}

function normalizeResult(value: unknown): MasanielloResult | null {
  return RESULT_ALIASES[String(value ?? "").toUpperCase()] ?? null;
}

/**
 * Ciclo que veio do backend (`robot_state.masaniello_cycle`), campo a campo.
 *
 * Devolve `null` para qualquer coisa que não pareça um ciclo: a calculadora
 * some em vez de mostrar número inventado.
 */
export function normalizeMasanielloCycle(raw: unknown): MasanielloCycle | null {
  if (!raw || typeof raw !== "object") return null;
  const data = raw as Record<string, unknown>;
  const id = String(data.id ?? "").trim();
  const n = Math.trunc(numberOr(data.n, 0));
  const w = Math.trunc(numberOr(data.w, 0));
  if (!id || n <= 0 || w <= 0) return null;
  const rows: MasanielloRow[] = [];
  for (const item of Array.isArray(data.rows) ? data.rows : []) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    const result = normalizeResult(row.result);
    if (!result) continue;
    rows.push({
      seq: nullableNumber(row.seq),
      order_id: String(row.order_id ?? ""),
      at: row.at ? String(row.at) : null,
      asset: row.asset ? String(row.asset) : null,
      result,
      stake: numberOr(row.stake, 0),
      stake_planned: numberOr(row.stake_planned, numberOr(row.stake, 0)),
      adjusted_to_min: row.adjusted_to_min === true,
      payout: nullableNumber(row.payout),
      profit: numberOr(row.profit, 0),
      capital_after: numberOr(row.capital_after, 0),
      hit_rate: numberOr(row.hit_rate, 0),
      errors_left: Math.trunc(numberOr(row.errors_left, 0)),
    });
  }
  let pending: MasanielloPending | null = null;
  if (data.pending && typeof data.pending === "object") {
    const item = data.pending as Record<string, unknown>;
    pending = {
      order_id: String(item.order_id ?? ""),
      stake: numberOr(item.stake, 0),
      stake_planned: numberOr(item.stake_planned, numberOr(item.stake, 0)),
      adjusted_to_min: item.adjusted_to_min === true,
      asset: item.asset ? String(item.asset) : null,
      payout: nullableNumber(item.payout),
      at: item.at ? String(item.at) : null,
      unknown: item.unknown === true,
    };
  }
  return {
    id,
    rev: Math.trunc(numberOr(data.rev, 0)),
    status: normalizeStatus(data.status),
    end_reason: data.end_reason ? String(data.end_reason) : null,
    started_at: data.started_at ? String(data.started_at) : null,
    ended_at: data.ended_at ? String(data.ended_at) : null,
    capital_inicial: numberOr(data.capital_inicial, 0),
    capital_atual: numberOr(data.capital_atual, 0),
    n,
    w,
    payout_ref: numberOr(data.payout_ref, MASANIELLO_DEFAULT_PAYOUT_REF),
    min_entry: numberOr(data.min_entry, 0),
    target: numberOr(data.target, 0),
    max_errors: Math.trunc(numberOr(data.max_errors, n - w)),
    wins: Math.trunc(numberOr(data.wins, 0)),
    losses: Math.trunc(numberOr(data.losses, 0)),
    pending,
    next_stake: nullableNumber(data.next_stake),
    next_stake_adjusted: data.next_stake_adjusted === true,
    rows,
  };
}

/**
 * O "Iniciar" vai continuar este ciclo? Só se ele está em andamento e o plano
 * configurado é o mesmo (capital, operações, acertos e payout de referência).
 * É a mesma regra de `masaniello_begin_if_needed` no backend.
 */
export function masanielloWillContinue(
  cycle: MasanielloCycle | null | undefined,
  plan: { capital: number; operations: number; wins: number; payoutRef: number },
): boolean {
  if (!cycle || cycle.status !== "ACTIVE") return false;
  return (
    cents(cycle.capital_inicial) === cents(plan.capital) &&
    cycle.n === plan.operations &&
    cycle.w === plan.wins &&
    cents(cycle.payout_ref) === cents(plan.payoutRef)
  );
}
