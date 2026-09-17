import { resolveApiBaseUrl } from "./apiBaseUrl";
import { withAuthRefreshRetry } from "./authSessionKeepAlive";
import {
  clearSessionIdentityCache,
  readSessionIdentityCache,
  sessionIdentityCacheTtlMs,
  writeSessionIdentityCache,
} from "./sessionIdentityCache";
import { clearAuthSnapshot, refreshAuthSession, renewAuthSessionCookies } from "./useAuth";

export const apiConfig = {
  BASE_URL: resolveApiBaseUrl(import.meta.env.VITE_API_BASE_URL),
  hasKey: Boolean(import.meta.env.VITE_PANEL_API_KEY),
} as const;

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly code?: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export type ApiResponse<T> =
  | { ok: true; data: T; error?: never; code?: never; status?: number }
  | { ok: false; error: string; code?: string; status?: number; data?: never };

export type AccountType = "trial" | "client" | "marketing";
export type ClientSegment = "active" | "trial" | "marketing" | "inactive" | "pending";
export type PaymentStatus = "pending" | "paid" | "failed" | "refunded" | "overdue" | "not_required";
export type ApprovalStatus = "pending" | "approved" | "rejected";
export type FeedbackStatus = "pending" | "approved" | "rejected" | "archived";
export type EmailEventType = string;
export type AdminPermission = string;
export type OutgoingWebhookEvent = string;

export interface Page<T> {
  items: T[];
  has_more: boolean;
  next_offset?: number;
}

export interface FeedbackItem {
  id: string;
  author_name: string;
  description: string;
  result?: string | null;
  content: string;
  video_url?: string | null;
  rating?: number | null;
  status: FeedbackStatus;
  created_at: string;
  author_email?: string;
  admin_note?: string | null;
  user_id?: string | null;
  rejection_expires_at?: string | null;
}

export interface BillingPlan {
  id: string;
  name: string;
  slug: string;
  description: string;
  price: number;
  currency: string;
  billing_interval_months: number;
  features: string[];
  checkout_url: string;
  cakto_product_id: string;
  cakto_offer_id: string;
  display_order: number;
  is_active: boolean;
  is_featured: boolean;
}

export type BillingPlanPayload =
  Omit<BillingPlan, "id" | "cakto_product_id" | "cakto_offer_id" | "checkout_url"> & {
    cakto_product_id: string | null;
    cakto_offer_id: string | null;
    checkout_url: string | null;
  };

export interface BillingHistoryItem {
  id: string;
  plan_name?: string;
  amount: number;
  currency: string;
  status: PaymentStatus;
  created_at: string;
  plan_slug?: string;
  occurred_at: string;
  event: string;
}

export interface AdminClient {
  id: string;
  user_id?: string;
  email: string;
  name?: string | null;
  account_type: AccountType;
  payment_status?: PaymentStatus;
  plan_id?: string | null;
  active?: boolean;
  expires_at?: string | null;
  created_at: string;
  phone?: string | null;
  trader_id?: string | null;
  plan_name?: string | null;
  marketing_win_rate?: number | null;
  deleted_at?: string | null;
  grant_access?: boolean;
  approval_status?: ApprovalStatus;
}

export interface AdminClientPayload {
  email: string;
  name?: string;
  password?: string;
  account_type: AccountType;
  payment_status?: PaymentStatus;
  plan_id?: string | null;
  phone?: string | null;
  trader_id?: string | null;
  trial_days?: number | null;
  marketing_mode?: string | null;
  marketing_win_rate?: number | null;
}

export interface AdminAccessRecord {
  id: string;
  user_id?: string;
  email: string;
  name: string;
  role?: string;
  permissions: AdminPermission[];
  active?: boolean;
  role_id: string;
  job_title: string;
  manageable_role_ids?: string[] | null;
}

export interface AdminAccessPayload {
  email: string;
  name?: string;
  password?: string;
  role?: string;
  permissions: AdminPermission[];
  job_title?: string;
  manageable_role_ids?: string[] | null;
}

export interface AdminDashboardAssetRanking {
  asset: string;
  operations: number;
  wins: number;
  losses: number;
  accuracy: number;
}

export interface AdminDashboardUserRanking {
  user_id: string;
  name: string;
  email: string;
  operations: number;
  wins: number;
  win_rate: number;
  profit: number;
}

export interface AdminDashboardHourlyResult {
  hour: number;
  hour_label: string;
  operations: number;
  wins: number;
  losses: number;
  win_rate: number;
  profit: number;
}

