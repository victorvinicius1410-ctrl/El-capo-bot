import type { RobotHistoryItem } from "@/hooks/useRobotHistory";

export interface DashboardDailyRow {
  dateKey: string;
  dateLabel: string;
  result: number;
  wins: number;
  losses: number;
  currency: string;
  mode: string;
}

/** Agrupa operações por dia local para a tabela do dashboard. */
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
    const dateKey = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
    const bucket = buckets.get(dateKey) ?? { result: 0, wins: 0, losses: 0 };
    bucket.result += item.profit;
    if (item.result === "WIN") bucket.wins += 1;
    else bucket.losses += 1;
    buckets.set(dateKey, bucket);
  }
  return [...buckets.entries()].map(([dateKey, bucket]) => {
    const [year, month, day] = dateKey.split("-").map(Number);
    const itemMode = items.find((item) => {
      const stamp = item.finishedAt || item.openedAt || item.createdAt;
      return stamp && new Date(stamp).toLocaleDateString("en-CA") === dateKey;
    })?.accountMode;
    return {
      dateKey,
      dateLabel: new Date(year, month - 1, day).toLocaleDateString("pt-BR"),
      ...bucket,
      currency: currency.trim() || "-",
      mode: itemMode ?? (mode.trim() || "-"),
    };
  }).sort((left, right) => right.dateKey.localeCompare(left.dateKey));
}
