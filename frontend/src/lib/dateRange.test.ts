import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { fromBrasiliaDate } from "./brasiliaTime.ts";
import {
  inclusiveDayCount,
  isStampInRange,
  matchPreset,
  rangeFromDays,
  rangeFromPreset,
  trailingDaysUntilToday,
} from "./dateRange.ts";

const TODAY = fromBrasiliaDate(2026, 7, 14, 15, 30, 0);

describe("rangeFromPreset", () => {
  it("hoje cobre só o dia corrente", () => {
    const range = rangeFromPreset("today", TODAY, 90);
    assert.equal(range.start.toISOString(), fromBrasiliaDate(2026, 7, 14).toISOString());
    assert.equal(range.end.toISOString(), fromBrasiliaDate(2026, 7, 14).toISOString());
  });

  it("ontem não inclui hoje", () => {
    const range = rangeFromPreset("yesterday", TODAY, 90);
    assert.equal(range.start.toISOString(), fromBrasiliaDate(2026, 7, 13).toISOString());
    assert.equal(range.end.toISOString(), fromBrasiliaDate(2026, 7, 13).toISOString());
  });

  it("últimos 7 dias são inclusivos até hoje", () => {
    const range = rangeFromPreset("last_7", TODAY, 90);
    assert.equal(range.start.toISOString(), fromBrasiliaDate(2026, 7, 8).toISOString());
    assert.equal(inclusiveDayCount(range.start, range.end), 7);
  });

  it("esta semana começa na segunda", () => {
    const range = rangeFromPreset("this_week", TODAY, 90);
    assert.equal(range.start.toISOString(), fromBrasiliaDate(2026, 7, 10).toISOString());
    assert.equal(range.end.toISOString(), fromBrasiliaDate(2026, 7, 14).toISOString());
  });

  it("semana passada é a segunda a domingo anteriores", () => {
    const range = rangeFromPreset("last_week", TODAY, 90);
    assert.equal(range.start.toISOString(), fromBrasiliaDate(2026, 7, 3).toISOString());
    assert.equal(range.end.toISOString(), fromBrasiliaDate(2026, 7, 9).toISOString());
  });

  it("mês passado é julho inteiro em 14/08/2026", () => {
    const range = rangeFromPreset("last_month", TODAY, 365);
    assert.equal(range.start.toISOString(), fromBrasiliaDate(2026, 6, 1).toISOString());
    assert.equal(range.end.toISOString(), fromBrasiliaDate(2026, 6, 31).toISOString());
  });

  it("máximo respeita maxDays", () => {
    const range = rangeFromPreset("maximum", TODAY, 90);
    assert.equal(inclusiveDayCount(range.start, range.end), 90);
  });
});

describe("trailingDaysUntilToday", () => {
  it("busca do início até hoje mesmo se o fim for no passado", () => {
    const start = fromBrasiliaDate(2026, 6, 1);
    assert.equal(trailingDaysUntilToday(start, TODAY, 365), 45);
  });
});

describe("rangeFromDays", () => {
  it("reconstrói intervalo trailing", () => {
    const range = rangeFromDays(30, TODAY, "last_30");
    assert.equal(matchPreset(range, TODAY, 90), "last_30");
  });
});

describe("isStampInRange", () => {
  it("inclui timestamps no dia civil de Brasília de início e fim", () => {
    const start = fromBrasiliaDate(2026, 7, 13);
    const end = fromBrasiliaDate(2026, 7, 13);
    assert.equal(isStampInRange(fromBrasiliaDate(2026, 7, 13, 12).toISOString(), start, end), true);
    assert.equal(isStampInRange(fromBrasiliaDate(2026, 7, 14, 12).toISOString(), start, end), false);
  });

  it("hoje em Brasília cobre 22h mesmo depois da meia-noite UTC", () => {
    const late = new Date("2026-08-19T01:10:00.000Z");
    const range = rangeFromPreset("today", late, 90);
    assert.equal(isStampInRange("2026-08-19T01:10:00.000Z", range.start, range.end), true);
    assert.equal(isStampInRange("2026-08-19T04:00:00.000Z", range.start, range.end), false);
    assert.equal(range.start.toISOString(), "2026-08-18T03:00:00.000Z");
  });
});
