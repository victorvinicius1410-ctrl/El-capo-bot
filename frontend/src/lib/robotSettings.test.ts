import assert from "node:assert/strict";
import { describe, it, beforeEach } from "node:test";
import {
  DEFAULT_ENTRY_VALUE,
  ENTRY_VALUE_MAX,
  ENTRY_VALUE_MIN,
  STOP_MONEY_MIN,
  STOP_OPERATIONS_MIN,
  coerceSelectableMarketMode,
  formatForexOpenCountdown,
  getRobotSettingsSnapshot,
  hoursUntilForexOpenMarket,
  isForexOpenMarketAvailable,
  markRobotSettingsSynced,
  normalizeRobotSettings,
  normalizeStopMode,
  parseEntryValueInput,
  parseStopMoneyInput,
  parseStopOperationsInput,
  rememberRobotSettingsFromState,
  resetRobotSettingsState,
  setRobotSettingsForUser,
  visibleRobotMarketModeOptions,
} from "./robotSettings.ts";

describe("robotSettings money parsing", () => {
  it("exige mínimo de 5 em stop win/loss", () => {
    assert.equal(STOP_MONEY_MIN, 5);
    assert.equal(parseStopMoneyInput("5", 50), 5);
    assert.equal(parseStopMoneyInput("5.5", 50), 5.5);
    assert.equal(parseStopMoneyInput("4.99", 50), 50);
    assert.equal(parseStopMoneyInput("3", 50), 50);
    assert.equal(normalizeRobotSettings({ stopWin: 5, stopLoss: 5 }).stopWin, 5);
    assert.equal(normalizeRobotSettings({ stopWin: 5, stopLoss: 5 }).stopLoss, 5);
    assert.equal(normalizeRobotSettings({ stopWin: 4, stopLoss: 3 }).stopWin, STOP_MONEY_MIN);
    assert.equal(normalizeRobotSettings({ stopWin: 4, stopLoss: 3 }).stopLoss, STOP_MONEY_MIN);
  });

  it("rejeita stop win/loss zero ou negativo mantendo o fallback", () => {
    assert.equal(parseStopMoneyInput("0", 50), 50);
    assert.equal(parseStopMoneyInput("-10", 30), 30);
    assert.equal(parseStopMoneyInput("", 50), null);
    assert.equal(parseStopMoneyInput("abc", 50), null);
  });

  it("exige mínimo de 5 no valor de entrada", () => {
    assert.equal(ENTRY_VALUE_MIN, 5);
    assert.equal(DEFAULT_ENTRY_VALUE, 5);
    assert.equal(parseEntryValueInput("5", 5), 5);
    assert.equal(parseEntryValueInput("5.01", 5), 5.01);
    assert.equal(parseEntryValueInput("4.99", 5), 5);
    assert.equal(parseEntryValueInput("1", 5), 5);
    assert.equal(normalizeRobotSettings({ entryValue: 5 }).entryValue, 5);
    assert.equal(normalizeRobotSettings({ entryValue: 1 }).entryValue, ENTRY_VALUE_MIN);
  });

  it("bloqueia valor de entrada zero ou negativo", () => {
    assert.equal(parseEntryValueInput("0", 5), 5);
    assert.equal(parseEntryValueInput("-1", 5), 5);
    assert.equal(normalizeRobotSettings({ entryValue: 0 }).entryValue, ENTRY_VALUE_MIN);
    assert.equal(normalizeRobotSettings({ entryValue: -5 }).entryValue, ENTRY_VALUE_MIN);
  });

  it("respeita o teto máximo da entrada", () => {
    assert.equal(parseEntryValueInput(String(ENTRY_VALUE_MAX + 1), 5), ENTRY_VALUE_MAX);
    assert.equal(normalizeRobotSettings({ entryValue: ENTRY_VALUE_MAX + 50 }).entryValue, ENTRY_VALUE_MAX);
  });

  it("permite string vazia durante a digitação sem resetar para o default", () => {
    assert.equal(parseStopMoneyInput("", 50), null);
    assert.equal(parseEntryValueInput("", 5), null);
  });
});

describe("robotSettings stop modes", () => {
  it("normaliza modos money e operations", () => {
    assert.equal(normalizeStopMode("money"), "money");
    assert.equal(normalizeStopMode("valor"), "money");
    assert.equal(normalizeStopMode("operations"), "operations");
    assert.equal(normalizeStopMode("quantidade"), "operations");
    assert.equal(normalizeStopMode("qtd"), "operations");
  });

  it("exige mínimo de 1 operação no stop por quantidade", () => {
    assert.equal(STOP_OPERATIONS_MIN, 1);
    assert.equal(parseStopOperationsInput("1", 5), 1);
    assert.equal(parseStopOperationsInput("0", 5), 5);
    assert.equal(parseStopOperationsInput("", 5), null);
    assert.equal(normalizeRobotSettings({ stopWinOperations: 0 }).stopWinOperations, STOP_OPERATIONS_MIN);
    assert.equal(normalizeRobotSettings({ stopLossOperations: 8 }).stopLossOperations, 8);
    assert.equal(normalizeRobotSettings({ stopWinMode: "operations" }).stopWinMode, "operations");
    assert.equal(normalizeRobotSettings({ stopLossMode: "ops" }).stopLossMode, "operations");
  });
});

