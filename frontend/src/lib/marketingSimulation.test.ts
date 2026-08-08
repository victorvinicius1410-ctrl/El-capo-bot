import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  isMarketingSimulationAccount,
  normalizeMarketingHistory,
  normalizeMarketingStats,
} from "./marketingSimulation.ts";

describe("marketingSimulation", () => {
  it("identifica apenas conta marketing em simulação", () => {
    assert.equal(
      isMarketingSimulationAccount({ account_type: "marketing", marketing_mode: "simulation" }),
      true,
    );
    assert.equal(
      isMarketingSimulationAccount({ account_type: "client", marketing_mode: "simulation" }),
      false,
    );
  });

  it("normaliza histórico sintético para o painel Shift+O", () => {
    const items = normalizeMarketingHistory([
      {
        id: "t1",
        result: "WIN",
        asset: "EURUSD-OTC",
        direction: "CALL",
        amount: 10,
        payout: 80,
        profit: 8,
        created_at: "2026-07-21T12:00:00.000Z",
      },
    ]);
    assert.equal(items.length, 1);
    assert.equal(items[0]?.result, "WIN");
    assert.equal(items[0]?.active, "EURUSD-OTC");
    assert.equal(items[0]?.accountMode, "REAL");
    assert.equal(items[0]?.orderId, "t1");
  });

  it("filtra histórico por dias", () => {
    const old = normalizeMarketingHistory(
      [
        {
          id: "old",
          result: "WIN",
          asset: "EURUSD-OTC",
          direction: "CALL",
          amount: 10,
          payout: 80,
          profit: 8,
          created_at: "2020-01-01T00:00:00.000Z",
        },
      ],
      1,
    );
    assert.equal(old.length, 0);
  });

  it("normaliza placar do painel Shift+O", () => {
    const stats = normalizeMarketingStats({
      wins: 3,
      losses: 1,
      total_trades: 4,
      win_rate: 75,
      profit: 12.5,
    });
    assert.equal(stats.wins, 3);
    assert.equal(stats.losses, 1);
    assert.equal(stats.winRate, 75);
  });
});