export interface AdminDashboardOperations {
  total: number;
  wins: number;
  losses: number;
  win_rate: number;
  profit: number;
}

export interface AdminDashboardData {
  active_clients: number;
  inactive_clients: number;
  trial_clients: number;
  new_clients: number;
  revenue: number;
  revenue_by_currency: Record<string, number>;
  new_client_revenue: number;
  new_client_revenue_by_currency: Record<string, number>;
  operations: AdminDashboardOperations;
  hourly_results: AdminDashboardHourlyResult[];
  top_winners: AdminDashboardUserRanking[];
  top_losers: AdminDashboardUserRanking[];
  most_accurate_assets: AdminDashboardAssetRanking[];
  least_accurate_assets: AdminDashboardAssetRanking[];
}

export interface AdminFinanceMetrics {
  gross_revenue: number;
  net_revenue: number;
  refunds: number;
  chargebacks: number;
  active_subscriptions: number;
  pending_payments: number;
  approved_payments: number;
  refused_payments: number;
  canceled_subscriptions: number;
  mrr: number;
  arr: number;
  average_ticket: number;
  approval_rate: number;
  churn_rate: number;
  period_start: string;
  period_end: string;
  period_days: number;
  breakdowns?: Record<string, unknown[]>;
  daily_series?: Array<Record<string, unknown>>;
}

export interface WebhookEndpoint {
  id: string;
  name: string;
  url: string;
  description?: string | null;
  subscribed_events: OutgoingWebhookEvent[];
  is_active: boolean;
  created_at?: string;
}

export interface CreatedWebhookEndpoint extends WebhookEndpoint {
  secret?: string;
  signing_secret: string;
}

export interface WebhookCatalogItem {
  event: OutgoingWebhookEvent;
  event_type: OutgoingWebhookEvent;
  name?: string;
  description?: string;
  payload?: Record<string, unknown>;
  example?: Record<string, unknown>;
}

export interface EmailTemplate {
  id: string;
  event_type: EmailEventType;
  subject: string;
  html_body: string;
  is_enabled: boolean;
  updated_at?: string;
}

export interface EmailDelivery {
  id: string;
  event_type: EmailEventType;
  recipient?: string;
  subject: string;
  status: string;
  created_at: string;
  error?: string | null;
  attempt_count?: number;
  last_error_code?: string | null;
}

export interface WebhookDelivery {
  id: string;
  event_id: string;
  endpoint_id: string;
  status: string;
  attempt_count: number;
  response_status: number | null;
  latency_ms: number | null;
  created_at: string;
}

export interface FinanceSettings {
  provider?: string;
  webhook_configured: boolean;
  api_configured: boolean;
  offers_provisioning_configured?: boolean;
  default_product_id?: string | null;
  webhook_url: string;
  enabled_events: string[];
  last_event_at: string | null;
  last_reconciled_at: string | null;
}

export interface EmailSettings {
  provider: string;
  enabled: boolean;
  from_configured: boolean;
  smtp_configured?: boolean;
  available_variables?: string[];
  variable_descriptions?: Record<string, string>;
  events?: string[];
}

function requestId(): string {
  return crypto.randomUUID();
}

function errorMessage(value: unknown, fallback: string): string {
  return typeof value === "string" && value ? value : fallback;
}

