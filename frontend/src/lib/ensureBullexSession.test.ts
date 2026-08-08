import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  markManualBullexDisconnect,
  resetManualBullexDisconnectForTests,
} from "./manualBullexDisconnect.ts";
import {
  runAutoReconnectWithRetry,
  shouldAutoReconnectBullex,
} from "./ensureBullexSession.ts";

describe("shouldAutoReconnectBullex", () => {
  const base = {
    connected: false,
    credentialsSaved: true,
    alreadyAttempted: false,
    pendingConnect: false,
    stillLoading: false,
  };

  it("dispara quando desconectado com login salvo", () => {
    resetManualBullexDisconnectForTests();
    assert.equal(shouldAutoReconnectBullex(base), true);
  });

  it("não dispara se já conectado", () => {
    resetManualBullexDisconnectForTests();
    assert.equal(shouldAutoReconnectBullex({ ...base, connected: true }), false);
  });

  it("não dispara sem credenciais salvas", () => {
    resetManualBullexDisconnectForTests();
    assert.equal(shouldAutoReconnectBullex({ ...base, credentialsSaved: false }), false);
  });

  it("não dispara se já tentou nesta visita", () => {
    resetManualBullexDisconnectForTests();
    assert.equal(shouldAutoReconnectBullex({ ...base, alreadyAttempted: true }), false);
  });

  it("não dispara enquanto carrega ou há login pendente", () => {
    resetManualBullexDisconnectForTests();
    assert.equal(shouldAutoReconnectBullex({ ...base, stillLoading: true }), false);
    assert.equal(shouldAutoReconnectBullex({ ...base, pendingConnect: true }), false);
  });

  it("não dispara após Desconectar Bullex manual (60s)", () => {
    resetManualBullexDisconnectForTests();
    markManualBullexDisconnect();
    assert.equal(shouldAutoReconnectBullex(base), false);
    resetManualBullexDisconnectForTests();
  });
});

describe("runAutoReconnectWithRetry", () => {
  const noSleep = async () => {};

  it("retorna sucesso na 1ª tentativa sem esperar nada", async () => {
    let calls = 0;
    const result = await runAutoReconnectWithRetry(
      async () => {
        calls += 1;
        return { ok: true };
      },
      { sleep: noSleep },
    );
    assert.equal(result.ok, true);
    assert.equal(calls, 1);
  });

  it("tenta de novo com backoff quando a 1ª falha (regressão: antes desistia na 1ª falha)", async () => {
    let calls = 0;
    const delaysUsed: number[] = [];
    const result = await runAutoReconnectWithRetry(
      async () => {
        calls += 1;
        if (calls < 3) return { ok: false, error: "SESSION_NOT_FOUND" };
        return { ok: true };
      },
      {
        sleep: async (ms) => {
          delaysUsed.push(ms);
        },
        onRetryScheduled: (attempt, delayMs) => {
          assert.equal(delayMs, delaysUsed[attempt - 1] ?? delayMs);
        },
      },
    );
    assert.equal(result.ok, true);
    assert.equal(calls, 3);
    assert.deepEqual(delaysUsed, [5_000, 15_000]);
  });

  it("desiste só depois de esgotar as tentativas configuradas", async () => {
    let calls = 0;
    const result = await runAutoReconnectWithRetry(
      async () => {
        calls += 1;
        return { ok: false, error: "BULLEX_TEMPORARY_UNAVAILABLE" };
      },
      { sleep: noSleep, maxAttempts: 3, delaysMs: [1, 1] },
    );
    assert.equal(result.ok, false);
    assert.equal(result.error, "BULLEX_TEMPORARY_UNAVAILABLE");
    assert.equal(calls, 3);
  });

  it("para de tentar imediatamente quando cancelado (ex.: troca de aba/unmount)", async () => {
    let calls = 0;
    let cancelled = false;
    const result = await runAutoReconnectWithRetry(
      async () => {
        calls += 1;
        cancelled = true;
        return { ok: false, error: "SESSION_NOT_FOUND" };
      },
      { sleep: noSleep, isCancelled: () => cancelled },
    );
    assert.equal(result.ok, false);
    assert.equal(calls, 1);
  });
});
