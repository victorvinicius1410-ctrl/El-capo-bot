import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { getStoppedRobotState, liveDisplayCountdownSeconds, type RobotState } from "./robotState.ts";

function withCountdown(
  overrides: Partial<RobotState> & {
    display_countdown_seconds: number;
    fetched_at: number;
  },
): RobotState {
  return {
    ...getStoppedRobotState(),
    enabled: true,
    worker_running: true,
    status: "WAITING_NEXT_CYCLE",
    display_countdown_label: "Próxima análise em",
    ...overrides,
  };
}

describe("liveDisplayCountdownSeconds", () => {
  it("decai a cada segundo com base em fetched_at quando não há next_cycle_at", () => {
    const fetchedAt = Date.parse("2026-07-21T15:00:00.000Z");
    const state = withCountdown({
      display_countdown_seconds: 120,
      fetched_at: fetchedAt,
      next_cycle_at: null,
    });

    assert.equal(liveDisplayCountdownSeconds(state, fetchedAt), 120);
    assert.equal(liveDisplayCountdownSeconds(state, fetchedAt + 3_000), 117);
    assert.equal(liveDisplayCountdownSeconds(state, fetchedAt + 120_000), 0);
    assert.equal(liveDisplayCountdownSeconds(state, fetchedAt + 130_000), 0);
  });

  it("prioriza next_cycle_at para manter o timer contínuo entre polls", () => {
    const fetchedAt = Date.parse("2026-07-21T15:00:00.000Z");
    const nextCycleAt = "2026-07-21T15:05:00.000Z";
    const state = withCountdown({
      display_countdown_seconds: 300,
      fetched_at: fetchedAt,
      next_cycle_at: nextCycleAt,
    });

    assert.equal(liveDisplayCountdownSeconds(state, fetchedAt), 300);
    assert.equal(liveDisplayCountdownSeconds(state, fetchedAt + 45_000), 255);
    assert.equal(liveDisplayCountdownSeconds(state, Date.parse(nextCycleAt)), 0);
  });

  it("não trava em valores intermediários quando o poll demora vários segundos", () => {
    const fetchedAt = Date.parse("2026-07-21T15:00:00.000Z");
    const state = withCountdown({
      display_countdown_seconds: 90,
      fetched_at: fetchedAt,
      next_cycle_at: "2026-07-21T15:01:30.000Z",
    });

    const ticks = [0, 1, 2, 3, 4, 5].map((second) =>
      liveDisplayCountdownSeconds(state, fetchedAt + second * 1_000),
    );
    assert.deepEqual(ticks, [90, 89, 88, 87, 86, 85]);
  });

  it("oculta o timer quando o label é Buscando melhor oportunidade", () => {
    const fetchedAt = Date.parse("2026-07-21T15:00:00.000Z");
    const state = withCountdown({
      display_countdown_label: "Buscando melhor oportunidade",
      display_countdown_seconds: 120,
      fetched_at: fetchedAt,
      next_cycle_at: "2026-07-21T15:02:00.000Z",
    });

    assert.equal(liveDisplayCountdownSeconds(state, fetchedAt), 0);
    assert.equal(liveDisplayCountdownSeconds(state, fetchedAt + 30_000), 0);
  });
});
