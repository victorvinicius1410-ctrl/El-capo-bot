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
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(draft.slug)) errors.slug = "Use letras minúsculas, números e hífens.";
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
