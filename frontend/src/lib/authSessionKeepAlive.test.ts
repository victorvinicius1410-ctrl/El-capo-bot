import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  canRevalidateAuthNow,
  shouldRevalidateAuthOnVisibilityChange,
  withAuthRefreshRetry,
} from "./authSessionKeepAlive.ts";

describe("shouldRevalidateAuthOnVisibilityChange", () => {
  it("revalida só quando a aba volta a ficar visível", () => {
    assert.equal(shouldRevalidateAuthOnVisibilityChange(true, true), true);
    assert.equal(shouldRevalidateAuthOnVisibilityChange(true, false), false);
    assert.equal(shouldRevalidateAuthOnVisibilityChange(false, true), false);
    assert.equal(shouldRevalidateAuthOnVisibilityChange(false, false), false);
  });
});

describe("canRevalidateAuthNow", () => {
  it("respeita o intervalo mínimo entre revalidações", () => {
    assert.equal(canRevalidateAuthNow(1000, 0, 120_000), true);
    assert.equal(canRevalidateAuthNow(20_000, 1000, 120_000), false);
    assert.equal(canRevalidateAuthNow(121_000, 1000, 120_000), true);
  });

  it("usa 120s como intervalo padrão (reduz /auth/session sob carga)", async () => {
    const { AUTH_REVALIDATE_MIN_INTERVAL_MS } = await import("./authSessionKeepAlive.ts");
    assert.equal(AUTH_REVALIDATE_MIN_INTERVAL_MS, 120_000);
  });
});

describe("withAuthRefreshRetry", () => {
  it("retorna o primeiro resultado quando a sessão ainda é válida", async () => {
    let refreshCalls = 0;
    const result = await withAuthRefreshRetry(
      async () => ({ userId: "u1" }),
      async () => {
        refreshCalls += 1;
        return true;
      },
    );
    assert.deepEqual(result, { userId: "u1" });
    assert.equal(refreshCalls, 0);
  });

  it("renova e tenta de novo quando a primeira leitura falha", async () => {
    let attempts = 0;
    let refreshCalls = 0;
    const result = await withAuthRefreshRetry(
      async () => {
        attempts += 1;
        return attempts === 1 ? null : { userId: "u2" };
      },
      async () => {
        refreshCalls += 1;
        return true;
      },
    );
    assert.deepEqual(result, { userId: "u2" });
    assert.equal(attempts, 2);
    assert.equal(refreshCalls, 1);
  });

  it("não tenta de novo se o refresh falhar", async () => {
    let attempts = 0;
    const result = await withAuthRefreshRetry(
      async () => {
        attempts += 1;
        return null;
      },
      async () => false,
    );
    assert.equal(result, null);
    assert.equal(attempts, 1);
  });
});
