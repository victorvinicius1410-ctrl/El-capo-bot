import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { ROBOT_WS_FALLBACK_POLL_MS, robotStateWsUrl } from "./robotStateWsUrl.ts";
import {
  ROBOT_STATE_FAST_POLL_MS,
  robotStateRefetchInterval,
  setRobotStateWsLive,
} from "./robotState.ts";

describe("robotStateWs", () => {
  it("monta wss a partir da base https", () => {
    const url = robotStateWsUrl("ticket-abc", "https://api.elcapobot.online");
    assert.equal(url, "wss://api.elcapobot.online/ws/robot-state?ticket=ticket-abc");
  });

  it("fallback HTTP é 30s e pausa quando WS está live", () => {
    assert.equal(ROBOT_STATE_FAST_POLL_MS, ROBOT_WS_FALLBACK_POLL_MS);
    setRobotStateWsLive(true);
    assert.equal(robotStateRefetchInterval(undefined, "u1"), false);
    setRobotStateWsLive(false);
    assert.equal(robotStateRefetchInterval(undefined, "u1"), 30_000);
  });
});
