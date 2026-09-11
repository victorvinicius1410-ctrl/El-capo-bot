/**
 * A opção "Mercado aberto" precisa ficar travada e EXPLICADA.
 *
 * O cadeado antigo usava `title` num `<button disabled>`: no celular não abria
 * nada (não há hover) e em vários navegadores o `title` nem aparece em elemento
 * desabilitado. A pessoa batia num botão morto sem saber por quê.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  OPEN_MARKET_MAINTENANCE_MESSAGE,
  OPEN_MARKET_MAINTENANCE_SHORT,
  OPEN_MARKET_MAINTENANCE_TITLE,
  OPEN_MARKET_UNDER_MAINTENANCE,
  isMarketModeLocked,
  resolveMarketModeLock,
} from "./openMarketMaintenance.ts";

const forexAberto = { openMarketAvailable: true };
const forexFechado = { openMarketAvailable: false, forexClosedMessage: "Abre em 2 dias" };

describe("trava do mercado aberto", () => {
  it("OPEN fica travado por manutenção mesmo com a sessão forex aberta", () => {
    const lock = resolveMarketModeLock("OPEN", forexAberto);
    if (!OPEN_MARKET_UNDER_MAINTENANCE) {
      assert.equal(lock, null);
      return;
    }
    assert.equal(lock?.reason, "maintenance");
    assert.equal(lock?.title, OPEN_MARKET_MAINTENANCE_TITLE);
    assert.equal(lock?.message, OPEN_MARKET_MAINTENANCE_MESSAGE);
  });

  it("a manutenção vem antes do horário — nada de prometer 'abre em 2 dias'", () => {
    const lock = resolveMarketModeLock("OPEN", forexFechado);
    if (!OPEN_MARKET_UNDER_MAINTENANCE) {
      assert.equal(lock?.reason, "forex_closed");
      return;
    }
    assert.equal(lock?.reason, "maintenance");
    assert.ok(!/Abre em/.test(lock?.message ?? ""));
  });

  it("OTC nunca é travado", () => {
    assert.equal(resolveMarketModeLock("OTC", forexAberto), null);
    assert.equal(resolveMarketModeLock("OTC", forexFechado), null);
    assert.equal(isMarketModeLocked("OTC", forexAberto), false);
  });

  it("BOTH nunca é travado — continua operando OTC normalmente", () => {
    assert.equal(resolveMarketModeLock("BOTH", forexAberto), null);
    assert.equal(resolveMarketModeLock("BOTH", forexFechado), null);
  });

  it("a mensagem diz o motivo e o que fazer, sem jargão", () => {
    assert.ok(OPEN_MARKET_MAINTENANCE_MESSAGE.length > 40);
    assert.match(OPEN_MARKET_MAINTENANCE_MESSAGE, /corretora/i);
    assert.match(OPEN_MARKET_MAINTENANCE_MESSAGE, /OTC/);
  });

  it("o resumo do botão convida ao toque (celular não tem hover)", () => {
    assert.match(OPEN_MARKET_MAINTENANCE_SHORT, /toque/i);
  });

  it("isMarketModeLocked concorda com resolveMarketModeLock", () => {
    for (const mode of ["OTC", "OPEN", "BOTH"] as const) {
      assert.equal(
        isMarketModeLocked(mode, forexAberto),
        resolveMarketModeLock(mode, forexAberto) !== null,
      );
    }
  });

  it("reabrir é trocar uma constante só", () => {
    assert.equal(typeof OPEN_MARKET_UNDER_MAINTENANCE, "boolean");
  });
});
