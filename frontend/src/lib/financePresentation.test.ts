import assert from "node:assert/strict";
import { test } from "node:test";

import {
  billingCycleLabel,
  currencySymbol,
  formatPriceInput,
  parsePriceInput,
  planCopyForCycle,
  slugifyPlanName,
  uniquePlanSlug,
  validatePlanDraft,
  type PlanDraft,
} from "./financePresentation.ts";

function draft(overrides: Partial<PlanDraft> = {}): PlanDraft {
  return {
    name: "Plano Mensal",
    slug: "",
    description: "Assinatura mensal",
    price: 147.9,
    currency: "BRL",
    billing_interval_months: 1,
    features: ["Robô liberado"],
    is_featured: false,
    is_active: false,
    display_order: 1,
    cakto_product_id: "",
    cakto_offer_id: "",
    checkout_url: "",
    ...overrides,
  } as PlanDraft;
}

test("slug sai do nome sem acento nem pontuação", () => {
  assert.equal(slugifyPlanName("Plano Anual — Promoção!"), "plano-anual-promocao");
  assert.equal(slugifyPlanName("  Mensal  "), "mensal");
  assert.equal(slugifyPlanName("***"), "");
});

test("slug repetido ganha sufixo numérico", () => {
  assert.equal(uniquePlanSlug("mensal", []), "mensal");
  assert.equal(uniquePlanSlug("mensal", ["mensal"]), "mensal-2");
  assert.equal(uniquePlanSlug("mensal", ["mensal", "mensal-2"]), "mensal-3");
  assert.equal(uniquePlanSlug("", ["plano"]), "plano-2");
});

test("slug em branco não é mais erro, mas nome sem letras é", () => {
  assert.equal(validatePlanDraft(draft({ slug: "" })).slug, undefined);
  assert.equal(validatePlanDraft(draft({ slug: "Plano Mensal" })).slug !== undefined, true);
  assert.equal(validatePlanDraft(draft({ name: "***", slug: "" })).name !== undefined, true);
});

test("preço aceita o que o brasileiro digita", () => {
  assert.equal(parsePriceInput("1.147,90"), 1147.9);
  assert.equal(parsePriceInput("147,90"), 147.9);
  assert.equal(parsePriceInput("147.90"), 147.9);
  assert.equal(parsePriceInput("R$ 97"), 97);
  assert.equal(Number.isNaN(parsePriceInput("")), true);
  assert.equal(Number.isNaN(parsePriceInput("R$")), true);
});

test("preço volta formatado com duas casas", () => {
  assert.equal(formatPriceInput(1147.9), "1.147,90");
  assert.equal(formatPriceInput(97), "97,00");
  assert.equal(formatPriceInput(Number.NaN), "");
});

test("cada ciclo sugere um texto diferente", () => {
  const mensal = planCopyForCycle(1);
  const trimestral = planCopyForCycle(3);
  const anual = planCopyForCycle(12);
  const descricoes = new Set([mensal, trimestral, planCopyForCycle(6), anual].map((c) => c.description));
  assert.equal(descricoes.size, 4, "descrições não podem se repetir entre ciclos");
  for (const copy of [mensal, trimestral, anual]) {
    assert.ok(copy.description.length > 20);
    assert.ok(copy.features.length >= 4);
  }
  assert.notDeepEqual(mensal.features, anual.features);
});

test("ciclo fora do padrão ainda recebe texto", () => {
  const copy = planCopyForCycle(7);
  assert.match(copy.description, /7 meses/);
  assert.ok(copy.features.some((feature) => feature.includes("7 meses")));
  assert.match(planCopyForCycle(0).description, /1 meses|1 mês/);
});

test("a sugestão é uma cópia, não a lista interna", () => {
  const primeira = planCopyForCycle(1);
  primeira.features.push("editado à mão");
  assert.equal(planCopyForCycle(1).features.includes("editado à mão"), false);
});

test("moeda e ciclo têm rótulo legível", () => {
  assert.equal(currencySymbol("BRL"), "R$");
  assert.equal(currencySymbol("USD"), "USD");
  assert.equal(billingCycleLabel(3), "Trimestral");
  assert.equal(billingCycleLabel(12), "Anual");
  assert.equal(billingCycleLabel(7), "");
});
