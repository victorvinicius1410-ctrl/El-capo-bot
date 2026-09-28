import assert from "node:assert/strict";
import { test } from "node:test";

import { filterEmailDeliveriesByRecipient } from "./emailPresentation.ts";

const items = [
  { id: "1", recipient: "Edielson@Gmail.com" },
  { id: "2", recipient: "maria@hotmail.com" },
  { id: "3", recipient: null },
];

test("busca vazia devolve todas as entregas", () => {
  assert.deepEqual(filterEmailDeliveriesByRecipient(items, "  ").map((i) => i.id), ["1", "2", "3"]);
});

test("busca ignora maiúsculas e acha por parte do e-mail", () => {
  assert.deepEqual(filterEmailDeliveriesByRecipient(items, "edielson@").map((i) => i.id), ["1"]);
  assert.deepEqual(filterEmailDeliveriesByRecipient(items, "HOTMAIL").map((i) => i.id), ["2"]);
});

test("entrega sem destinatário não casa com busca", () => {
  assert.deepEqual(filterEmailDeliveriesByRecipient(items, "gmail").map((i) => i.id), ["1"]);
});
