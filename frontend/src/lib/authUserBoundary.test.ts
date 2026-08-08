import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { planAuthIdentityTransition } from "./authUserBoundary.ts";

describe("planAuthIdentityTransition", () => {
  it("nao faz nada quando o id permanece igual", () => {
    assert.deepEqual(planAuthIdentityTransition("u1", "u1"), {
      shouldGateRender: false,
      shouldResetState: false,
      cancelInFlightQueries: false,
    });
    assert.deepEqual(planAuthIdentityTransition(null, null), {
      shouldGateRender: false,
      shouldResetState: false,
      cancelInFlightQueries: false,
    });
  });

  it("no primeiro bind limpa stores locais sem cancelQueries (evita CancelledError no /admin)", () => {
    assert.deepEqual(planAuthIdentityTransition(undefined, "u1"), {
      shouldGateRender: false,
      shouldResetState: true,
      cancelInFlightQueries: false,
    });
    assert.deepEqual(planAuthIdentityTransition(undefined, null), {
      shouldGateRender: false,
      shouldResetState: true,
      cancelInFlightQueries: false,
    });
  });

  it("no login e logout limpa stores locais sem cancelQueries", () => {
    assert.deepEqual(planAuthIdentityTransition(null, "u1"), {
      shouldGateRender: false,
      shouldResetState: true,
      cancelInFlightQueries: false,
    });
    assert.deepEqual(planAuthIdentityTransition("u1", null), {
      shouldGateRender: false,
      shouldResetState: true,
      cancelInFlightQueries: false,
    });
  });

  it("na troca entre dois usuarios concretos cancela queries e usa gate", () => {
    assert.deepEqual(planAuthIdentityTransition("u1", "u2"), {
      shouldGateRender: true,
      shouldResetState: true,
      cancelInFlightQueries: true,
    });
  });
});