const KNOWN_ERROR_MESSAGES: Record<string, string> = {
  INVALID_API_KEY: "Erro de configuração da API",
  SESSION_NOT_FOUND: "Conta Bullex desconectada. Clique em Conectar Bullex.",
  SESSION_DISCONNECTED: "Conta Bullex desconectada. Clique em Conectar Bullex.",
  invalid_credentials: "Email ou senha Bullex inválidos",
  BULLEX_TEMPORARY_UNAVAILABLE:
    "A corretora está temporariamente indisponível. Aguarde alguns segundos e tente de novo.",
  BULLEX_REQUESTS_LIMIT_EXCEEDED:
    "A Bullex limitou as conexões deste servidor. Aguarde cerca de 60 minutos e tente de novo.",
  BULLEX_NOT_CONNECTED:
    "Conta Bullex desconectada ou sessão expirada. Reconecte em Configurações → Conta Corretora e tente de novo.",
  REAL_MODE_NOT_CONFIRMED: "Não foi possível confirmar a conta REAL. Tente reconectar a Bullex.",
  REAL_BALANCE_NOT_DETECTED:
    "Não foi possível confirmar o saldo REAL agora. Aguarde alguns segundos e tente de novo, ou reconecte a Bullex.",
  INSUFFICIENT_BALANCE:
    "Saldo insuficiente. Faça um depósito na BullEx ou reduza o valor da entrada.",
  INSUFFICIENT_FUNDS:
    "Saldo insuficiente para a entrada. Deposite na BullEx ou reduza o valor da entrada.",
  RESET_CYCLE_REQUIRED:
    "Stop Win ou Stop Loss atingido. Clique em Reiniciar placar e depois em Iniciar Operação.",
  STOP_WIN_HIT:
    "Stop Win atingido. Clique em Reiniciar placar para liberar uma nova operação.",
  STOP_LOSS_HIT:
    "Stop Loss atingido. Clique em Reiniciar placar para liberar uma nova operação.",
  ACCESS_INACTIVE:
    "Seu acesso está inativo ou aguardando aprovação. Regularize no Financeiro ou aguarde a liberação.",
  SIMULATED_TRADE_NOT_FOUND:
    "Operação já removida ou não encontrada. Atualize o histórico e tente de novo.",
};

/** Traduz códigos conhecidos da API para mensagens amigáveis. */
export function describeApiErrorCode(code: string | undefined, fallback = "Erro inesperado"): string {
  if (!code) return fallback;
  if (KNOWN_ERROR_MESSAGES[code]) return KNOWN_ERROR_MESSAGES[code];
  return fallback !== "Erro inesperado" ? fallback : code;
}

export function isKnownApiErrorCode(code: string | undefined | null): boolean {
  return Boolean(code && code in KNOWN_ERROR_MESSAGES);
}

const PANEL_API_KEY = import.meta.env.VITE_PANEL_API_KEY ?? "";

async function readSessionIdentityOnce(): Promise<{ userId: string; email: string | null } | null> {
  if (!apiConfig.BASE_URL) return null;
  try {
    const response = await fetch(`${apiConfig.BASE_URL}/auth/session`, {
      credentials: "include",
      headers: { "x-request-id": requestId() },
    });
    if (!response.ok) return null;
    const body = (await response.json()) as {
      ok?: boolean;
      data?: { authenticated?: boolean; user?: { id?: string | number; email?: string | null } };
    };
    if (!body?.ok || !body.data?.authenticated || body.data.user?.id == null) return null;
    return {
      userId: String(body.data.user.id),
      email: body.data.user.email ? String(body.data.user.email) : null,
    };
  } catch {
    return null;
  }
}

/**
 * Lê a identidade da sessão; se o access expirou, tenta POST /auth/refresh uma vez.
 *
 * O GET /auth/session já renova em silêncio no backend; o retry cobre corrida
 * entre Max-Age do cookie e a primeira leitura falha.
 *
 * Cache em memória (TTL curto) evita um round-trip de `/auth/session` em toda
 * `apiRequest` — crítico na navegação admin e no polling do robô.
 */
async function getSessionIdentity(): Promise<{ userId: string; email: string | null } | null> {
  const cached = readSessionIdentityCache();
  if (cached !== undefined) return cached;
  const identity = await withAuthRefreshRetry(readSessionIdentityOnce, renewAuthSessionCookies);
  writeSessionIdentityCache(identity, sessionIdentityCacheTtlMs());
  return identity;
}

function shouldAttachIdentityHeaders(path: string): boolean {
  return !path.startsWith("/admin/") && !path.startsWith("/marketing-simulation/");
}

/**
 * Executa uma chamada autenticada ao backend, validando a sessão por cookie e
 * anexando os cabeçalhos de identidade esperados pela API.
 */
