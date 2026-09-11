import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  formatTrialRemaining,
  remainingDaysFromExpiresAt,
  remainingMs,
  trialDaysFromExpiresAt,
} from "./trial.ts";

describe("trial helpers", () => {
  const now = Date.parse("2026-08-26T12:00:00.000Z");

  it("calcula dias restantes a partir de expires_at", () => {
    const expiresAt = "2026-08-29T12:00:00.000Z";
    assert.equal(remainingDaysFromExpiresAt(expiresAt, now), 3);
    assert.equal(trialDaysFromExpiresAt(expiresAt, "7", now), "3");
  });

  it("não infla 3 dias para 4 por skew de poucos segundos", () => {
    // Simula browser ~5s atrás do servidor após criar trial de 3 dias.
    const expiresAt = "2026-08-29T12:00:05.000Z";
    assert.equal(remainingDaysFromExpiresAt(expiresAt, now), 3);
    assert.equal(trialDaysFromExpiresAt(expiresAt, "7", now), "3");
  });

  it("formata trial com dias e relógio", () => {
    const ms = remainingMs("2026-09-25T12:00:00.000Z", now);
    assert.equal(formatTrialRemaining(ms), "30 dias, 00:00:00");
  });

  it("formata menos de um dia só com relógio", () => {
    assert.equal(formatTrialRemaining(5 * 3_600_000), "05:00:00");
  });
});
