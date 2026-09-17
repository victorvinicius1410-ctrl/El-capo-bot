/**
 * Memória da moeda da conta BullEx, por usuário.
 *
 * O backend zera `currency` no `/account` sempre que a conta não está
 * conectada (e também na janela entre abrir o painel e o primeiro poll
 * responder). Sem moeda, o campo "Valor por entrada" não sabe se o mínimo é
 * R$ 5 ou US$ 1 — e antes disso anunciava "Mínimo R$ 1,00", que não vale em
 * nenhuma das duas contas.
 *
 * O backend, esse, não esquece: `resolve_user_account_currency` lê a moeda
 * memorizada/persistida e cobra R$ 5 de uma conta BRL mesmo desconectada.
 * Este módulo dá a mesma memória ao painel, para os dois lados concordarem.
 *
 * Regras:
 * - só grava moeda conhecida (código não vazio vindo da corretora);
 * - snapshot ao vivo sempre vence a memória (trocou de conta, vale o novo);
 * - `localStorage` com espelho em memória, porque em aba anônima o storage
 *   lança e a sessão precisa continuar funcionando.
 */

import { normalizeAccountCurrency } from "./bullexConnection.ts";

const STORAGE_PREFIX = "bullex:account-currency:";

/** Espelho em memória: cobre SSR, modo privado e storage bloqueado. */
const memo = new Map<string, "BRL" | "USD">();

function storageKey(userId: string): string {
  return `${STORAGE_PREFIX}${userId}`;
}

/** Código de moeda utilizável — vazio/nulo não conta como "conhecido". */
export function isKnownAccountCurrency(currency?: string | null): boolean {
  return String(currency ?? "").trim() !== "";
}

/** Guarda a moeda da conta conectada para o usuário informado. */
export function rememberAccountCurrency(
  userId: string | null | undefined,
  currency?: string | null,
): void {
  if (!userId || !isKnownAccountCurrency(currency)) return;
  const code = normalizeAccountCurrency(currency);
  memo.set(String(userId), code);
  try {
    globalThis.localStorage?.setItem(storageKey(String(userId)), code);
  } catch {
    /* sem storage — o espelho em memória basta para esta aba */
  }
}

/** Última moeda conhecida do usuário, ou `null` se nunca conectou. */
export function recallAccountCurrency(userId: string | null | undefined): "BRL" | "USD" | null {
  if (!userId) return null;
  const key = String(userId);
  const cached = memo.get(key);
  if (cached) return cached;
  let stored: string | null = null;
  try {
    stored = globalThis.localStorage?.getItem(storageKey(key)) ?? null;
  } catch {
    stored = null;
  }
  if (!isKnownAccountCurrency(stored)) return null;
  const code = normalizeAccountCurrency(stored);
  memo.set(key, code);
  return code;
}

/** Esquece a moeda memorizada (troca de conta/usuário). */
export function forgetAccountCurrency(userId: string | null | undefined): void {
  if (!userId) return;
  const key = String(userId);
  memo.delete(key);
  try {
    globalThis.localStorage?.removeItem(storageKey(key));
  } catch {
    /* sem storage — basta ter limpado o espelho */
  }
}

/** Limpa o espelho em memória (usado nos testes). */
export function resetAccountCurrencyMemo(): void {
  memo.clear();
}