export async function apiRequest<T = unknown>(
  path: string,
  init: RequestInit = {},
  expectedUserId?: string,
): Promise<ApiResponse<T>> {
  if (!apiConfig.BASE_URL) {
    return { ok: false, error: "VITE_API_BASE_URL não configurada", code: "NO_BACKEND" };
  }
  const identity = await getSessionIdentity();
  if (!identity) return { ok: false, error: "Não autenticado", code: "NO_AUTH" };
  if (expectedUserId && expectedUserId !== identity.userId) {
    return { ok: false, error: "Sessão de usuário alterada", code: "AUTH_USER_CHANGED" };
  }
  try {
    const attachIdentity = shouldAttachIdentityHeaders(path);
    const response = await fetch(`${apiConfig.BASE_URL}${path}`, {
      credentials: "include",
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init.headers ?? {}),
        ...(attachIdentity
          ? {
              "x-api-key": PANEL_API_KEY,
              "x-user-id": identity.userId,
              ...(identity.email ? { "x-user-email": identity.email } : {}),
            }
          : {}),
        "x-request-id": requestId(),
      },
    });
    const text = await response.text();
    const body = text ? (JSON.parse(text) as Record<string, unknown>) : {};
    const nested = typeof body.error === "object" && body.error
      ? body.error as Record<string, unknown>
      : undefined;
    if (body.ok === false || !response.ok) {
      if (response.status === 401) clearSessionIdentityCache();
      const code = errorMessage(body.code ?? nested?.code ?? body.error, "") || undefined;
      return {
        ok: false,
        error: describeApiErrorCode(
          code,
          errorMessage(body.message ?? nested?.message ?? body.error, `Erro ${response.status}`),
        ),
        code,
        status: response.status,
      };
    }
    const data = body.ok === true && "data" in body ? body.data : body;
    return { ok: true, data: data as T, status: response.status };
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      return { ok: false, error: "Tempo esgotado ao conectar. Tente novamente.", code: "TIMEOUT" };
    }
    const raw = error instanceof Error ? error.message : "Erro de rede";
    // "Failed to fetch" costuma mascarar 500/CORS; mensagem amigável em PT.
    const message =
      raw === "Failed to fetch" || raw === "NetworkError when attempting to fetch resource."
        ? "Falha de comunicação com a API. Tente novamente."
        : raw;
    return {
      ok: false,
      error: message,
      code: "NETWORK_ERROR",
    };
  }
}

/** Autentica com email/senha criando o cookie de sessão HTTP-only. */
export async function loginWithPassword(
  email: string,
  password: string,
): Promise<ApiResponse<{ id: string; email?: string }>> {
  if (!apiConfig.BASE_URL) {
    return { ok: false, error: "VITE_API_BASE_URL não configurada", code: "NO_BACKEND" };
  }
  // Trim nas bordas evita 401 por espaço de copy/paste/autofill.
  const cleanEmail = email.trim();
  const cleanPassword = password.trim();
  try {
    const response = await fetch(`${apiConfig.BASE_URL}/auth/login`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", "x-request-id": requestId() },
      body: JSON.stringify({ email: cleanEmail, password: cleanPassword }),
    });
    const body = (await response.json().catch(() => ({}))) as {
      ok?: boolean;
      data?: { user?: { id: string; email?: string } };
      error?: { message?: string; code?: string };
    };
    if (!response.ok || body?.ok === false) {
      return {
        ok: false,
        error: body?.error?.message ?? "Email ou senha inválidos",
        code: body?.error?.code ?? "INVALID_CREDENTIALS",
        status: response.status,
      };
    }
    clearSessionIdentityCache();
    await refreshAuthSession();
    return { ok: true, data: body.data?.user as { id: string; email?: string } };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "Erro de rede" };
  }
}

/** Cadastro público de lead (fica pendente até aprovação ou compra de plano). */
export async function registerWithPassword(payload: {
  name: string;
  email: string;
  password: string;
  phone?: string;
}): Promise<
  ApiResponse<{
    id: string;
    email?: string;
    approval_status?: ApprovalStatus;
    message?: string;
  }>
> {
  if (!apiConfig.BASE_URL) {
    return { ok: false, error: "VITE_API_BASE_URL não configurada", code: "NO_BACKEND" };
  }
  const cleanName = payload.name.trim();
  const cleanEmail = payload.email.trim();
  const cleanPassword = payload.password.trim();
  const cleanPhone = payload.phone?.trim() || undefined;
  try {
    const response = await fetch(`${apiConfig.BASE_URL}/auth/register`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", "x-request-id": requestId() },
      body: JSON.stringify({
        name: cleanName,
        email: cleanEmail,
        password: cleanPassword,
        phone: cleanPhone,
      }),
    });
    const body = (await response.json().catch(() => ({}))) as {
      ok?: boolean;
      data?: {
        user?: { id: string; email?: string };
        approval_status?: ApprovalStatus;
        message?: string;
      };
      error?: { message?: string; code?: string; field?: string };
    };
    if (!response.ok || body?.ok === false) {
      return {
        ok: false,
        error: body?.error?.message ?? "Não foi possível concluir o cadastro",
        code: body?.error?.code ?? "REGISTRATION_FAILED",
        status: response.status,
      };
    }
    await refreshAuthSession();
    return {
      ok: true,
      data: {
        id: body.data?.user?.id as string,
        email: body.data?.user?.email,
        approval_status: body.data?.approval_status,
        message: body.data?.message,
      },
    };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "Erro de rede" };
  }
}