describe("rememberRobotSettingsFromState preserva escolha do usuário", () => {
  beforeEach(() => {
    resetRobotSettingsState();
  });

  it("não volta timeframe/mercado para M1/OTC quando o poll traz campos vazios", () => {
    setRobotSettingsForUser("u1", {
      timeframe: "M5",
      marketMode: "OPEN",
      entryValue: 12,
      stopWin: 80,
      stopLoss: 40,
    });

    rememberRobotSettingsFromState("u1", {
      timeframe: undefined,
      marketMode: undefined,
      entryValue: undefined,
      stopWin: undefined,
      stopLoss: undefined,
    });

    const snapshot = getRobotSettingsSnapshot("u1");
    assert.equal(snapshot.timeframe, "M5");
    assert.equal(snapshot.marketMode, "OPEN");
    assert.equal(snapshot.entryValue, 12);
    assert.equal(snapshot.stopWin, 80);
    assert.equal(snapshot.stopLoss, 40);
  });

  it("preserva edições locais ainda não salvas contra poll com defaults do backend", () => {
    markRobotSettingsSynced("u1", {
      timeframe: "M1",
      marketMode: "OTC",
      entryValue: 5,
      stopWin: 50,
      stopLoss: 30,
    });
    setRobotSettingsForUser("u1", {
      timeframe: "M15",
      marketMode: "BOTH",
      entryValue: 25,
      stopWin: 120,
      stopLoss: 60,
      martingaleEnabled: true,
      martingaleSteps: 2,
    });

    rememberRobotSettingsFromState("u1", {
      timeframe: "M1",
      marketMode: "OTC",
      entryValue: 5,
      stopWin: 50,
      stopLoss: 30,
      martingaleEnabled: false,
      martingaleSteps: 1,
    });

    const snapshot = getRobotSettingsSnapshot("u1");
    assert.equal(snapshot.timeframe, "M15");
    assert.equal(snapshot.marketMode, "BOTH");
    assert.equal(snapshot.entryValue, 25);
    assert.equal(snapshot.stopWin, 120);
    assert.equal(snapshot.stopLoss, 60);
    assert.equal(snapshot.martingaleEnabled, true);
    assert.equal(snapshot.martingaleSteps, 2);
  });

  it("aplica estado do backend quando não há edição local pendente", () => {
    markRobotSettingsSynced("u1", {
      timeframe: "M1",
      marketMode: "OTC",
      entryValue: 5,
      stopWin: 50,
      stopLoss: 30,
    });

    rememberRobotSettingsFromState("u1", {
      timeframe: "M5",
      marketMode: "OPEN",
      entryValue: 9,
      stopWin: 70,
      stopLoss: 35,
    });

    const snapshot = getRobotSettingsSnapshot("u1");
    assert.equal(snapshot.timeframe, "M5");
    assert.equal(snapshot.marketMode, "OPEN");
    assert.equal(snapshot.entryValue, 9);
    assert.equal(snapshot.stopWin, 70);
    assert.equal(snapshot.stopLoss, 35);
  });
});

describe("robotSettings mercado aberto / OTC", () => {
  it("mantém OPEN visível no sábado com cadeado e contagem de horas", () => {
    const saturday = new Date(Date.UTC(2026, 6, 25, 15, 0, 0)); // 25 Jul 2026 sábado
    assert.equal(isForexOpenMarketAvailable(saturday), false);
    assert.equal(coerceSelectableMarketMode("OPEN", saturday), "OTC");
    assert.equal(coerceSelectableMarketMode("BOTH", saturday), "BOTH");
    const options = visibleRobotMarketModeOptions(saturday);
    assert.equal(options.length, 3);
    assert.equal(options.some((item) => item.value === "OPEN"), true);
    const hours = hoursUntilForexOpenMarket(saturday);
    assert.ok(hours != null && hours > 0);
    const label = formatForexOpenCountdown(saturday);
    assert.ok(label && /Abre em/.test(label));
  });

  it("mantém OPEN liberado na quarta-feira UTC", () => {
    const wednesday = new Date(Date.UTC(2026, 6, 22, 12, 0, 0));
    assert.equal(isForexOpenMarketAvailable(wednesday), true);
    assert.equal(coerceSelectableMarketMode("OPEN", wednesday), "OPEN");
    assert.equal(visibleRobotMarketModeOptions(wednesday).length, 3);
    assert.equal(hoursUntilForexOpenMarket(wednesday), null);
    assert.equal(formatForexOpenCountdown(wednesday), null);
  });
});
