/**
 * Helpers da conta marketing: detecção de modo e normalização do painel Shift+O.
 *
 * O visual/histórico do painel usa os mesmos endpoints `/robot/*` do cliente.
 * O histórico sintético existe só para o painel oculto (Shift+O).
 */

import type { MarketingSimulationStats, MarketingSimulationTrade } from "@/lib/api";
import type { RobotHistoryItem, RobotStats } from "@/hooks/useRobotHistory";

export const MARKETING_HISTORY_QUERY_KEY = ["marketing-simulation", "history"] as const;
export const MARKETING_STATS_QUERY_KEY = ["marketing-simulation", "stats"] as const;

export function isMarketingSimulationAccount(access: {
  account_type?: string | null;
  marketing_mode?: string | null;
} | null | undefined): boolean {
  return access?.account_type === "marketing" && access?.marketing_mode === "simulation";
}

/**
 * Converte trades do painel Shift+O para o formato interno de listagem.
 */
export function normalizeMarketingHistory(
  trades: MarketingSimulationTrade[],
  days?: number,
): RobotHistoryItem[] {
  const cutoff = days && days > 0 ? Date.now() - days * 24 * 60 * 60 * 1000 : null;
  const items: RobotHistoryItem[] = [];
  for (const trade of trades) {
    const createdAt = trade.created_at ?? null;
    if (cutoff && createdAt) {
      const ts = new Date(createdAt).getTime();
      if (Number.isFinite(ts) && ts < cutoff) continue;
    }
    const direction = String(trade.direction || "").toUpperCase();
    const result = String(trade.result || "").toUpperCase();
    if ((direction !== "CALL" && direction !== "PUT") || (result !== "WIN" && result !== "LOSS")) {
      continue;
    }
    items.push({
      id: String(trade.id),
      createdAt,
      accountMode: "REAL",
      active: String(trade.asset || "EURUSD-OTC"),
      direction: direction as "CALL" | "PUT",
      amount: Number(trade.amount) || 0,
      confidence: null,
      payout: trade.payout == null ? null : Number(trade.payout),
      orderId: String(trade.id),
      result: result as "WIN" | "LOSS",
      badge: "NORMAL",
      isGale: false,
      galeStep: null,
      profit: Number(trade.profit) || 0,
      openedAt: createdAt,
      finishedAt: createdAt,
      timeframe: null,
    });
  }
  return items.sort((a, b) => String(b.finishedAt ?? "").localeCompare(String(a.finishedAt ?? "")));
}

/**
 * Converte o placar da API do Shift+O no formato do hook de estatísticas.
 */
export function normalizeMarketingStats(stats: MarketingSimulationStats | null | undefined): RobotStats {
  return {
    wins: Math.max(0, Math.trunc(stats?.wins ?? 0)),
    losses: Math.max(0, Math.trunc(stats?.losses ?? 0)),
    totalTrades: Math.max(0, Math.trunc(stats?.total_trades ?? 0)),
    winRate: Number(stats?.win_rate ?? 0) || 0,
    profit: Number(stats?.profit ?? 0) || 0,
    profitFactor: null,
    currentWinStreak: 0,
    currentLossStreak: 0,
    bestWinStreak: 0,
    bestLossStreak: 0,
  };
}