/** Solicita o email de recuperação de senha. */
export async function requestPasswordRecovery(
  email: string,
): Promise<ApiResponse<{ message: string }>> {
  if (!apiConfig.BASE_URL) {
    return { ok: false, error: "VITE_API_BASE_URL não configurada", code: "NO_BACKEND" };
  }
  try {
    const response = await fetch(`${apiConfig.BASE_URL}/auth/password-recovery`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", "x-request-id": requestId() },
      body: JSON.stringify({ email }),
    });
    const body = (await response.json().catch(() => ({}))) as {
      ok?: boolean;
      data?: { message?: string };
      error?: { message?: string; code?: string };
    };
    if (!response.ok || body?.ok === false) {
      return {
        ok: false,
        error: body?.error?.message ?? "Não foi possível solicitar a recuperação.",
        code: body?.error?.code,
        status: response.status,
      };
    }
    return {
      ok: true,
      data: { message: body.data?.message ?? "Enviamos as instruções para o seu email." },
    };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "Erro de rede" };
  }
}

/** Cria a sessão por cookie a partir dos tokens do link de recuperação. */
export async function createSessionFromTokens(tokens: {
  access_token: string;
  refresh_token: string | null;
}): Promise<ApiResponse<{ id: string; email?: string }>> {
  if (!apiConfig.BASE_URL) {
    return { ok: false, error: "VITE_API_BASE_URL não configurada", code: "NO_BACKEND" };
  }
  try {
    const response = await fetch(`${apiConfig.BASE_URL}/auth/session/from-tokens`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", "x-request-id": requestId() },
      body: JSON.stringify(tokens),
    });
    const body = (await response.json().catch(() => ({}))) as {
      ok?: boolean;
      data?: { user?: { id: string; email?: string } };
      error?: { message?: string; code?: string };
    };
    if (!response.ok || body?.ok === false) {
      return {
        ok: false,
        error: body?.error?.message ?? "Link inválido ou expirado",
        code: body?.error?.code ?? "INVALID_SESSION",
        status: response.status,
      };
    }
    // A visita ao link de recuperacao comeca sem cookie, entao o gate de sessao
    // ja cacheou `null` por 120s. Sem limpar aqui, o PATCH seguinte devolve
    // NO_AUTH sem sequer chamar o backend.
    clearSessionIdentityCache();
    await refreshAuthSession();
    return { ok: true, data: body.data?.user as { id: string; email?: string } };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "Erro de rede" };
  }
}

export async function logoutSession(): Promise<void> {
  if (apiConfig.BASE_URL) {
    try {
      await fetch(`${apiConfig.BASE_URL}/auth/logout`, {
        method: "POST",
        credentials: "include",
        headers: { "x-request-id": requestId() },
      });
    } catch {
      // A limpeza local acontece mesmo sem resposta do backend.
    }
  }
  clearSessionIdentityCache();
  clearAuthSnapshot();
}

export const updateAccountPassword = (password: string) =>
  apiRequest("/auth/password", { method: "PATCH", body: JSON.stringify({ password }) });
export const updateAccountEmail = async (email: string) => {
  const result = await apiRequest("/auth/email", {
    method: "PATCH",
    body: JSON.stringify({ email }),
  });
  if (result.ok) await refreshAuthSession();
  return result;
};
export interface MyAccessData {
  permissions: AdminPermission[];
  impersonating?: boolean;
  role?: string;
  is_admin?: boolean;
  manageable_roles?: Array<{ id: string; name: string }>;
  /** `/me/access` devolve booleano (o handler converte "true" antes). */
  grant_access?: boolean;
  access_status?: "active" | "inactive" | "pending_approval" | string;
  approval_status?: ApprovalStatus | string;
  account_type?: AccountType | string;
  marketing_mode?: "simulation" | string | null;
  marketing_win_rate?: number | null;
  expires_at?: string | null;
  user_id?: string | null;
  impersonation_expires_at?: string | null;
}

