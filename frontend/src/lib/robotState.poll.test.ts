import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  getStoppedRobotState,
  ROBOT_STATE_FAST_POLL_MS,
  ROBOT_STATE_SLOW_POLL_MS,
  ROBOT_STATE_WS_RECONCILE_POLL_MS,
  registerRobotStateFailure,
  resetRobotStateBackoff,
  robotStateRefetchInterval,
  setRobotStateWsLive,
} from "./robotState.ts";

describe("robotStateRefetchInterval", () => {
  it("usa poll de fallback 30s quando WS está offline", () => {
    setRobotStateWsLive(false);
    const state = getStoppedRobotState();
    assert.equal(robotStateRefetchInterval(state), ROBOT_STATE_SLOW_POLL_MS);
    assert.equal(ROBOT_STATE_FAST_POLL_MS, 30_000);
  });

  it("com WS live o HTTP vira reconciliação — nunca desligado", () => {
    // Pausar de vez deixava o painel refém de um canal só: socket zumbi
    // (OPEN e mudo) congelava o placar em 0-0 até o F5.
    setRobotStateWsLive(true);
    const state = {
      ...getStoppedRobotState(),
      operation_in_progress: true,
      status: "PENDING_RESULT",
    };
    assert.equal(robotStateRefetchInterval(state), ROBOT_STATE_WS_RECONCILE_POLL_MS);
    assert.ok(ROBOT_STATE_WS_RECONCILE_POLL_MS > ROBOT_STATE_SLOW_POLL_MS);
    setRobotStateWsLive(false);
  });

  it("erro recente manda no intervalo mesmo com WS live", () => {
    setRobotStateWsLive(true);
    const now = 1_000_000;
    const wait = registerRobotStateFailure("u-backoff", now);
    // Backoff do usuário tem precedência: o poll volta antes dos 60s.
    assert.equal(robotStateRefetchInterval(undefined, "u-backoff", now), wait);
    resetRobotStateBackoff("u-backoff");
    assert.equal(
      robotStateRefetchInterval(undefined, "u-backoff", now),
      ROBOT_STATE_WS_RECONCILE_POLL_MS,
    );
    setRobotStateWsLive(false);
  });

  it("fallback nunca abaixo de 30s", () => {
    setRobotStateWsLive(false);
    assert.ok(ROBOT_STATE_FAST_POLL_MS >= 30_000);
    assert.ok(ROBOT_STATE_SLOW_POLL_MS >= ROBOT_STATE_FAST_POLL_MS);
  });
});
