import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";

import {
  PRIVACY_MASK,
  isPrivacyModeOn,
  maskMoney,
  privacyToggleLabel,
  setPrivacyMode,
  togglePrivacyMode,
} from "./privacyMode.ts";

beforeEach(() => {
  setPrivacyMode(false);
});

describe("modo privacidade", () => {
  it("começa desligado", () => {
    assert.equal(isPrivacyModeOn(), false);
  });

  it("alterna e devolve o novo estado", () => {
    assert.equal(togglePrivacyMode(), true);
    assert.equal(isPrivacyModeOn(), true);
    assert.equal(togglePrivacyMode(), false);
    assert.equal(isPrivacyModeOn(), false);
  });

  it("avisa quem estiver ligado só quando o valor muda", () => {
    // Sem window o módulo não persiste; o que importa aqui é a notificação.
    setPrivacyMode(true);
    assert.equal(isPrivacyModeOn(), true);
    setPrivacyMode(true);
    assert.equal(isPrivacyModeOn(), true);
  });
});

describe("maskMoney", () => {
  it("devolve o texto original com o modo desligado", () => {
    assert.equal(maskMoney("R$ 84,13", false), "R$ 84,13");
    assert.equal(maskMoney("-", false), "-");
  });

  it("troca qualquer valor pela máscara com o modo ligado", () => {
    assert.equal(maskMoney("R$ 84,13", true), PRIVACY_MASK);
    assert.equal(maskMoney("+R$ 1.200,00", true), PRIVACY_MASK);
    assert.equal(maskMoney("-", true), PRIVACY_MASK);
  });
});

describe("privacyToggleLabel", () => {
  it("diz a ação que o clique faz", () => {
    assert.equal(privacyToggleLabel(false), "Esconder saldo");
    assert.equal(privacyToggleLabel(true), "Mostrar saldo");
  });
});
