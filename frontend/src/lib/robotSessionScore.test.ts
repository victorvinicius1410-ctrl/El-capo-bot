import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { getStoppedRobotState, mergeRobotSessionScore, preserveRobotSessionScore } from "./robotState.ts";

describe("preserveRobotSessionScore", () => {
  it("mantém o placar da sessão quando start/stop chega zerado", () => {
    const previous = { ...getStoppedRobotState(), wins: 4, losses: 2, profit: 18.5 };
    const incoming = { ...getStoppedRobotState(), enabled: true, wins: 0, losses: 0, profit: 0 };
    const merged = preserveRobotSessionScore(previous, incoming);
    assert.equal(merged.enabled, true);
    assert.equal(merged.wins, 4);
    assert.equal(merged.losses, 2);
    assert.equal(merged.profit, 18.5);
  });

  it("aceita zeragem explícita (Reiniciar placar)", () => {
    const previous = { ...getStoppedRobotState(), wins: 4, losses: 2, profit: 18.5 };
    const incoming = { ...getStoppedRobotState(), wins: 0, losses: 0, profit: 0 };
    const merged = preserveRobotSessionScore(previous, incoming, { allowBlankOverwrite: true });
    assert.equal(merged.wins, 0);
    assert.equal(merged.losses, 0);
    assert.equal(merged.profit, 0);
  });

  it("aplica placar novo quando o snapshot traz números reais", () => {
    const previous = { ...getStoppedRobotState(), wins: 4, losses: 2, profit: 18.5 };
    const incoming = { ...getStoppedRobotState(), wins: 5, losses: 2, profit: 26 };
    const merged = preserveRobotSessionScore(previous, incoming);
    assert.equal(merged.wins, 5);
    assert.equal(merged.losses, 2);
    assert.equal(merged.profit, 26);
  });

  it("não deixa snapshot atrasado derrubar o placar no start/stop (5x3 → 2x0)", () => {
    const previous = { ...getStoppedRobotState(), wins: 5, losses: 3, profit: 42 };
    const incoming = { ...getStoppedRobotState(), enabled: false, wins: 2, losses: 0, profit: 12 };
    const merged = preserveRobotSessionScore(previous, incoming);
    assert.equal(merged.wins, 5);
    assert.equal(merged.losses, 3);
    assert.equal(merged.profit, 42);
  });

  it("aceita queda quando a exclusão foi pedida (allowScoreDecrease)", () => {
    const previous = { ...getStoppedRobotState(), wins: 5, losses: 3, profit: 42 };
    const incoming = { ...getStoppedRobotState(), wins: 5, losses: 2, profit: 34 };
    const merged = preserveRobotSessionScore(previous, incoming, { allowScoreDecrease: true });
    assert.equal(merged.wins, 5);
    assert.equal(merged.losses, 2);
    assert.equal(merged.profit, 34);
  });

  it("aceita 0-0 pedido — exclusão da última operação", () => {
    // Antes caía no ramo "snapshot chegou em branco" e o placar antigo voltava.
    const previous = { ...getStoppedRobotState(), wins: 1, losses: 0, profit: 8.7 };
    const incoming = { ...getStoppedRobotState(), wins: 0, losses: 0, profit: 0 };
    const merged = preserveRobotSessionScore(previous, incoming, { allowScoreDecrease: true });
    assert.equal(merged.wins, 0);
    assert.equal(merged.losses, 0);
    assert.equal(merged.profit, 0);
  });

  it("descarta a réplica atrasada repetindo o placar de antes da exclusão", () => {
    const previous = { ...getStoppedRobotState(), wins: 4, losses: 3, profit: 33.3 };
    const incoming = { ...getStoppedRobotState(), wins: 5, losses: 3, profit: 42 };
    const merged = preserveRobotSessionScore(previous, incoming, { forceScorePreserve: true });
    assert.equal(merged.wins, 4);
    assert.equal(merged.losses, 3);
    assert.equal(merged.profit, 33.3);
  });

  it("sem baixa pedida, queda de uma operação continua sendo atraso", () => {
    // O ±1 antigo era um palpite: com um WIN novo entrando junto (wins-1,
    // losses+1) ele já errava. Agora quem decide é resolveSessionScoreGate.
    const previous = { ...getStoppedRobotState(), wins: 5, losses: 3, profit: 42 };
    const incoming = { ...getStoppedRobotState(), wins: 5, losses: 2, profit: 34 };
    const merged = preserveRobotSessionScore(previous, incoming);
    assert.equal(merged.wins, 5);
    assert.equal(merged.losses, 3);
  });

  it("não inventa placar quando ainda não havia sessão", () => {
    const incoming = { ...getStoppedRobotState(), wins: 0, losses: 0, profit: 0 };
    const merged = preserveRobotSessionScore(undefined, incoming);
    assert.equal(merged.wins, 0);
    assert.equal(merged.losses, 0);
  });

  it("não deixa snapshot atrasado desfazer o Reiniciar placar", () => {
    const previous = {
      ...getStoppedRobotState(),
      wins: 0,
      losses: 0,
      profit: 0,
      stop_reset_at: "2026-08-15T19:00:00+00:00",
    };
    const incoming = {
      ...getStoppedRobotState(),
      wins: 10,
      losses: 12,
      profit: 55,
      stop_reset_at: "2026-08-15T12:00:00+00:00",
    };
    const merged = preserveRobotSessionScore(previous, incoming);
    assert.equal(merged.wins, 0);
    assert.equal(merged.losses, 0);
    assert.equal(merged.profit, 0);
    assert.equal(merged.stop_reset_at, previous.stop_reset_at);
  });

  it("grava o placar gerado no Shift+O por cima do 0-0 do overlay", () => {
    const previous = { ...getStoppedRobotState(), wins: 0, losses: 0, profit: 0 };
    const merged = mergeRobotSessionScore(previous, { wins: 8, losses: 2, profit: 46.4 });
    assert.equal(merged.wins, 8);
    assert.equal(merged.losses, 2);
    assert.equal(merged.profit, 46.4);
    assert.equal(merged.enabled, previous.enabled);
  });

  it("soma operação avulsa do Shift+O no placar já exibido", () => {
    const previous = { ...getStoppedRobotState(), wins: 8, losses: 2, profit: 46.4 };
    const merged = mergeRobotSessionScore(
      previous,
      { wins: 1, losses: 0, profit: 8.7 },
      { accumulate: true },
    );
    assert.equal(merged.wins, 9);
    assert.equal(merged.losses, 2);
    assert.equal(merged.profit, 55.1);
  });

  it("subtrai operação excluída do placar do overlay", () => {
    const previous = { ...getStoppedRobotState(), wins: 8, losses: 2, profit: 46.4 };
    const merged = mergeRobotSessionScore(
      previous,
      { wins: 1, losses: 0, profit: 8.7 },
      { subtract: true },
    );
    assert.equal(merged.wins, 7);
    assert.equal(merged.losses, 2);
    assert.equal(merged.profit, 37.7);
  });

  it("não deixa wins/losses negativos ao subtrair", () => {
    const previous = { ...getStoppedRobotState(), wins: 0, losses: 1, profit: -10 };
    const merged = mergeRobotSessionScore(
      previous,
      { wins: 1, losses: 1, profit: -10 },
      { subtract: true },
    );
    assert.equal(merged.wins, 0);
    assert.equal(merged.losses, 0);
    assert.equal(merged.profit, 0);
  });
});
