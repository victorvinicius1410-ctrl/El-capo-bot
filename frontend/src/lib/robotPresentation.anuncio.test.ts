import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { getRobotStatusPresentation, clearRejectionMemory } from "./robotPresentation.ts";
import { getStoppedRobotState, type RobotState } from "./robotState.ts";

const here = new URL(".", import.meta.url).pathname;

/**
 * 11/09/2026: o painel anunciava "melhor ativo encontrado" com direção e
 * contagem, e a conferência de suporte/resistência e pavio — que só roda com a
 * vela fechada, na virada — cancelava 65% dessas entradas. O cliente ouvia
 * "entrada rejeitada" sem nunca ter havido ordem.
 */
function estado(extra: Partial<RobotState>): RobotState {
  return { ...getStoppedRobotState(), enabled: true, connected: true, ...extra } as RobotState;
}

const sinal = {
  symbol: "EURUSD-OTC",
  direction: "CALL",
  confidence: 95,
  payout: 87,
  created_at: "2026-09-11T21:00:00Z",
} as unknown as NonNullable<RobotState["pending_signal"]>;

describe("entrada não é anunciada antes da confirmação", () => {
  for (const status of ["SIGNAL_FOUND", "WAITING_ENTRY_WINDOW", "WAITING_ENTRY", "WAITING_NEXT_CANDLE_ENTRY"]) {
    it(`${status} mostra análise, sem ativo nem direção`, () => {
      clearRejectionMemory();
      const p = getRobotStatusPresentation(estado({ status, pending_signal: sinal }), Date.now());
      assert.equal(p.kind, "analyzing");
      assert.equal(p.direction, null);
      assert.equal(p.signal, null);
      assert.doesNotMatch(`${p.title} ${p.detail ?? ""}`, /EURUSD|CALL|compra/i);
    });
  }

  it("a fala da entrada só existe no momento da ordem", () => {
    const narracao = readFileSync(join(here, "robotNarration.ts"), "utf8");
    assert.doesNotMatch(narracao, /Melhor ativo encontrado/);
    assert.doesNotMatch(narracao, /Entrada preparada/);
    // A explicação da análise passou para o evento da operação aberta.
    assert.match(narracao, /Operação aberta em \$\{speakSymbol\(active\)\}\.\$\{directionPart\}\$\{analise\}/);
  });
});
