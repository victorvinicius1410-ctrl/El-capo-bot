import type { AdminFinanceMetrics, BillingPlan } from "./api";

export const CAKTO_ESSENTIAL_EVENTS = [
  "purchase_approved",
  "purchase_refused",
  "refund",
  "chargeback",
  "subscription_created",
  "subscription_canceled",
  "subscription_renewed",
  "subscription_renewal_refused",
] as const;

export interface PlanDraft extends Omit<BillingPlan, "id"> {}

/** Moedas aceitas pelo backend (hoje só BRL: ONLY_BRL_SUPPORTED). */
export const CURRENCY_OPTIONS = [
  { code: "BRL", label: "Real brasileiro (R$)", symbol: "R$", locale: "pt-BR" },
] as const;

/** Ciclos de cobrança oferecidos como atalho no formulário. */
export const BILLING_CYCLE_OPTIONS = [
  { months: 1, label: "Mensal" },
  { months: 3, label: "Trimestral" },
  { months: 6, label: "Semestral" },
  { months: 12, label: "Anual" },
] as const;

/** Símbolo da moeda escolhida, com fallback para o código. */
export function currencySymbol(code: string): string {
  return CURRENCY_OPTIONS.find((option) => option.code === code)?.symbol ?? code;
}

/** Nome do ciclo quando ele é um dos atalhos; vazio quando é personalizado. */
export function billingCycleLabel(months: number): string {
  return BILLING_CYCLE_OPTIONS.find((option) => option.months === months)?.label ?? "";
}

/**
 * Deriva um slug válido a partir do nome do plano.
 *
 * Remove acentos, troca o que não for letra ou número por hífen e corta as
 * pontas. Devolve string vazia quando o nome não tem nenhum caractere útil.
 */
export function slugifyPlanName(name: string): string {
  return name
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/**
 * Garante um slug livre, acrescentando sufixo numérico quando já existe.
 *
 * O backend recusa slug repetido (PLAN_SLUG_ALREADY_EXISTS); como o campo
 * passou a ser opcional na tela, a desambiguação acontece aqui.
 */
export function uniquePlanSlug(base: string, taken: Iterable<string>): string {
  const used = new Set([...taken].map((value) => value.trim().toLowerCase()));
  const seed = base || "plano";
  if (!used.has(seed)) return seed;
  let suffix = 2;
  while (used.has(`${seed}-${suffix}`)) suffix += 1;
  return `${seed}-${suffix}`;
}

/**
 * Lê um preço digitado com máscara brasileira.
 *
 * Aceita "1.147,90", "1147,90" e "1147.90". Devolve NaN quando não há dígito,
 * para o campo poder ficar vazio enquanto o usuário digita.
 */
export function parsePriceInput(value: string): number {
  const cleaned = value.replace(/[^\d,.-]/g, "");
  if (!/\d/.test(cleaned)) return Number.NaN;
  const normalized = cleaned.includes(",")
    ? cleaned.replace(/\./g, "").replace(",", ".")
    : cleaned;
  return Number(normalized);
}

/** Formata o preço para o campo, no padrão da moeda escolhida. */
export function formatPriceInput(value: number, currency = "BRL"): string {
  if (!Number.isFinite(value)) return "";
  const locale = CURRENCY_OPTIONS.find((option) => option.code === currency)?.locale ?? "pt-BR";
  return value.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** Calcula o desconto de uma oferta contra o preço mensal de referência. */
export function planDiscountPercent(price: number, months: number, monthlyReference: number): number {
  if (months <= 1 || price < 0 || monthlyReference <= 0) return 0;
  return Math.max(0, Math.round((1 - price / (monthlyReference * months)) * 100));
}

/** Confirma que o checkout pertence ao domínio oficial da Cakto. */
export function isOfficialCaktoCheckoutUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "pay.cakto.com.br";
  } catch {
    return false;
  }
}

