import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  markManualBullexDisconnect,
  resetManualBullexDisconnectForTests,
} from "./manualBullexDisconnect.ts";
import {
  canStartRobotOperation,
  formatMoneyForSpeech,
  isBullExConnected,
  isBullExDisconnected,
  preferStableBullExAccount,
} from "./bullexConnection.ts";

describe("canStartRobotOperation", () => {
  const base = {
    apiConfigured: true,
    connected: true,
    operationRunning: false,
    balance: 10_347.46,
  };

  it("habilita quando conta conectada com saldo e robô parado", () => {
    assert.equal(canStartRobotOperation(base), true);
  });

  it("desabilita sem API, rodando ou saldo zero (connected não trava o botão)", () => {
    assert.equal(canStartRobotOperation({ ...base, apiConfigured: false }), false);
    assert.equal(canStartRobotOperation({ ...base, connected: false }), true);
    assert.equal(canStartRobotOperation({ ...base, operationRunning: true }), false);
    assert.equal(canStartRobotOperation({ ...base, balance: 0 }), false);
  });

  it("aceita saldo null (ainda carregando)", () => {
    assert.equal(canStartRobotOperation({ ...base, balance: null }), true);
  });
});

describe("isBullExConnected com pending", () => {
  it("considera conectado durante pendingConnect mesmo sem account", () => {
    assert.equal(isBullExConnected({ pendingConnect: true }), true);
  });

  it("status CONNECTED prevalece sobre account connected:false stale", () => {
    assert.equal(
      isBullExConnected({
        account: { connected: false } as never,
        accountStatus: { status: "CONNECTED" },
      }),
      true,
    );
  });

  it("backoff sozinho nao confirma desconexao no banner", () => {
    assert.equal(isBullExDisconnected({ accountStatus: { status: "BACKOFF" } }), false);
    assert.equal(
      isBullExConnected({
        account: { connected: true, email: "a@b.com", balance: 10 } as never,
        accountStatus: { status: "BACKOFF" },
      }),
      true,
    );
  });
});

describe("preferStableBullExAccount", () => {
  const good = {
    connected: true,
    balance: 100,
    currency: "BRL",
    mode: "REAL" as const,
    email: "a@b.com",
    requires_2fa: false,
    status: "connected" as const,
  };

  it("mantem snapshot bom quando poll devolve desconectado vazio", () => {
    resetManualBullexDisconnectForTests();
    const next = {
      ...good,
      connected: false,
      balance: null,
      email: null,
      mode: null,
      status: "disconnected" as const,
    };
    assert.deepEqual(preferStableBullExAccount(good, next), good);
  });

  it("preenche email/saldo omitidos em resposta connected", () => {
    resetManualBullexDisconnectForTests();
    const next = { ...good, email: null, balance: null };
    const merged = preferStableBullExAccount(good, next);
    assert.equal(merged.email, "a@b.com");
    assert.equal(merged.balance, 100);
    assert.equal(merged.connected, true);
  });

  it("após desconexão manual aceita desconectado vazio", () => {
    resetManualBullexDisconnectForTests();
    markManualBullexDisconnect();
    const next = {
      ...good,
      connected: false,
      balance: null,
      email: null,
      mode: null,
      status: "disconnected" as const,
    };
    assert.deepEqual(preferStableBullExAccount(good, next), next);
    resetManualBullexDisconnectForTests();
  });
});

describe("formatMoneyForSpeech", () => {
  it("fala reais e centavos sem número decimal quebrado", () => {
    assert.equal(formatMoneyForSpeech(84, "BRL"), "84 reais e 0 centavos");
    assert.equal(formatMoneyForSpeech(84.13, "BRL"), "84 reais e 13 centavos");
    assert.equal(formatMoneyForSpeech(1, "BRL"), "1 real e 0 centavos");
    assert.equal(formatMoneyForSpeech(1.01, "BRL"), "1 real e 1 centavo");
    assert.equal(formatMoneyForSpeech(0.5, "BRL"), "0 reais e 50 centavos");
    assert.equal(formatMoneyForSpeech(0, "BRL"), "0 reais e 0 centavos");
  });

  it("suporta dólar e valores negativos", () => {
    assert.equal(formatMoneyForSpeech(10.05, "USD"), "10 dólares e 5 centavos");
    assert.equal(formatMoneyForSpeech(-12.4, "BRL"), "menos 12 reais e 40 centavos");
  });

  it("não usa vírgula nem ponto decimal na fala", () => {
    const spoken = formatMoneyForSpeech(84.13, "BRL");
    assert.doesNotMatch(spoken, /,/);
    assert.doesNotMatch(spoken, /\d\.\d/);
  });
});
