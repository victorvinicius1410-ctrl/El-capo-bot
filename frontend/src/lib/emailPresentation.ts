export const EMAIL_EVENT_LABELS: Record<string, string> = {
  "purchase.completed": "Compra / boas-vindas",
  "purchase.existing_account": "Compra (já tinha conta)",
  "subscription.renewed": "Assinatura renovada",
  "subscription.canceled": "Assinatura cancelada",
  "payment.refunded": "Reembolso",
  "payment.chargeback": "Chargeback",
  "subscription.payment_failed": "Falha no pagamento",
  "trial.started": "Teste iniciado",
  "trial.ended": "Teste encerrado",
  "user.password_recovery_requested": "Recuperação de senha",
};

/** Variáveis oficiais substituídas pelo servidor com {{nome}}. */
export const EMAIL_TEMPLATE_VARIABLES = [
  { name: "customer_name", description: "Nome do cliente" },
  { name: "customer_email", description: "E-mail do cliente" },
  { name: "plan_name", description: "Nome da oferta/plano" },
  { name: "amount", description: "Valor cobrado (número cru)" },
  { name: "currency", description: "Moeda (ex.: BRL)" },
  { name: "amount_display", description: "Valor já formatado (ex.: R$ 147,90)" },
  { name: "plan_name_display", description: "Plano com fallback quando o evento não traz plano" },
  { name: "first_access_url", description: "Link para definir senha no 1º acesso" },
  { name: "first_access_cta_url", description: "Link de 1º acesso com fallback para o login" },
  { name: "recovery_url", description: "Link de recuperação de senha" },
  { name: "expires_at", description: "Expiração do link (ISO)" },
  { name: "expires_at_br", description: "Expiração em pt-BR (ex.: 15/09/2026 às 23:59)" },
  { name: "expires_in_seconds", description: "Segundos até expirar" },
  { name: "expires_in_human", description: "Validade por extenso (ex.: 1 hora)" },
  { name: "login_url", description: "URL de login do painel" },
  { name: "company_name", description: "Nome da empresa" },
  { name: "event_type", description: "Código do evento" },
  { name: "checkout_url", description: "URL de checkout Cakto" },
  { name: "support_url", description: "URL de suporte" },
  { name: "name", description: "Alias de customer_name" },
  { name: "email", description: "Alias de customer_email" },
  { name: "reset_url", description: "Alias de recovery_url / first_access_url" },
] as const;

/** Traduz o estado técnico de uma entrega de email. */
export function emailDeliveryStatusLabel(status: string): string {
  return (
    (
      {
        sent: "Enviado",
        delivered: "Entregue",
        failed: "Falhou",
        pending: "Pendente",
        retrying: "Tentando de novo",
      } as Record<string, string>
    )[status] ?? status
  );
}

/** Explica códigos de erro técnicos de entrega para o admin. */
export function emailDeliveryErrorLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  const labels: Record<string, string> = {
    DISPATCH_TIMEOUT: "Não chegou a ser enviado (fila/worker)",
    PROVIDER_NOT_CONFIGURED: "Provedor SMTP/Resend não configurado",
    PROVIDER_REJECTED: "Provedor rejeitou o envio",
    PROVIDER_AUTH_FAILED: "Falha de autenticação SMTP/Resend",
    PROVIDER_UNAVAILABLE: "Provedor indisponível",
    QUEUE_FAILED: "Falha ao enfileirar",
    EMAILS_DISABLED: "Envio desativado no servidor",
    RECIPIENT_INVALID: "Destinatário inválido",
  };
  return labels[code] ?? code;
}

/** Substitui variáveis {{nome}} no preview local. */
export function renderEmailPreview(template: string, variables: Record<string, string>): string {
  return template.replace(/\{\{\s*([\w.]+)\s*\}\}/g, (_match, name: string) => variables[name] ?? "");
}

/** Fornece dados fictícios seguros para o preview administrativo. */
export function sampleEmailVariables(): Record<string, string> {
  return {
    customer_name: "Cliente ElCapo",
    customer_email: "cliente@exemplo.com",
    name: "Cliente ElCapo",
    email: "cliente@exemplo.com",
    plan_name: "Plano Mensal",
    plan_name_display: "Plano Mensal",
    amount: "147.90",
    currency: "BRL",
    amount_display: "R$ 147,90",
    first_access_url: "https://app.elcapobot.online/reset-password?token=exemplo",
    first_access_cta_url: "https://app.elcapobot.online/reset-password?token=exemplo",
    recovery_url: "https://app.elcapobot.online/reset-password?token=exemplo",
    reset_url: "https://app.elcapobot.online/reset-password?token=exemplo",
    expires_at: "2026-08-07T12:00:00+00:00",
    expires_at_br: "07/08/2026 às 09:00",
    expires_in_seconds: "3600",
    expires_in_human: "1 hora",
    login_url: "https://app.elcapobot.online/login",
    company_name: "ElCapo AutoBot",
    event_type: "purchase.completed",
    checkout_url: "https://pay.cakto.com.br/exemplo",
    support_url: "https://wa.me/",
  };
}

/** Valida assunto e HTML antes do envio ao backend. */
export function validateEmailTemplateDraft(draft: {
  subject: string;
  html_body: string;
}): string | null {
  if (!draft.subject.trim()) return "Informe o assunto do email.";
  if (!draft.html_body.trim()) return "Informe o HTML do email.";
  return null;
}

/** Rótulo amigável do evento; fallback para o código técnico. */
export function emailEventLabel(eventType: string): string {
  return EMAIL_EVENT_LABELS[eventType] ?? eventType;
}
