import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  resetScoreConfirmLabel,
  resetScoreHighlight,
  shouldConfirmResetScore,
} from "./resetScoreConfirm.ts";

describe("resetScoreConfirm", () => {
  it("diz o que vai sumir, no plural certo", () => {
    assert.equal(resetScoreHighlight({ wins: 4, losses: 1, profit: 12 }), "4 WINs × 1 LOSS");
    assert.equal(resetScoreHighlight({ wins: 1, losses: 0, profit: 4.2 }), "1 WIN × 0 LOSSes");
  });

  it("não pergunta nada com o placar já zerado", () => {
    // Quem aperta com 0-0 está destravando o start depois de um stop batido:
    // uma confirmação aqui seria só atrito.
    assert.equal(resetScoreHighlight({ wins: 0, losses: 0, profit: 0 }), null);
    assert.equal(shouldConfirmResetScore({ wins: 0, losses: 0, profit: 0 }), false);
    assert.equal(shouldConfirmResetScore(null), false);
    assert.equal(shouldConfirmResetScore(undefined), false);
  });

  it("pergunta sempre que há placar do dia", () => {
    assert.equal(shouldConfirmResetScore({ wins: 0, losses: 2, profit: -10 }), true);
    assert.equal(shouldConfirmResetScore({ wins: 3, losses: 0, profit: 30 }), true);
  });

  it("ignora número quebrado ou negativo vindo do servidor", () => {
    assert.equal(resetScoreHighlight({ wins: -3, losses: 0, profit: 0 }), null);
    assert.equal(resetScoreHighlight({ wins: 2.7, losses: 0, profit: 0 }), "2 WINs × 0 LOSSes");
  });

  it("o botão que confirma muda de nome conforme o peso da ação", () => {
    assert.equal(resetScoreConfirmLabel({ wins: 2, losses: 1, profit: 8 }), "Sim, zerar o placar");
    assert.equal(resetScoreConfirmLabel({ wins: 0, losses: 0, profit: 0 }), "Reiniciar placar");
  });
});
