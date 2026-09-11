import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  DEFAULT_MARKETING_DEMO_SETTINGS,
  MARKETING_ASSET_OPTIONS,
  formatMarketingTradeDateTime,
  localDateTimeInputToIso,
  normalizeMarketingDemoSettings,
  resolveMarketingTradePreview,
  toLocalDateTimeInputValue,
} from "./marketingDemoSettings.ts";

describe("marketingDemoSettings", () => {
  it("normaliza valor, payout e ativo do Shift+O", () => {
    const settings = normalizeMarketingDemoSettings({
      amount: 25,
      payout: 90,
      asset: " gbpusd-otc ",
      direction: "PUT",
      result: "WIN",
    });
    assert.equal(settings.amount, 25);
    assert.equal(settings.payout, 90);
    assert.equal(settings.asset, "GBPUSD-OTC");
    assert.equal(settings.direction, "PUT");
    assert.equal(settings.result, "WIN");
  });

  it("rejeita payout inválido e cai no padrão", () => {
    const settings = normalizeMarketingDemoSettings({ payout: 150, amount: -1, result: "DRAW" as "WIN" });
    assert.equal(settings.payout, DEFAULT_MARKETING_DEMO_SETTINGS.payout);
    assert.equal(settings.amount, DEFAULT_MARKETING_DEMO_SETTINGS.amount);
    assert.equal(settings.result, DEFAULT_MARKETING_DEMO_SETTINGS.result);
  });

  it("resolve preview com valor, payout e resultado forçado", () => {
    const preview = resolveMarketingTradePreview({
      ...DEFAULT_MARKETING_DEMO_SETTINGS,
      amount: 40,
      payout: 82,
      asset: "USDJPY-OTC",
      direction: "CALL",
      result: "LOSS",
    });
    assert.equal(preview.amount, 40);
    assert.equal(preview.payout, 82);
    assert.equal(preview.asset, "USDJPY-OTC");
    assert.equal(preview.direction, "CALL");
    assert.equal(preview.result, "LOSS");
  });

  it("expõe lista de ativos para o seletor do painel", () => {
    assert.ok(MARKETING_ASSET_OPTIONS.length >= 3);
    assert.ok(MARKETING_ASSET_OPTIONS.includes("EURUSD-OTC"));
    assert.ok(!MARKETING_ASSET_OPTIONS.includes("ETHUSD-OTC" as (typeof MARKETING_ASSET_OPTIONS)[number]));
    assert.ok(!MARKETING_ASSET_OPTIONS.includes("BTCUSD-OTC" as (typeof MARKETING_ASSET_OPTIONS)[number]));
  });

  it("converte datetime-local de Brasília para ISO8601 UTC", () => {
    const iso = localDateTimeInputToIso("2026-07-20T14:30");
    assert.equal(iso, "2026-07-20T17:30:00.000Z");
  });

  it("rejeita datetime-local inválido", () => {
    assert.equal(localDateTimeInputToIso(""), null);
    assert.equal(localDateTimeInputToIso("20/07/2026"), null);
    assert.equal(localDateTimeInputToIso("2026-02-31T10:00"), null);
  });

  it("formata created_at para exibição pt-BR em Brasília", () => {
    const formatted = formatMarketingTradeDateTime("2026-07-20T17:30:00.000Z");
    assert.match(formatted, /20\/07\/2026/);
    assert.match(formatted, /14:30/);
    assert.equal(formatMarketingTradeDateTime(null), "");
    assert.equal(formatMarketingTradeDateTime("nao-data"), "");
  });

  it("gera valor inicial para datetime-local em Brasília", () => {
    const value = toLocalDateTimeInputValue(new Date("2026-07-20T17:05:00.000Z"));
    assert.equal(value, "2026-07-20T14:05");
  });
});
