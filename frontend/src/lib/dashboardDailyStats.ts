import type { RobotHistoryItem, RobotStats } from "@/hooks/useRobotHistory";
import { getEmptyRobotStats } from "@/hooks/useRobotHistory";
import { brasiliaDateKey, formatBrasiliaDate } from "@/lib/brasiliaTime";
import { isStampInRange } from "@/lib/dateRange";

export interface DashboardDailyRow {
  dateKey: string;
  dateLabel: string;
  result: number;
  wins: number;
  losses: number;
  currency: string;
  mode: string;
}

/** Agrupa operações por dia civil de Brasília para a tabela do dashboard. */
export function aggregateDashboardDailyRows(
  items: RobotHistoryItem[],
  currency: string,
  mode: string,
): DashboardDailyRow[] {
  const buckets = new Map<string, { result: number; wins: number; losses: number }>();
  for (const item of items) {
    const stamp = item.finishedAt || item.openedAt || item.createdAt;
    if (!stamp) continue;
    const date = new Date(stamp);
    if (Number.isNaN(date.getTime())) continue;
    const dateKey = brasiliaDateKey(date);
    const bucket = buckets.get(dateKey) ?? { result: 0, wins: 0, losses: 0 };
    bucket.result += item.profit;
    // Empate nao entra em vitorias nem em derrotas — o `else` cru contava todo
    // nao-WIN como derrota. O backend (`build_robot_stats`) ja filtra para
    // {WIN, LOSS}; aqui precisa fazer igual, senao o painel diverge da API.
    if (item.result === "WIN") bucket.wins += 1;
    else if (item.result === "LOSS") bucket.losses += 1;
    buckets.set(dateKey, bucket);
  }
  return [...buckets.entries()].map(([dateKey, bucket]) => {
    const itemMode = items.find((item) => {
      const stamp = item.finishedAt || item.openedAt || item.createdAt;
      return stamp && brasiliaDateKey(new Date(stamp)) === dateKey;
    })?.accountMode;
    return {
      dateKey,
      dateLabel: formatBrasiliaDate(`${dateKey}T12:00:00-03:00`),
      ...bucket,
      currency: currency.trim() || "-",
      mode: itemMode ?? (mode.trim() || "-"),
    };
  }).sort((left, right) => right.dateKey.localeCompare(left.dateKey));
}

/** Mantém só operações cujo horário cai no intervalo civil de Brasília. */
export function filterHistoryByRange(
  items: RobotHistoryItem[],
  start: Date,
  end: Date,
): RobotHistoryItem[] {
  return items.filter((item) =>
    isStampInRange(item.finishedAt || item.openedAt || item.createdAt, start, end),
  );
}

/** Recalcula indicadores a partir da lista já filtrada. */
export function computeRobotStatsFromItems(items: RobotHistoryItem[]): RobotStats {
  let wins = 0;
  let losses = 0;
  let profit = 0;
  let grossWins = 0;
  let grossLosses = 0;
  let currentWinStreak = 0;
  let currentLossStreak = 0;
  let bestWinStreak = 0;
  let bestLossStreak = 0;
  let streakKind: "WIN" | "LOSS" | null = null;
  let streak = 0;
  for (const item of items) {
    profit += item.profit;
    if (item.result === "WIN") {
      wins += 1;
      if (item.profit > 0) grossWins += item.profit;
      if (streakKind === "WIN") streak += 1;
      else {
        streakKind = "WIN";
        streak = 1;
      }
      currentWinStreak = streak;
      currentLossStreak = 0;
      bestWinStreak = Math.max(bestWinStreak, streak);
    } else if (item.result === "DRAW") {
      // Empate nao conta e nao quebra sequencia, igual ao backend, que o remove
      // da lista antes de calcular as sequencias.
      continue;
    } else {
      losses += 1;
      if (item.profit < 0) grossLosses += Math.abs(item.profit);
      if (streakKind === "LOSS") streak += 1;
      else {
        streakKind = "LOSS";
        streak = 1;
      }
      currentLossStreak = streak;
      currentWinStreak = 0;
      bestLossStreak = Math.max(bestLossStreak, streak);
    }
  }
  const totalTrades = wins + losses;
  return {
    ...getEmptyRobotStats(),
    wins,
    losses,
    totalTrades,
    winRate: totalTrades ? (wins / totalTrades) * 100 : 0,
    profit,
    profitFactor: grossLosses > 0 ? grossWins / grossLosses : null,
    currentWinStreak,
    currentLossStreak,
    bestWinStreak,
    bestLossStreak,
  };
}
