import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { ApiError, apiRequest } from "@/lib/api";
import { useAuth } from "@/lib/useAuth";

export type RobotHistoryDays = number;
export type RobotHistoryResult = "WIN" | "LOSS" | "DRAW";

export interface RobotHistoryItem {
  id: string;
  createdAt: string | null;
  accountMode: "REAL" | null;
  active: string;
  direction: "CALL" | "PUT";
  amount: number;
  confidence: number | null;
  payout: number | null;
  orderId: string | null;
  result: RobotHistoryResult;
  badge: "NORMAL" | "GALE 1" | "GALE WIN" | "GALE LOSS";
  isGale: boolean;
  galeStep: number | null;
  profit: number;
  openedAt: string | null;
  finishedAt: string | null;
  timeframe: string | null;
  strategyName: string | null;
  strategyKey: string | null;
  strategySummary: string | null;
  analysisDetail: string | null;
  speechPreview: string | null;
}

export interface RobotStats {
  wins: number;
  losses: number;
  totalTrades: number;
  winRate: number;
  profit: number;
  profitFactor: number | null;
  currentWinStreak: number;
  currentLossStreak: number;
  bestWinStreak: number;
  bestLossStreak: number;
}

export const ROBOT_HISTORY_QUERY_KEY = ["robot-history"] as const;
export const ROBOT_STATS_QUERY_KEY = ["robot-stats"] as const;
const EMPTY_STATS: RobotStats = {
  wins: 0,
  losses: 0,
  totalTrades: 0,
  winRate: 0,
  profit: 0,
  profitFactor: null,
  currentWinStreak: 0,
  currentLossStreak: 0,
  bestWinStreak: 0,
  bestLossStreak: 0,
};

/** Busca e normaliza o histórico operacional da sessão atual (igual para cliente e marketing). */
export function useRobotHistory(days: RobotHistoryDays): UseQueryResult<RobotHistoryItem[], Error> {
  const { user } = useAuth();
  return useQuery({
    queryKey: [...ROBOT_HISTORY_QUERY_KEY, user?.id, days],
    queryFn: async () => {
      const response = await apiRequest<unknown>(`/robot/history?days=${days}`);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return normalizeHistory(response.data);
    },
    enabled: Boolean(user?.id),
    refetchInterval: 45_000,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
    retry: 1,
    staleTime: 30_000,
  });
}

/** Busca os indicadores agregados do robô (igual para cliente e marketing). */
export function useRobotStats(days: RobotHistoryDays): UseQueryResult<RobotStats, Error> {
  const { user } = useAuth();
  return useQuery({
    queryKey: [...ROBOT_STATS_QUERY_KEY, user?.id, days],
    queryFn: async () => {
      const response = await apiRequest<unknown>(`/robot/stats?days=${days}`);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return normalizeStats(response.data);
    },
    enabled: Boolean(user?.id),
    refetchInterval: 45_000,
    retry: 1,
    staleTime: 30_000,
  });
}

export function getEmptyRobotStats(): RobotStats {
  return EMPTY_STATS;
}

function normalizeHistory(input: unknown): RobotHistoryItem[] {
  const root = record(input);
  const payload = record(root.data ?? root.result ?? root.payload ?? input);
  const items = Array.isArray(input)
    ? input
    : ([payload.items, payload.history, payload.trades, payload.operations, payload.records, payload.results].find(
        Array.isArray,
      ) ?? []);
  return (items as unknown[])
    .map(normalizeHistoryItem)
    .filter((item): item is RobotHistoryItem => item !== null);
}

/** Chaves internas que nao vao para a tela do cliente. */
const CHAVES_INTERNAS = new Set(["LIVE_DEMO"]);

function chaveVisivel(chave: string | null): string | null {
  return chave && CHAVES_INTERNAS.has(chave.toUpperCase()) ? null : chave;
}

