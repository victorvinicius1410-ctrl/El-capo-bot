import assert from "node:assert/strict";
import { test } from "node:test";

import { masanielloFormView } from "./masanielloPresentation.ts";
import { DEFAULT_ROBOT_SETTINGS, normalizeRobotSettings } from "./robotSettings.ts";
import { operationSummaryRows } from "./startOperationSummary.ts";

const plain = (text: string) => text.split(String.fromCharCode(160)).join(" ");
const asPairs = (rows: ReturnType<typeof operationSummaryRows>) =>
  rows.map((row) => [row.label, plain(row.value)]);

test("valor fixo: entrada, os dois stops e o gale", () => {
  const config = normalizeRobotSettings({ ...DEFAULT_ROBOT_SETTINGS, stopWin: 77, stopLoss: 30 });
  assert.deepEqual(asPairs(operationSummaryRows(config, "BRL", null)), [
    ["Operação", "1 min · OTC"],
    ["Cada entrada", "R$ 5,00"],
    ["Para ao ganhar", "+R$ 77,00"],
    ["Para ao perder", "−R$ 30,00"],
    ["Gale", "Desligado"],
  ]);
});

test("stop por operações e gale ligado aparecem como foram configurados", () => {
  const config = normalizeRobotSettings({
    ...DEFAULT_ROBOT_SETTINGS,
    timeframe: "M5",
    marketMode: "BOTH",
    stopWinMode: "operations",
    stopWinOperations: 5,
    stopLossMode: "operations",
    stopLossOperations: 1,
    martingaleEnabled: true,
    martingaleSteps: 2,
    martingaleMultiplier: 2.5,
  });
  assert.deepEqual(asPairs(operationSummaryRows(config, "BRL", null)), [
    ["Operação", "5 min · Ambos"],
    ["Cada entrada", "R$ 5,00"],
    ["Para ao ganhar", "5 WINs"],
    ["Para ao perder", "1 LOSS"],
    ["Gale", "até 2 · 2,5x"],
  ]);
});

test("Gerenciamento Consistente: capital, meta, limite e tamanho das entradas", () => {
  const config = normalizeRobotSettings({
    ...DEFAULT_ROBOT_SETTINGS,
    masanielloEnabled: true,
    masanielloCapital: 100,
  });
  const plan = masanielloFormView({
    capital: 100,
    profile: "conservador",
    operations: 10,
    wins: 4,
    payoutRef: 80,
    currency: "BRL",
    balance: 500,
  });
  const rows = operationSummaryRows(config, "BRL", plan);
  assert.deepEqual(asPairs(rows), [
    ["Operação", "1 min · OTC"],
    ["Capital do ciclo", "R$ 100,00"],
    ["Meta (4 acertos)", "+R$ 10,58"],
    ["Limite (7 erros)", "−R$ 100,00"],
    ["1ª entrada", "R$ 6,82"],
    ["Maior entrada", "R$ 61,43"],
  ]);
  assert.deepEqual(
    rows.map((row) => row.tone),
    ["neutral", "neutral", "positive", "negative", "neutral", "neutral"],
  );
});

test("conta em dólar usa o símbolo da conta", () => {
  const config = normalizeRobotSettings({ ...DEFAULT_ROBOT_SETTINGS, entryValue: 2 }, "USD");
  const rows = asPairs(operationSummaryRows(config, "USD", null));
  assert.equal(rows[1][1], "$2.00");
});
