/**
 * O botão Iniciar/Parar não pode "voltar sozinho" depois do clique (30/09/2026:
 * "tinha que clicar 3 vezes para parar").
 */

import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";
import {
  OPERATION_INTENT_WINDOW_MS,
  applyOperationIntent,
  clearOperationIntent,
  getOperationIntent,
  registerOperationIntent,
  resetOperationIntentsForTests,
} from "./robotOperationIntent.ts";

const USER = "user-1";

describe("intenção de Iniciar/Parar", () => {
  beforeEach(() => resetOperationIntentsForTests());

  it("estado atrasado dizendo 'ligado' não desfaz o Parar", () => {
    registerOperationIntent(USER, false, 1_000);
    const atrasado = { enabled: true, worker_running: true, status: "ANALYZING" };
    const exibido = applyOperationIntent(USER, atrasado, 2_000);
    assert.equal(exibido.enabled, false);
    assert.equal(exibido.worker_running, false);
    assert.equal(exibido.status, "ANALYZING");
  });

  it("estado atrasado dizendo 'parado' não desfaz o Iniciar", () => {
    registerOperationIntent(USER, true, 1_000);
    assert.equal(applyOperationIntent(USER, { enabled: false }, 1_500).enabled, true);
  });

  it("estado atrasado DEPOIS da resposta do próprio clique também é barrado", () => {
    registerOperationIntent(USER, false, 1_000);
    assert.equal(applyOperationIntent(USER, { enabled: false }, 1_200).enabled, false); // resposta do stop
    assert.equal(applyOperationIntent(USER, { enabled: true }, 1_900).enabled, false); // WS atrasado
  });

  it("a intenção expira: nunca prende o painel", () => {
    registerOperationIntent(USER, false, 1_000);
    const depois = 1_000 + OPERATION_INTENT_WINDOW_MS + 1;
    assert.equal(applyOperationIntent(USER, { enabled: true }, depois).enabled, true);
  });

  it("clique recusado pelo servidor desfaz a intenção", () => {
    registerOperationIntent(USER, true, 1_000);
    clearOperationIntent(USER);
    assert.equal(applyOperationIntent(USER, { enabled: false }, 1_100).enabled, false);
  });

  it("não mistura usuários", () => {
    registerOperationIntent(USER, false, 1_000);
    assert.equal(applyOperationIntent("outro", { enabled: true }, 1_100).enabled, true);
  });
});