function normalizeHistoryItem(input: unknown): RobotHistoryItem | null {
  const value = record(input);
  const active = text(value.active ?? value.symbol);
  const direction = text(value.direction ?? value.signal).toUpperCase();
  const resultValue = text(value.result ?? value.cycle_result ?? value.outcome ?? value.status).toUpperCase();
  // O empate devolve a entrada: nao e vitoria nem derrota. Antes ele caia no
  // `null` abaixo e a operacao era DESCARTADA aqui — some do Historico mesmo
  // com o backend gravando. Ver `dashboardDailyStats`, que tambem nao pode
  // conta-lo como derrota.
  const result = ["WIN", "GALE_WIN", "WON"].includes(resultValue)
    ? "WIN"
    : ["LOSS", "GALE_LOSS", "LOST"].includes(resultValue)
      ? "LOSS"
      : ["DRAW", "EQUAL", "TIE"].includes(resultValue)
        ? "DRAW"
        : null;
  if (!active || (direction !== "CALL" && direction !== "PUT") || !result) return null;
  const galeStep = numeric(value.gale_step ?? value.martingale_step);
  const cycle = text(value.cycle_result).toUpperCase();
  const badge =
    cycle === "GALE_WIN"
      ? "GALE WIN"
      : cycle === "GALE_LOSS"
        ? "GALE LOSS"
        : galeStep != null && galeStep >= 1
          ? "GALE 1"
          : "NORMAL";
  const analysis = record(value.analysis_json ?? value.analysisJson ?? value.analysis);
  return {
    id: text(value.id) || `${active}:${text(value.finished_at)}`,
    createdAt: optionalText(value.created_at),
    accountMode: "REAL",
    active,
    direction: direction as "CALL" | "PUT",
    amount: numeric(value.amount ?? value.entry_value) ?? 0,
    confidence: percentage(value.confidence),
    payout: percentage(value.payout),
    orderId: optionalText(value.order_id),
    result,
    badge,
    isGale: galeStep != null,
    galeStep,
    profit: numeric(value.profit ?? value.pnl) ?? 0,
    openedAt: optionalText(value.opened_at ?? value.sent_at),
    finishedAt: optionalText(value.finished_at ?? value.closed_at),
    timeframe: optionalText(value.timeframe ?? value.expiration),
    strategyName: optionalText(
      value.strategy_name ?? value.strategyName ?? analysis.strategy_name ?? analysis.strategyName,
    ),
    // A chave e mostrada crua no Historico (badge sob o nome da estrategia).
    // `LIVE_DEMO` aparecia ali com todas as letras e entregava, na tela do
    // cliente, o que a narracao acabou de deixar de dizer. A marca continua
    // gravada no banco e visivel no admin — some so desta view.
    strategyKey: chaveVisivel(
      optionalText(
        value.strategy_key ?? value.strategyKey ?? analysis.strategy_key ?? analysis.strategyKey,
      ),
    ),
    strategySummary: optionalText(
      value.strategy_summary ??
        value.strategySummary ??
        analysis.strategy_summary ??
        analysis.strategySummary,
    ),
    analysisDetail: optionalText(
      value.analysis_detail ??
        value.analysisDetail ??
        analysis.analysis_detail ??
        analysis.analysisDetail ??
        value.entry_reason ??
        value.entryReason ??
        analysis.entry_reason ??
        analysis.strategy_reason,
    ),
    speechPreview: optionalText(
      value.speech_preview ??
        value.speechPreview ??
        analysis.speech_preview ??
        analysis.speechPreview,
    ),
  };
}

function normalizeStats(input: unknown): RobotStats {
  const root = record(input);
  const value = record(root.stats ?? root.data ?? root.result ?? root.summary ?? input);
  return {
    wins: nonNegative(value.wins ?? value.won),
    losses: nonNegative(value.losses),
    totalTrades: nonNegative(value.total_trades ?? value.totalTrades ?? value.total),
    winRate: percentage(value.win_rate ?? value.winRate ?? value.assertiveness) ?? 0,
    profit: numeric(value.profit ?? value.pnl) ?? 0,
    profitFactor: numeric(value.profit_factor ?? value.profitFactor),
    currentWinStreak: nonNegative(value.current_win_streak ?? value.currentWinStreak),
    currentLossStreak: nonNegative(value.current_loss_streak ?? value.currentLossStreak),
    bestWinStreak: nonNegative(value.best_win_streak ?? value.bestWinStreak),
    bestLossStreak: nonNegative(value.best_loss_streak ?? value.bestLossStreak),
  };
}

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" ? (value as Record<string, unknown>) : {};
const text = (value: unknown): string =>
  typeof value === "string"
    ? value.trim()
    : typeof value === "number" && Number.isFinite(value)
      ? String(value)
      : "";
const optionalText = (value: unknown): string | null => text(value) || null;
const numeric = (value: unknown): number | null => {
  const parsed =
    typeof value === "number"
      ? value
      : typeof value === "string"
        ? Number(value.replace(",", "."))
        : Number.NaN;
  return Number.isFinite(parsed) ? parsed : null;
};
const percentage = (value: unknown): number | null => {
  const parsed = numeric(value);
  return parsed == null ? null : parsed >= 0 && parsed <= 1 ? parsed * 100 : parsed;
};
const nonNegative = (value: unknown): number => Math.max(0, Math.trunc(numeric(value) ?? 0));
