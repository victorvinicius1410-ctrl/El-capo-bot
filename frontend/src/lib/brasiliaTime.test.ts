import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  brasiliaDateKey,
  brasiliaDateParts,
  formatBrasiliaDate,
  formatBrasiliaDateTime,
  fromBrasiliaDate,
} from "./brasiliaTime.ts";

describe("brasiliaTime", () => {
  it("depois da meia-noite UTC ainda é o dia anterior em Brasília", () => {
    const instant = new Date("2026-08-19T01:30:00.000Z");
    assert.deepEqual(brasiliaDateParts(instant), { year: 2026, month: 7, day: 18 });
    assert.equal(brasiliaDateKey(instant), "2026-08-18");
  });

  it("meia-noite de Brasília é 03:00 UTC", () => {
    const start = fromBrasiliaDate(2026, 7, 18);
    assert.equal(start.toISOString(), "2026-08-18T03:00:00.000Z");
  });

  it("formata data e hora no fuso de Brasília", () => {
    const instant = new Date("2026-08-19T01:10:00.000Z");
    assert.equal(formatBrasiliaDate(instant), "18/08/2026");
    assert.match(formatBrasiliaDateTime(instant), /18\/08\/2026/);
    assert.match(formatBrasiliaDateTime(instant), /22:10/);
  });
});
