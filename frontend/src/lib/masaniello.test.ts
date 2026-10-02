import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  masanielloMinCapital,
  masanielloPlanForProfile,
  masanielloPlanSummary,
  masanielloWillContinue,
  newMasanielloCycle,
  normalizeMasanielloCycle,
  normalizeMasanielloProfile,
  simulateMasaniello,
  validMasanielloPlan,
} from "./masaniello.ts";

// Os MESMOS vetores do backend: os dois motores têm que devolver igual.
const vectors = JSON.parse(
  readFileSync(
    new URL("../../../backend/tests/fixtures/masaniello_vectors.json", import.meta.url),
    "utf8",
  ),
) as {
  simulations: {
    name: string;
    input: {
      capital: number;
      operations: number;
      wins: number;
      payout_ref: number;
      results: string[];
      min_entry: number;
      payout_real: number | null;
    };
    expect: Record<string, unknown> & { rows: Record<string, unknown>[] };
  }[];
  plans: {
    input: {
      capital: number;
      operations: number;
      wins: number;
      payout_ref: number;
      min_entry: number;
    };
    expect: Record<string, unknown>;
  }[];
};

const ROW_KEYS = [
  "seq",
  "result",
  "stake",
  "stake_planned",
  "adjusted_to_min",
  "profit",
  "capital_after",
  "hit_rate",
  "errors_left",
] as const;

test("entradas da planilha do dono (100, 38 operações, 14 acertos, payout 1,82)", () => {
  const cycle = simulateMasaniello(100, 38, 14, 82, ["L", "W", "L", "W"]);
  assert.deepEqual(
    cycle.rows.map((row) => row.stake),
    [0.4, 0.58, 0.38, 0.55],
  );
  assert.equal(cycle.target, 100.81);
});

test("simulações batem com os vetores do backend", () => {
  for (const item of vectors.simulations) {
    const { input, expect } = item;
    const cycle = simulateMasaniello(
      input.capital,
      input.operations,
      input.wins,
      input.payout_ref,
      input.results,
      { minEntry: input.min_entry, payoutReal: input.payout_real },
    );
    const got = cycle as unknown as Record<string, unknown>;
    for (const key of [
      "status",
      "end_reason",
      "capital_atual",
      "target",
      "wins",
      "losses",
      "next_stake",
    ]) {
      assert.deepEqual(got[key], expect[key], `${item.name}: ${key}`);
    }
    const rows = cycle.rows.map((row) => {
      const picked: Record<string, unknown> = {};
      for (const key of ROW_KEYS) picked[key] = row[key];
      return picked;
    });
    assert.deepEqual(rows, expect.rows, `${item.name}: linhas`);
  }
});

test("resumos do plano batem com os vetores do backend", () => {
  for (const item of vectors.plans) {
    const { input, expect } = item;
    assert.deepEqual(
      masanielloPlanSummary(
        input.capital,
        input.operations,
        input.wins,
        input.payout_ref,
        input.min_entry,
      ),
      expect,
      JSON.stringify(input),
    );
  }
});

test("erros esgotados perdem o capital inteiro", () => {
  const cycle = simulateMasaniello(100, 10, 4, 80, Array(7).fill("L"), { minEntry: 5 });
  assert.equal(cycle.status, "BUST");
  assert.equal(cycle.capital_atual, 0);
});

test("perfil manda em operações e acertos; personalizado fica na faixa", () => {
  assert.deepEqual(masanielloPlanForProfile("moderado", 99, 1), { operations: 10, wins: 5 });
  assert.deepEqual(masanielloPlanForProfile("personalizado", 500, 500), {
    operations: 100,
    wins: 99,
  });
  assert.deepEqual(masanielloPlanForProfile("personalizado", 12, 5), { operations: 12, wins: 5 });
  assert.equal(normalizeMasanielloProfile("Agressivo"), "agressivo");
  assert.equal(normalizeMasanielloProfile("xyz"), "personalizado");
  assert.equal(normalizeMasanielloProfile(undefined), "conservador");
});

test("plano válido exige ao menos um erro aceito", () => {
  assert.equal(validMasanielloPlan(10, 4), true);
  assert.equal(validMasanielloPlan(10, 10), false);
  assert.equal(validMasanielloPlan(10, 0), false);
  assert.equal(validMasanielloPlan(101, 4), false);
});

test("capital mínimo paga a primeira entrada", () => {
  const minimo = masanielloMinCapital(10, 4, 80, 5);
  assert.equal(minimo, 73.32);
  assert.ok(masanielloPlanSummary(minimo, 10, 4, 80, 5).first_stake >= 5);
});

test("normaliza o ciclo que vem do backend e recusa lixo", () => {
  assert.equal(normalizeMasanielloCycle(null), null);
  assert.equal(normalizeMasanielloCycle({ id: "", n: 10, w: 4 }), null);
  const cycle = normalizeMasanielloCycle({
    id: "c1",
    rev: 3,
    status: "ACTIVE",
    capital_inicial: 100,
    capital_atual: 93.18,
    n: 10,
    w: 4,
    payout_ref: 80,
    min_entry: 5,
    target: 110.58,
    max_errors: 6,
    wins: 0,
    losses: 1,
    next_stake: 10.23,
    pending: { order_id: "9", stake: 10.23, at: "2026-10-02T10:00:00+00:00" },
    rows: [
      {
        seq: 1,
        order_id: "8",
        result: "LOSS",
        stake: 6.82,
        profit: -6.82,
        capital_after: 93.18,
        hit_rate: 0,
        errors_left: 5,
      },
      { result: "QUALQUER" },
    ],
  });
  assert.ok(cycle);
  assert.equal(cycle.rows.length, 1);
  assert.equal(cycle.rows[0].stake_planned, 6.82);
  assert.equal(cycle.pending?.order_id, "9");
  assert.equal(cycle.next_stake, 10.23);
});

test("Iniciar continua o ciclo só com o mesmo plano", () => {
  const cycle = newMasanielloCycle(100, 10, 4, 80, 5);
  const plan = { capital: 100, operations: 10, wins: 4, payoutRef: 80 };
  assert.equal(masanielloWillContinue(cycle, plan), true);
  assert.equal(masanielloWillContinue(cycle, { ...plan, capital: 200 }), false);
  assert.equal(masanielloWillContinue(cycle, { ...plan, wins: 5 }), false);
  assert.equal(masanielloWillContinue({ ...cycle, status: "TARGET_HIT" }, plan), false);
  assert.equal(masanielloWillContinue(null, plan), false);
});
