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
      timeframe: trade.timeframe ?? trade.period ?? null,
      strategyName: trade.strategy_name ?? null,
      strategyKey: trade.strategy_key ?? null,
      strategySummary: trade.strategy_summary ?? null,
      analysisDetail: trade.analysis_detail ?? null,
      speechPreview: trade.speech_preview ?? null,
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

/**
 * Calcula o placar visual (WIN / LOSS / lucro) de um lote do Shift+O.
 *
 * Usado para atualizar o overlay do El Capo na hora, sem esperar o Redis.
 *
 * @param trades - Operações recém-geradas ou a operação avulsa criada
 * @returns Contadores no formato do `robotState` do overlay
 */
export function overlayScoreFromTrades(
  trades: Array<{ result?: string; profit?: number }> | null | undefined,
): { wins: number; losses: number; profit: number } {
  let wins = 0;
  let losses = 0;
  let profit = 0;
  for (const trade of trades ?? []) {
    const result = String(trade.result || "").toUpperCase();
    if (result === "WIN") wins += 1;
    else if (result === "LOSS") losses += 1;
    profit += Number(trade.profit) || 0;
  }
  return { wins, losses, profit: Math.round(profit * 100) / 100 };
}
