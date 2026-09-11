/**
 * Resolução da URL base da API do painel.
 *
 * Permite localhost (dev), produção (`api.elcapobot.online`) e staging
 * (`api.elcapo2.shop`). Qualquer outro host cai no fallback de produção
 * para evitar apontar o painel a um backend não autorizado.
 */

export const PRODUCTION_API_BASE_URL = "https://api.elcapobot.online";
export const STAGING_API_BASE_URL = "https://api.elcapo2.shop";

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);
const ALLOWED_HTTPS_API_HOSTS = new Set([
  "api.elcapobot.online",
  "api.elcapo2.shop",
]);

/**
 * Normaliza e valida `VITE_API_BASE_URL`.
 *
 * Args:
 *   configured: Valor bruto da env (pode ser undefined / com barra final).
 *
 * Returns:
 *   URL https/http permitida, sem barra final. Fallback: produção.
 *
 * Example:
 *   >>> resolveApiBaseUrl("https://api.elcapo2.shop/")
 *   "https://api.elcapo2.shop"
 */
export function resolveApiBaseUrl(configured: string | undefined): string {
  const normalized = configured?.trim().replace(/\/+$/, "");
  if (!normalized) return PRODUCTION_API_BASE_URL;
  try {
    const url = new URL(normalized);
    const isLocal = LOCAL_HOSTS.has(url.hostname);
    if (isLocal && (url.protocol === "http:" || url.protocol === "https:")) {
      return normalized;
    }
    if (url.protocol === "https:" && ALLOWED_HTTPS_API_HOSTS.has(url.hostname)) {
      return normalized;
    }
  } catch {
    // Configuração inválida → endpoint oficial de produção.
  }
  return PRODUCTION_API_BASE_URL;
}
