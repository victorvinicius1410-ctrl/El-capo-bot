import type { BullExAccount } from "./api";
import { isManualBullexDisconnectActive } from "./manualBullexDisconnect.ts";

export interface BullExStatus {
  status: string;
}

interface ConnectionInput {
  account?: BullExAccount | null;
  accountStatus?: BullExStatus | null;
  cachedGrace?: boolean;
  pendingConnect?: boolean;
}

function statusToken(status?: string | null): string {
  return String(status ?? "").trim().toUpperCase();
}

/** Status transitório do poll (backoff) — não é desconexão confirmada. */
export function isBullExStatusBackoff(accountStatus?: BullExStatus | null): boolean {
  return statusToken(accountStatus?.status) === "BACKOFF";
}

/**
 * Mantém email/saldo do snapshot anterior quando o poll devolve desconectado
 * "vazio" ou conectado sem métricas (comum ao entrar/sair de Configurações).
 *
 * Após Desconectar Bullex (manual), NÃO mascara — o usuário precisa ver
 * Desconectado e o formulário de login.
 */
export function preferStableBullExAccount(
  previous: BullExAccount | undefined,
  next: BullExAccount,
): BullExAccount {
  if (isManualBullexDisconnectActive()) return next;

  const previousGood =
    previous != null &&
    previous.connected === true &&
    (previous.email != null || previous.balance != null);

  if (!previousGood || !previous) return next;

  const nextEmptyDisconnect =
    next.connected === false && next.email == null && next.balance == null;
  if (nextEmptyDisconnect) return previous;

  if (next.connected === true && (next.email == null || next.balance == null)) {
    return {
      ...next,
      email: next.email ?? previous.email,
      balance: next.balance ?? previous.balance,
      currency: next.currency ?? previous.currency,
    };
  }
  return next;
}

/** Determina se os sinais de conta indicam conexão ativa. */
export function isBullExConnected({
  account,
  accountStatus,
  cachedGrace = false,
  pendingConnect = false,
}: ConnectionInput): boolean {
  if (cachedGrace || pendingConnect) return true;
  const status = statusToken(accountStatus?.status);
  // Sinais positivos primeiro: um /account stale com connected:false (backoff)
  // não pode anular um /bullex/status CONNECTED fresco.
  if (status === "CONNECTED") return true;
  if (account?.connected === true || account?.status === "connected") return true;
  // Backoff sem snapshot de conta: ainda não confirmar desconexão.
  if (status === "BACKOFF") return false;
  if (account?.connected === false || account?.status === "disconnected") return false;
  return false;
}

/** Determina se os sinais de conta indicam desconexão confirmada. */
export function isBullExDisconnected(input: ConnectionInput): boolean {
  if (isBullExConnected(input)) return false;
  // Poll em backoff / grace — banner vermelho não deve aparecer.
  if (isBullExStatusBackoff(input.accountStatus) || input.cachedGrace) return false;
  return input.account?.connected === false ||
    input.account?.status === "disconnected" ||
    statusToken(input.accountStatus?.status) === "DISCONNECTED";
}

/** Normaliza o código monetário suportado pelo painel. */
export function normalizeAccountCurrency(currency?: string | null): "BRL" | "USD" {
  const code = String(currency ?? "").trim().toUpperCase();
  if (code === "USD" || code === "US$" || code === "$" || code.includes("USD")) return "USD";
  return "BRL";
}

/** Formata o saldo na moeda retornada pela corretora. */
export function formatBullExBalance(balance?: number | null, currency?: string | null): string {
  if (balance == null || !Number.isFinite(balance)) return "-";
  const code = normalizeAccountCurrency(currency);
  return new Intl.NumberFormat(code === "USD" ? "en-US" : "pt-BR", {
    style: "currency",
    currency: code,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(balance);
}

/** Símbolo curto da moeda usado nos campos de valores. */
export function currencySymbol(currency?: string | null): string {
  return normalizeAccountCurrency(currency) === "USD" ? "$" : "R$";
}

/** Formata o resultado financeiro com sinal explícito quando positivo. */
export function formatProfitAmount(profit?: number | null, currency?: string | null): string {
  const value = profit == null || !Number.isFinite(profit) ? 0 : profit;
  const formatted = formatBullExBalance(value, currency);
  if (formatted === "-") return formatted;
  return value > 0 ? `+${formatted}` : formatted;
}

/** Classifica o resultado financeiro para estilização. */
export function profitTone(profit?: number | null): "positive" | "negative" | "neutral" {
  const value = profit == null || !Number.isFinite(profit) ? 0 : profit;
  return value > 0 ? "positive" : value < 0 ? "negative" : "neutral";
}

/** Converte um valor monetário para leitura em voz alta pelo narrador. */
export function formatMoneyForSpeech(value?: number | null, currency?: string | null): string {
  if (value == null || !Number.isFinite(value)) return "não informado";
  const code = normalizeAccountCurrency(currency);
  const absolute = Math.abs(value);
  const centsTotal = Math.round(absolute * 100);
  const whole = Math.floor(centsTotal / 100);
  const cents = centsTotal % 100;
  const unit = code === "USD"
    ? whole === 1 ? "dólar" : "dólares"
    : whole === 1 ? "real" : "reais";
  const centsLabel = cents === 1 ? "centavo" : "centavos";
  const spoken = `${whole} ${unit} e ${cents} ${centsLabel}`;
  if (value < 0) return `menos ${spoken}`;
  return spoken;
}

const NO_BALANCE_MESSAGE = "Você está sem saldo para iniciar. Faça um depósito na Bullex.";
const LOW_BALANCE_MESSAGE = "Seu saldo é menor que o valor da entrada.";

/** Valida se o saldo permite operar com o valor de entrada configurado. */
export function entryValueBalanceError(
  balance: number | null | undefined,
  entryValue: number,
): string | null {
  if (balance != null && Number.isFinite(balance) && balance <= 0) return NO_BALANCE_MESSAGE;
  if (balance != null && Number.isFinite(balance) && entryValue > balance) return LOW_BALANCE_MESSAGE;
  return null;
}

/**
 * Decide se o botão "Iniciar Operação" do overlay pode ficar habilitado.
 *
 * Alinhado ao painel Configurações → Robô:
 * - não depende de `loginPending` (podia travar só o overlay);
 * - não desabilita por `connected` — o clique abre o diálogo / chama
 *   POST /robot/start, que limpa backoff e valida a Bullex de verdade.
 *   Bloquear no front por poll stale deixava o overlay sem POST nenhum.
 */
export function canStartRobotOperation(input: {
  apiConfigured: boolean;
  connected?: boolean;
  operationRunning: boolean;
  balance?: number | null;
}): boolean {
  if (!input.apiConfigured) return false;
  if (input.operationRunning) return false;
  if (input.balance != null && Number.isFinite(input.balance) && input.balance <= 0) return false;
  return true;
}
