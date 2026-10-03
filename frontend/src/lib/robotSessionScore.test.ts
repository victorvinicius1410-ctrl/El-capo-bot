import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  getStoppedRobotState,
  deletedTradeCountsInScore,
  mergeRobotSessionScore,
  optimisticScoreReset,
  preserveRobotSessionScore,
  SESSION_SCORE_RESET_GUARD_MS,
} from "./robotState.ts";

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
    const resetAt = Date.parse("2026-08-15T19:00:00+00:00");
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
    const merged = preserveRobotSessionScore(previous, incoming, { now: resetAt + 10_000 });
    assert.equal(merged.wins, 0);
    assert.equal(merged.losses, 0);
    assert.equal(merged.profit, 0);
    assert.equal(merged.stop_reset_at, previous.stop_reset_at);
  });

  it("passada a janela do reset, o placar do servidor volta a valer", () => {
    // Sem prazo, o painel guardava o `stop_reset_at` mais novo e recusava
    // TODO placar recebido depois: o cliente via 0x0 até apertar F5.
    const resetAt = Date.parse("2026-08-15T19:00:00+00:00");
    const previous = {
      ...getStoppedRobotState(),
      wins: 0,
      losses: 0,
      profit: 0,
      stop_reset_at: "2026-08-15T19:00:00+00:00",
    };
    const incoming = {
      ...getStoppedRobotState(),
      wins: 3,
      losses: 1,
      profit: 12.5,
      stop_reset_at: "2026-08-15T12:00:00+00:00",
    };
    const merged = preserveRobotSessionScore(previous, incoming, {
      now: resetAt + SESSION_SCORE_RESET_GUARD_MS + 1,
    });
    assert.equal(merged.wins, 3);
    assert.equal(merged.losses, 1);
    assert.equal(merged.profit, 12.5);
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

describe("optimisticScoreReset", () => {
  it("zera o placar no clique e marca o instante do reset", () => {
    const previous = { ...getStoppedRobotState(), wins: 1, losses: 4, profit: -64.12 };
    const zerado = optimisticScoreReset(previous, "2026-10-02T18:18:39.000Z");
    assert.equal(zerado.wins, 0);
    assert.equal(zerado.losses, 0);
    assert.equal(zerado.profit, 0);
    assert.equal(zerado.stop_reset_at, "2026-10-02T18:18:39.000Z");
    assert.equal(previous.losses, 4);
  });

  it("snapshot atrasado não devolve o placar antigo antes de o servidor responder", () => {
    // Caso de 02/10/2026: placar 1x4, reset pedido, e o WS ainda entrega o
    // snapshot de antes do reset enquanto o POST não volta.
    const clique = Date.parse("2026-10-02T18:18:39.000Z");
    const exibido = optimisticScoreReset(
      { ...getStoppedRobotState(), wins: 1, losses: 4, profit: -64.12, stop_reset_at: "2026-10-02T15:18:08.000Z" },
      new Date(clique).toISOString(),
    );
    const atrasado = {
      ...getStoppedRobotState(),
      wins: 1,
      losses: 4,
      profit: -64.12,
      stop_reset_at: "2026-10-02T15:18:08.000Z",
    };
    const merged = preserveRobotSessionScore(exibido, atrasado, { now: clique + 800 });
    assert.equal(merged.wins, 0);
    assert.equal(merged.losses, 0);
    assert.equal(merged.profit, 0);
  });
});

describe("deletedTradeCountsInScore", () => {
  const reset = "2026-10-02T18:18:39.000Z";

  it("operação de antes do Reiniciar placar não sai do placar (caso de 02/10)", () => {
    // 4 LOSS apagados depois do reset viravam 0x0 com +81,89.
    assert.equal(deletedTradeCountsInScore("2026-10-02T17:40:00.000Z", reset), false);
  });

  it("operação depois do reset sai do placar", () => {
    assert.equal(deletedTradeCountsInScore("2026-10-02T18:30:00.000Z", reset), true);
    assert.equal(deletedTradeCountsInScore(reset, reset), true);
  });

  it("sem reset, a janela começa no placar contínuo (01/10)", () => {
    assert.equal(deletedTradeCountsInScore("2026-09-29T12:00:00.000Z", null), false);
    assert.equal(deletedTradeCountsInScore("2026-10-01T12:00:00.000Z", null), true);
    // Reset antigo, de antes da regra, também cai em 01/10 (igual ao servidor).
    assert.equal(deletedTradeCountsInScore("2026-09-29T12:00:00.000Z", "2026-09-20T00:00:00Z"), false);
  });

  it("sem data, desconta como antes", () => {
    assert.equal(deletedTradeCountsInScore(undefined, reset), true);
    assert.equal(deletedTradeCountsInScore("lixo", reset), true);
  });
});
