import assert from "node:assert/strict";
import { test } from "node:test";

import { newMasanielloCycle, simulateMasaniello } from "./masaniello.ts";
import {
  masanielloCycleBalance,
  masanielloCycleHeadline,
  masanielloFormView,
  masanielloOverlayLine,
  masanielloPayoutRef,
  masanielloRowNote,
} from "./masanielloPresentation.ts";

const BASE = {
  capital: 100,
  profile: "conservador" as const,
  operations: 10,
  wins: 4,
  payoutRef: 80,
  currency: "BRL",
  balance: 500,
};

// O Intl separa "R$" do número com espaço não separável.
const plain = (text: string) => text.split(String.fromCharCode(160)).join(" ");

test("meta e limite aparecem em número e em valor ao mesmo tempo", () => {
  const view = masanielloFormView(BASE);
  assert.equal(plain(view.targetLabel), "4 acertos = +R$ 10,58");
  assert.equal(plain(view.limitLabel), "7 erros = −R$ 100,00");
  assert.equal(view.error, null);
  assert.equal(view.summary.first_stake, 6.82);
  assert.equal(view.summary.max_stake, 61.43);
});

test("avisa quando parte das entradas sobe para o mínimo da corretora", () => {
  const view = masanielloFormView(BASE);
  assert.equal(view.notices.length, 1);
  assert.match(plain(view.notices[0]), /R\$ 5,00/);
  assert.match(plain(view.notices[0]), /R\$ 1\.055,99/);
  assert.deepEqual(masanielloFormView({ ...BASE, capital: 2000, balance: 5000 }).notices, []);
});

test("capital abaixo do mínimo do plano bloqueia", () => {
  const view = masanielloFormView({ ...BASE, capital: 50 });
  assert.match(plain(view.error ?? ""), /Capital mínimo para este plano: R\$ 73,32/);
});

test("capital maior que o saldo bloqueia só quando vai abrir ciclo novo", () => {
  assert.match(
    plain(masanielloFormView({ ...BASE, balance: 80 }).error ?? ""),
    /Seu saldo \(R\$ 80,00\) é menor que o capital do ciclo/,
  );
  assert.equal(masanielloFormView({ ...BASE, balance: 80, continuing: true }).error, null);
  assert.match(masanielloFormView({ ...BASE, balance: 0 }).error ?? "", /sem saldo/);
  assert.equal(masanielloFormView({ ...BASE, balance: null }).error, null);
});

test("conta em dólar usa o mínimo de US$ 1", () => {
  const view = masanielloFormView({ ...BASE, capital: 20, currency: "USD", balance: 50 });
  assert.equal(view.minEntry, 1);
  assert.equal(view.error, null);
  assert.equal(view.summary.min_capital, 14.67);
});

test("perfil manda no plano mesmo com números soltos no formulário", () => {
  const view = masanielloFormView({ ...BASE, profile: "agressivo", operations: 99, wins: 1 });
  assert.equal(view.operations, 10);
  assert.equal(view.wins, 6);
  assert.equal(plain(view.limitLabel), "5 erros = −R$ 100,00");
});

test("payout de referência cai em 80 quando o servidor não mandou", () => {
  assert.equal(masanielloPayoutRef(undefined), 80);
  assert.equal(masanielloPayoutRef(0), 80);
  assert.equal(masanielloPayoutRef(85), 85);
});

test("título do ciclo conforme o estado", () => {
  const ativo = simulateMasaniello(100, 10, 4, 80, ["W", "L"], { minEntry: 5 });
  assert.equal(masanielloCycleHeadline(ativo, "BRL").title, "Ciclo em andamento");
  assert.match(
    masanielloCycleHeadline(ativo, "BRL").detail,
    /1 de 4 acertos · 1 erro de 6 aceitos/,
  );

  const meta = simulateMasaniello(100, 10, 4, 80, ["W", "W", "W", "W"], { minEntry: 5 });
  const metaTitulo = masanielloCycleHeadline(meta, "BRL");
  assert.equal(metaTitulo.title, "Meta do ciclo batida");
  assert.equal(metaTitulo.tone, "positive");

  const perdido = simulateMasaniello(100, 10, 4, 80, Array(7).fill("L"), { minEntry: 5 });
  const perdidoTitulo = masanielloCycleHeadline(perdido, "BRL");
  assert.match(perdidoTitulo.title, /capital do ciclo acabou/);
  assert.equal(perdidoTitulo.tone, "negative");
  assert.match(plain(perdidoTitulo.detail), /-R\$ 100,00/);

  const pendente = {
    ...newMasanielloCycle(100, 10, 4, 80, 5),
    pending: {
      order_id: "1",
      stake: 6.82,
      stake_planned: 6.82,
      adjusted_to_min: false,
      asset: null,
      payout: null,
      at: null,
      unknown: true,
    },
  };
  assert.match(masanielloCycleHeadline(pendente, "BRL").title, /Aguardando o resultado/);
});

test("balanço do ciclo em valor e percentual", () => {
  const cycle = simulateMasaniello(100, 10, 4, 80, ["L"], { minEntry: 5 });
  assert.deepEqual(masanielloCycleBalance(cycle), { amount: -6.82, percent: -6.8 });
});

test("observação de cada linha", () => {
  const cycle = simulateMasaniello(100, 10, 4, 80, ["W", "D", "W", "W", "W"], { minEntry: 5 });
  const notes = cycle.rows.map((row) => masanielloRowNote(row, cycle));
  assert.equal(notes[0], "6 erros ainda aceitos");
  assert.equal(notes[1], "Empate: valor devolvido, não conta");
  assert.match(notes[2], /^Entrada ajustada ao mínimo · 6 erros/);
  assert.equal(notes[4], "Meta batida");

  const perdido = simulateMasaniello(100, 10, 4, 80, Array(7).fill("L"), { minEntry: 5 });
  assert.equal(masanielloRowNote(perdido.rows[6], perdido), "Erros esgotados");
  assert.equal(masanielloRowNote(perdido.rows[5], perdido), "0 erros ainda aceitos");
});

test("linha do overlay só com ciclo em andamento", () => {
  const cycle = simulateMasaniello(100, 10, 4, 80, ["W", "L"], { minEntry: 5 });
  assert.match(
    plain(masanielloOverlayLine(cycle, "BRL") ?? ""),
    /^Ciclo 1\/4 acertos · 1\/6 erros · próxima R\$ /,
  );
  const meta = simulateMasaniello(100, 10, 4, 80, ["W", "W", "W", "W"], { minEntry: 5 });
  assert.equal(masanielloOverlayLine(meta, "BRL"), null);
  assert.equal(masanielloOverlayLine(null, "BRL"), null);
});