/** Normaliza respostas que podem ser uma lista ou uma página. */
export function normalizeListPayload<T>(value: T[] | { items?: T[] } | unknown): T[] {
  if (Array.isArray(value)) return value as T[];
  if (isRecord(value) && Array.isArray(value.items)) return value.items as T[];
  return [];
}

/** Valida os campos editáveis de uma oferta financeira. */
export function validatePlanDraft(
  draft: PlanDraft,
  options: { requireManualCaktoLink?: boolean } = {},
): Partial<Record<keyof PlanDraft, string>> {
  const errors: Partial<Record<keyof PlanDraft, string>> = {};
  if (!draft.name.trim()) errors.name = "Informe o nome do plano.";
  if (!draft.description.trim()) errors.description = "Informe a descrição do plano.";
  if (!draft.features.some((feature) => feature.trim())) errors.features = "Informe pelo menos um benefício.";
  // Slug é opcional na tela: quando fica em branco o formulário deriva do nome.
  if (draft.slug.trim() && !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(draft.slug.trim())) {
    errors.slug = "Use letras minúsculas, números e hífens.";
  }
  if (!draft.slug.trim() && !slugifyPlanName(draft.name)) {
    errors.name = "Informe um nome com letras ou números.";
  }
  if (!Number.isFinite(draft.price) || draft.price < 0) errors.price = "Informe um preço válido.";
  if (draft.price > 0 && draft.price < 5) errors.price = "O preço mínimo na Cakto é R$ 5,00.";
  if (!Number.isInteger(draft.billing_interval_months) || draft.billing_interval_months < 1) {
    errors.billing_interval_months = "O ciclo deve ter pelo menos um mês.";
  }
  if (!/^[A-Z]{3}$/.test(draft.currency)) errors.currency = "Use uma moeda ISO de três letras.";
  if (!Number.isInteger(draft.display_order) || draft.display_order < 0) errors.display_order = "Use uma ordem inteira igual ou maior que zero.";
  if (draft.checkout_url.trim() && !isOfficialCaktoCheckoutUrl(draft.checkout_url)) {
    errors.checkout_url = "Use uma URL HTTPS oficial da Cakto.";
  }
  if (options.requireManualCaktoLink && draft.is_active) {
    // Edição/migração: oferta ativa precisa do vínculo completo.
    // Criação nova usa auto-provisionamento (produto do .env) e não passa por aqui.
    if (!draft.cakto_product_id.trim()) errors.cakto_product_id = "Informe o produto Cakto.";
    if (!draft.cakto_offer_id.trim()) errors.cakto_offer_id = "Informe a oferta Cakto.";
    if (!draft.checkout_url.trim()) errors.checkout_url = "Informe o checkout Cakto.";
  }
  return errors;
}

/** Resume se as ofertas apontam para um único produto Cakto. */
export function summarizeCaktoProduct(offers: BillingPlan[]): {
  productId: string;
  hasConflict: boolean;
} {
  const ids = new Set(offers.map((offer) => offer.cakto_product_id?.trim() ?? "").filter(Boolean));
  return { productId: ids.size === 1 ? [...ids][0] : "", hasConflict: ids.size > 1 };
}

/** Normaliza coleções de métricas financeiras retornadas pelo backend. */
export function normalizeFinanceCollections(value: AdminFinanceMetrics | unknown): {
  breakdowns: Record<string, unknown[]>;
  dailySeries: Array<Record<string, unknown>>;
} {
  const source = isRecord(value) ? value : {};
  const breakdowns: Record<string, unknown[]> = {};
  if (isRecord(source.breakdowns)) {
    for (const [key, entries] of Object.entries(source.breakdowns)) {
      breakdowns[key] = Array.isArray(entries) ? entries : [];
    }
  }
  return {
    breakdowns,
    dailySeries: Array.isArray(source.daily_series) ? source.daily_series as Array<Record<string, unknown>> : [],
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