export const getMyAccess = () => apiRequest<MyAccessData>("/me/access");
export const listBillingPlans = () => apiRequest<BillingPlan[] | Page<BillingPlan>>("/billing/plans");
export const listBillingHistory = (offset = 0, limit = 10) =>
  apiRequest<BillingHistoryItem[] | Page<BillingHistoryItem>>(`/billing/history?offset=${offset}&limit=${limit}`);
export const getBillingCheckout = (planId: string) =>
  apiRequest<{ checkout_url: string }>(`/billing/checkout/${encodeURIComponent(planId)}`);
export const listPublicFeedbacks = (offset = 0, limit = 10) =>
  apiRequest<Page<FeedbackItem>>(`/feedbacks?limit=${limit}&offset=${offset}`);
export const listMyFeedbacks = (offset = 0, limit = 10) =>
  apiRequest<Page<FeedbackItem>>(`/feedbacks/mine?limit=${limit}&offset=${offset}`);
export const createFeedback = (payload: Record<string, unknown>) =>
  apiRequest<FeedbackItem>("/feedbacks", { method: "POST", body: JSON.stringify(payload) });
export const adminListFeedbacks = (status: FeedbackStatus, offset = 0, limit = 10) =>
  apiRequest<Page<FeedbackItem>>(`/admin/feedbacks?status=${status}&offset=${offset}&limit=${limit}`);
export const adminApproveFeedback = (id: string) => apiRequest(`/admin/feedbacks/${id}/approve`, { method: "POST" });
export const adminRejectFeedback = (id: string) => apiRequest(`/admin/feedbacks/${id}/reject`, { method: "POST" });
export const adminArchiveFeedback = (id: string) => apiRequest(`/admin/feedbacks/${id}`, { method: "PATCH", body: JSON.stringify({ status: "archived" }) });
export const adminDeleteFeedback = (id: string) => apiRequest(`/admin/feedbacks/${id}`, { method: "DELETE" });

