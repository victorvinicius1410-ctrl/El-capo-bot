import assert from "node:assert/strict";
import { describe, it, beforeEach } from "node:test";
import {
  clearResultFlashMemory,
  getRobotStatusPresentation,
  RESULT_OVERLAY_DISPLAY_MS,
  shouldShowResult,
} from "./robotPresentation.ts";
import { getStoppedRobotState, type RobotState, type RobotTrade } from "./robotState.ts";

const FINISHED = "2026-08-15T12:00:00.000Z";
const FINISHED_MS = Date.parse(FINISHED);

function closedTrade(overrides: Partial<RobotTrade> = {}): RobotTrade {
  return {
    active: "EURUSD-OTC",
    direction: "CALL",
    amount: 50,
    order_id: "ord-1",
    confidence: 80,
    payout: 80,
    strategy_score: 80,
    strategy_name: "Price Action",
    used_strategies: [],
    strategy_reason: null,
    entry_reason: null,
    result: "WIN",
    expires_at: null,
    sent_at: FINISHED,
    finished_at: FINISHED,
    profit: 40,
    gale_step: 0,
    is_gale: false,
    account_mode: "REAL",
    ...overrides,
  };
}

function analyzingAfterWin(overrides: Partial<RobotState> = {}): RobotState {
  return {
    ...getStoppedRobotState(),
    enabled: true,
    worker_running: true,
    connected: true,
    status: "WAITING_NEXT_CYCLE",
    cycle_result: null,
    last_trade: closedTrade(),
    display_countdown_label: "Buscando melhor oportunidade",
    ...overrides,
  };
}

describe("flash de WIN/LOSS no overlay", () => {
  beforeEach(() => {
    clearResultFlashMemory();
  });

  it("mostra WIN + ativo e busca oportunidade nos primeiros 60s", () => {
    const state = analyzingAfterWin();
    const now = FINISHED_MS + 20_000;
    assert.equal(shouldShowResult(state, now), true);
    const presentation = getRobotStatusPresentation(state, now);
    assert.equal(presentation.kind, "result");
    assert.equal(presentation.result, "WIN");
    assert.equal(presentation.trade?.active, "EURUSD-OTC");
    assert.equal(presentation.footer, "Buscando melhor oportunidade");
  });

  it("some WIN, LOSS e ativo depois de 1 minuto", () => {
    const state = analyzingAfterWin();
    const now = FINISHED_MS + RESULT_OVERLAY_DISPLAY_MS + 1_000;
    assert.equal(shouldShowResult(state, now), false);
    const presentation = getRobotStatusPresentation(state, now);
    assert.equal(presentation.kind, "analyzing");
    assert.equal(presentation.result, null);
    assert.equal(presentation.trade, null);
    assert.match(presentation.detail ?? "", /oportunidade/i);
  });

  it("não prende o overlay quando unseen_result fica true sem prazo", () => {
    const state = analyzingAfterWin({
      unseen_result: true,
      status: "WIN",
      cycle_result: "WIN",
      last_trade: closedTrade({ finished_at: FINISHED }),
    });
    assert.equal(shouldShowResult(state, FINISHED_MS + RESULT_OVERLAY_DISPLAY_MS + 5_000), false);
  });

  it("não mostra resultado antigo quando já há novo sinal", () => {
    const state = analyzingAfterWin({
      status: "WAITING_ENTRY",
      pending_signal: {
        symbol: "GBPUSD-OTC",
        direction: "PUT",
        confidence: 90,
        strategy_score: 90,
        strategy_name: "Price Action",
        strategy_key: null,
        strategy_summary: null,
        analysis_detail: null,
        speech_preview: null,
        used_strategies: [],
        strategy_reason: null,
        payout: 80,
        reason: null,
        created_at: null,
        ai_approved: null,
        ai_confidence: null,
        ai_risk: null,
        ai_candle_reading: null,
        ai_entry_reason: null,
        ai_voice_text: null,
        ai_block_reason: null,
        ai_error: null,
      },
    });
    assert.equal(shouldShowResult(state, FINISHED_MS + 10_000), false);
  });
});
