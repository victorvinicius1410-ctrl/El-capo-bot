import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  ROBOT_WS_FALLBACK_POLL_MS,
  ROBOT_WS_PING_INTERVAL_MS,
  ROBOT_WS_STALE_AFTER_MS,
  robotStateWsIsStale,
  robotStateWsUrl,
} from "./robotStateWsUrl.ts";
import {
  ROBOT_STATE_FAST_POLL_MS,
  ROBOT_STATE_WS_RECONCILE_POLL_MS,
  robotStateRefetchInterval,
  setRobotStateWsLive,
} from "./robotState.ts";

describe("robotStateWs", () => {
  it("monta wss a partir da base https", () => {
    const url = robotStateWsUrl("ticket-abc", "https://api.elcapobot.online");
    assert.equal(url, "wss://api.elcapobot.online/ws/robot-state?ticket=ticket-abc");
  });

  it("fallback HTTP é 30s e vira reconciliação quando WS está live", () => {
    assert.equal(ROBOT_STATE_FAST_POLL_MS, ROBOT_WS_FALLBACK_POLL_MS);
    setRobotStateWsLive(true);
    assert.equal(robotStateRefetchInterval(undefined, "u1"), ROBOT_STATE_WS_RECONCILE_POLL_MS);
    setRobotStateWsLive(false);
    assert.equal(robotStateRefetchInterval(undefined, "u1"), 30_000);
  });

  it("socket calado além de 2 pings é zumbi", () => {
    const opened = 1_000_000;
    // O servidor responde `pong` a cada ping: 45s de silêncio não é normal.
    assert.equal(robotStateWsIsStale(opened, opened + ROBOT_WS_PING_INTERVAL_MS), false);
    assert.equal(robotStateWsIsStale(opened, opened + ROBOT_WS_STALE_AFTER_MS), false);
    assert.equal(robotStateWsIsStale(opened, opened + ROBOT_WS_STALE_AFTER_MS + 1), true);
  });

  it("sem nenhuma mensagem ainda, não derruba o socket", () => {
    // `lastMessageAt = 0` é o estado antes do open: nada a julgar.
    assert.equal(robotStateWsIsStale(0, 5_000_000), false);
  });
});