export const adminDashboard = (days = 30) => apiRequest<AdminDashboardData>(`/admin/dashboard?days=${days}`);
export const adminListClients = (
  segment: ClientSegment,
  offset = 0,
  limit = 10,
  search?: string,
) => {
  const trimmed = search?.trim();
  const query = trimmed ? `&search=${encodeURIComponent(trimmed)}` : "";
  return apiRequest<Page<AdminClient>>(
    `/admin/clients?segment=${segment}&offset=${offset}&limit=${limit}${query}`,
  );
};
export const adminCreateClient = (payload: AdminClientPayload) => apiRequest<AdminClient>("/admin/clients", { method: "POST", body: JSON.stringify(payload) });
export const adminUpdateClient = (id: string, payload: AdminClientPayload) => apiRequest<AdminClient>(`/admin/clients/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
export const adminApproveClient = (id: string, accessDays: number) =>
  apiRequest<AdminClient>(`/admin/clients/${id}/approve`, {
    method: "POST",
    body: JSON.stringify({ access_days: accessDays }),
  });
export const adminDeleteClient = (id: string) => apiRequest(`/admin/clients/${id}`, { method: "DELETE" });
export const adminClientHistory = (id: string, days = 30) => apiRequest<unknown[]>(`/admin/clients/${id}/history?days=${days}`);
export const adminStartImpersonation = (targetUserId: string, reason: string, durationMinutes = 15) =>
  apiRequest("/admin/impersonations", { method: "POST", body: JSON.stringify({ target_user_id: targetUserId, reason, duration_minutes: durationMinutes }) });
export const endImpersonation = () => apiRequest("/admin/impersonations/current", { method: "DELETE" });

export const adminListAccesses = () => apiRequest<AdminAccessRecord[]>("/admin/admins");
export const adminCreateAccess = (payload: AdminAccessPayload) => apiRequest<AdminAccessRecord>("/admin/admins", { method: "POST", body: JSON.stringify(payload) });
export const adminUpdateAccess = (id: string, payload: AdminAccessPayload) => apiRequest<AdminAccessRecord>(`/admin/admins/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
export const adminDeleteAccess = (id: string) => apiRequest(`/admin/admins/${id}`, { method: "DELETE" });

export const adminFinanceMetrics = (days = 30) => apiRequest<AdminFinanceMetrics>(`/admin/finance/metrics?days=${days}`);
export const adminListFinancePlans = () => apiRequest<BillingPlan[]>("/admin/finance/plans");
export const adminCreateFinancePlan = (payload: BillingPlanPayload) => apiRequest<BillingPlan>("/admin/finance/plans", { method: "POST", body: JSON.stringify(payload) });
export const adminUpdateFinancePlan = (id: string, payload: BillingPlanPayload) => apiRequest<BillingPlan>(`/admin/finance/plans/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
export const adminDeleteFinancePlan = (id: string) => apiRequest(`/admin/finance/plans/${id}`, { method: "DELETE" });
export const adminFinanceSettings = () => apiRequest<FinanceSettings>("/admin/finance/settings");
export const adminReconcileFinance = () => apiRequest<Record<string, unknown>>("/admin/finance/reconcile", { method: "POST" });

export const adminListWebhooks = () => apiRequest<WebhookEndpoint[]>("/admin/webhooks");
export const adminCreateWebhook = (payload: Record<string, unknown>) => apiRequest<CreatedWebhookEndpoint>("/admin/webhooks", { method: "POST", body: JSON.stringify(payload) });
export const adminDeleteWebhook = (id: string) => apiRequest(`/admin/webhooks/${id}`, { method: "DELETE" });
export const adminWebhookCatalog = () => apiRequest<WebhookCatalogItem[]>("/admin/webhooks/catalog");
export const adminWebhookDeliveries = (_endpointId?: string, offset = 0, limit = 50) => apiRequest<WebhookDelivery[]>(`/admin/webhook-deliveries?offset=${offset}&limit=${limit}`);
export const adminReplayWebhookDelivery = (id: string) => apiRequest(`/admin/webhook-deliveries/${id}/replay`, { method: "POST" });

export const adminEmailSettings = () => apiRequest<EmailSettings>("/admin/emails/settings");
export const adminListEmailTemplates = () => apiRequest<EmailTemplate[]>("/admin/emails/templates");
export const adminUpdateEmailTemplate = (event: EmailEventType, payload: Partial<EmailTemplate>) => apiRequest<EmailTemplate>(`/admin/emails/templates/${event}`, { method: "PATCH", body: JSON.stringify(payload) });
export const adminPreviewEmailTemplate = (event: EmailEventType, payload?: Record<string, unknown>) => apiRequest<{ subject: string; html_body: string }>(`/admin/emails/templates/${event}/preview`, { method: "POST", body: JSON.stringify(payload ?? {}) });
export const adminSendTestEmail = (event: EmailEventType, recipientEmail?: string) =>
  apiRequest(`/admin/emails/templates/${event}/test`, {
    method: "POST",
    body: JSON.stringify(
      recipientEmail?.trim() ? { recipient_email: recipientEmail.trim() } : {},
    ),
  });
export const adminEmailDeliveries = (offset = 0, limit = 20) => apiRequest<EmailDelivery[]>(`/admin/emails/deliveries?offset=${offset}&limit=${limit}`);

export const bullexApi = {
  connect: (payload: { email: string; password: string }, options?: { signal?: AbortSignal }) =>
    apiRequest("/bullex/connect", { method: "POST", body: JSON.stringify(payload), signal: options?.signal }),
  disconnect: () => apiRequest("/bullex/disconnect", { method: "POST" }),
  reconnect: () => apiRequest("/bullex/reconnect", { method: "POST" }),
  credentialsStatus: () =>
    apiRequest<{ credentials_saved: boolean; email: string | null }>("/bullex/credentials"),
  forgetCredentials: () => apiRequest<{ credentials_saved: boolean }>("/bullex/credentials", { method: "DELETE" }),
  status: () => apiRequest<{ status: string }>("/bullex/status"),
  account: () => apiRequest<BullExAccount>("/bullex/account"),
  balance: () => apiRequest<{ balance: number; currency?: string }>("/bullex/balance"),
  payouts: (active?: string) =>
    apiRequest<BullExPayoutItem[]>(
      active
        ? `/bullex/payouts?active=${encodeURIComponent(active)}`
        : "/bullex/payouts",
    ),
};

export interface BullExPayoutItem {
  symbol: string;
  payout: number | null;
  type?: string;
}

export interface BullExAccount {
  connected: boolean;
  balance: number | null;
  currency: string | null;
  /** A corretora tambem devolve PRACTICE; o painel opera so em REAL. */
  mode: "REAL" | "PRACTICE" | null;
  email: string | null;
  requires_2fa: boolean;
  status: "connected" | "disconnected";
  /** True quando email/senha estão salvos criptografados no backend. */
  credentials_saved?: boolean;
}

export interface MarketingSimulationTrade {
  id: string;
  asset: string;
  direction: string;
  result: string;
  profit: number;
  amount?: number;
  payout?: number;
  created_at?: string;
  is_simulated?: boolean;
  source?: string;
  account_mode?: string;
  disclaimer?: string;
  strategy_name?: string;
  strategy_key?: string;
  strategy_summary?: string;
  analysis_detail?: string;
  /** Vem nas operacoes reais espelhadas no painel de marketing. */
  speech_preview?: string;
  used_strategies?: string[];
  timeframe?: string;
  period?: string;
}

export interface MarketingSimulationStats {
  wins: number;
  losses: number;
  total_trades: number;
  win_rate: number;
  profit: number;
}

export interface MarketingSimulationTradeUpdate {
  result?: "WIN" | "LOSS";
  profit?: number;
  amount?: number;
  asset?: string;
  direction?: "CALL" | "PUT";
  payout?: number;
}

/** Overrides opcionais definidos no Shift+O antes de mostrar a operação. */
export interface MarketingSimulationTradeCreate {
  amount?: number;
  payout?: number;
  asset?: string;
  direction?: "CALL" | "PUT";
  result?: "WIN" | "LOSS";
  /** ISO8601; se omitido, o backend usa o instante atual. */
  created_at?: string;
}

/** Gera o histórico completo a partir do placar desejado (Shift+O). */
export interface MarketingSimulationGenerateHistory {
  wins: number;
  losses: number;
  /** Valor de entrada único para WIN e LOSS. */
  amount: number;
  /** Opcional: se omitido, o backend consulta o payout real do ativo. */
  payout?: number;
  asset?: string;
  period?: "M1" | "M5" | "M15";
}

export const marketingSimulationHistory = () =>
  apiRequest<MarketingSimulationTrade[]>("/marketing-simulation/history");
export const marketingSimulationStats = () =>
  apiRequest<MarketingSimulationStats>("/marketing-simulation/stats");
export const marketingSimulateTrade = (payload?: MarketingSimulationTradeCreate) =>
  apiRequest<MarketingSimulationTrade>("/marketing-simulation/trades", {
    method: "POST",
    body: payload ? JSON.stringify(payload) : undefined,
  });
export const marketingGenerateHistory = (payload: MarketingSimulationGenerateHistory) =>
  apiRequest<MarketingSimulationTrade[]>("/marketing-simulation/generate-history", {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const marketingUpdateTrade = (tradeId: string, payload: MarketingSimulationTradeUpdate) =>
  apiRequest<MarketingSimulationTrade>(`/marketing-simulation/trades/${encodeURIComponent(tradeId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
export const marketingDeleteTrade = (tradeId: string) =>
  apiRequest<null>(`/marketing-simulation/trades/${encodeURIComponent(tradeId)}`, {
    method: "DELETE",
  });

export const robotState = (userId: string) =>
  apiRequest<Record<string, unknown>>("/robot/state", {}, userId);
export const robotConfig = (payload: Record<string, unknown>) => apiRequest("/robot/config", { method: "POST", body: JSON.stringify(payload) });
export const robotStart = () => apiRequest("/robot/start", { method: "POST" });
export const robotStop = () => apiRequest("/robot/stop", { method: "POST" });
/**
 * Liga/desliga o modo LIVE — cadência de demonstração para transmissão.
 *
 * Só conta de marketing consegue: o backend devolve 403
 * `LIVE_MODE_SOMENTE_MARKETING` para as demais. O modo afrouxa o portão de
 * qualidade em OTC para o robô entrar com mais frequência. Não melhora o
 * resultado — os ativos OTC são ruído medido, então mais entradas significam
 * perder mais rápido, na mesma proporção.
 */
export const robotLiveMode = (enabled: boolean) =>
  apiRequest("/robot/live-mode", { method: "POST", body: JSON.stringify({ enabled }) });
/**
 * Liga/desliga o Modo Estudo (só marketing, só vale com o LIVE ligado).
 * O backend devolve 403 `STUDY_MODE_SOMENTE_MARKETING` para as demais contas.
 */
export const robotStudyMode = (enabled: boolean) =>
  apiRequest("/robot/study-mode", { method: "POST", body: JSON.stringify({ enabled }) });
export const robotResetCycle = () => apiRequest("/robot/reset-cycle", { method: "POST" });
export const robotResetScore = () => apiRequest("/robot/reset-score", { method: "POST" });
export const robotSyncConnection = () => apiRequest("/robot/sync-connection", { method: "POST" });
