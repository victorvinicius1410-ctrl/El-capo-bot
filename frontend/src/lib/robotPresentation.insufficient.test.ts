import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

describe("aviso de saldo insuficiente no overlay", () => {
  it("detecta Insufficient funds e prioriza status INSUFFICIENT_BALANCE", () => {
    const presentation = readFileSync(join(here, "robotPresentation.ts"), "utf8");
    assert.match(presentation, /export function looksLikeInsufficientBalance/);
    assert.match(presentation, /funds for this transaction/);
    assert.match(presentation, /status === "INSUFFICIENT_BALANCE" \|\| looksLikeInsufficientBalance/);
    assert.match(presentation, /presentation\("stopped", "Saldo insuficiente"/);
  });

  it("api traduz INSUFFICIENT_BALANCE e INSUFFICIENT_FUNDS", () => {
    const api = readFileSync(join(here, "api.ts"), "utf8");
    assert.match(api, /INSUFFICIENT_BALANCE:/);
    assert.match(api, /INSUFFICIENT_FUNDS:/);
    assert.match(api, /reduza o valor da entrada/);
  });

  it("ScoreBadge e ProfitBadge tem fundo visivel no overlay", () => {
    const overlay = readFileSync(join(here, "../components/RobotOverlay.tsx"), "utf8");
    assert.match(overlay, /function ScoreBadge/);
    assert.match(overlay, /rounded-xl border/);
    assert.match(overlay, /bg-\[#041218\]\/90/);
    assert.match(overlay, /function ProfitBadge/);
    assert.match(overlay, /bg-\[#0a1216\]\/85/);
  });
});
