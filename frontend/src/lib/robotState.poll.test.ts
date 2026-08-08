import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  getStoppedRobotState,
  ROBOT_STATE_FAST_POLL_MS,
  ROBOT_STATE_SLOW_POLL_MS,
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

  it("pausa o poll HTTP quando o WebSocket está live", () => {
    setRobotStateWsLive(true);
    const state = {
      ...getStoppedRobotState(),
      operation_in_progress: true,
      status: "PENDING_RESULT",
    };
    assert.equal(robotStateRefetchInterval(state), false);
    setRobotStateWsLive(false);
  });

  it("fallback nunca abaixo de 30s", () => {
    setRobotStateWsLive(false);
    assert.ok(ROBOT_STATE_FAST_POLL_MS >= 30_000);
    assert.ok(ROBOT_STATE_SLOW_POLL_MS >= ROBOT_STATE_FAST_POLL_MS);
  });
});
