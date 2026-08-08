import type { OutgoingWebhookEvent, WebhookDelivery, WebhookEndpoint } from "./api";

export const OUTGOING_WEBHOOK_EVENTS = [
  "client.created", "client.updated", "payment.approved", "payment.refused",
  "subscription.created", "subscription.canceled", "robot.trade.finished",
] as const satisfies readonly OutgoingWebhookEvent[];

export const WEBHOOK_EVENT_LABELS: Record<string, string> = {
  "client.created": "Cliente criado",
  "client.updated": "Cliente atualizado",
  "payment.approved": "Pagamento aprovado",
  "payment.refused": "Pagamento recusado",
  "subscription.created": "Assinatura criada",
  "subscription.canceled": "Assinatura cancelada",
  "robot.trade.finished": "Operação finalizada",
};

/** Traduz o estado técnico de uma entrega de webhook. */
export function deliveryStatusLabel(status: string): string {
  return ({ delivered: "Entregue", success: "Entregue", failed: "Falhou", pending: "Pendente", retrying: "Reenviando" } as Record<string, string>)[status] ?? status;
}

/** Normaliza listas de destinos e entregas retornadas pela API. */
export function normalizeWebhookCollections(
  endpoints: WebhookEndpoint[] | { items?: WebhookEndpoint[] } | undefined,
  deliveries: WebhookDelivery[] | { items?: WebhookDelivery[] } | undefined,
): { endpoints: WebhookEndpoint[]; deliveries: WebhookDelivery[] } {
  return { endpoints: list(endpoints), deliveries: list(deliveries) };
}

/** Valida nome, URL HTTPS e eventos de um destino. */
export function validateWebhookDraft(draft: {
  name: string;
  url: string;
  subscribed_events: OutgoingWebhookEvent[];
}): Partial<Record<"name" | "url" | "subscribed_events", string>> {
  const errors: Partial<Record<"name" | "url" | "subscribed_events", string>> = {};
  if (!draft.name.trim()) errors.name = "Informe o nome.";
  try {
    if (new URL(draft.url).protocol !== "https:") errors.url = "Use uma URL HTTPS.";
  } catch {
    errors.url = "Informe uma URL válida.";
  }
  if (!draft.subscribed_events.length) errors.subscribed_events = "Selecione ao menos um evento.";
  return errors;
}

function list<T>(value: T[] | { items?: T[] } | undefined): T[] {
  return Array.isArray(value) ? value : value?.items ?? [];
}
