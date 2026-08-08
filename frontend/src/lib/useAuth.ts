import { useEffect, useSyncExternalStore } from "react";
import { apiConfig } from "./api";
import {
  canRevalidateAuthNow,
  shouldRevalidateAuthOnVisibilityChange,
} from "./authSessionKeepAlive";
import {
  clearSessionIdentityCache,
  readSessionIdentityCache,
  sessionIdentityCacheTtlMs,
  writeSessionIdentityCache,
} from "./sessionIdentityCache";

export interface AuthUser {
  id: string;
  email: string;
}

export interface AuthSnapshot {
  user: AuthUser | null;
  loading: boolean;
}

const listeners = new Set<() => void>();
const serverSnapshot: AuthSnapshot = { user: null, loading: true };
let snapshot = serverSnapshot;
let initialized = false;
let refreshInFlight: Promise<AuthUser | null> | null = null;
let lastVisibilityRevalidateAt = 0;

function emit(user: AuthUser | null, loading: boolean): void {
  if (snapshot.user?.id === user?.id && snapshot.loading === loading) return;
  snapshot = { user, loading };
  listeners.forEach((listener) => listener());
}

function authUserFromIdentity(
  identity: { userId: string; email: string | null } | null,
): AuthUser | null {
  if (!identity) return null;
  return { id: identity.userId, email: identity.email ?? "" };
}

/**
 * Lê `GET /auth/session`, reutilizando o cache de identidade compartilhado
 * com `apiRequest` para não dobrar round-trips.
 *
 * Args:
 *   bypassCache: se true, força rede (ex.: após voltar à aba, respeitando
 *     o intervalo mínimo de ``authSessionKeepAlive``).
 *
 * Returns:
 *   Usuário autenticado ou null.
 */
async function fetchSessionUser(bypassCache = false): Promise<AuthUser | null> {
  if (!apiConfig.BASE_URL) return null;
  if (!bypassCache) {
    const cached = readSessionIdentityCache();
    if (cached !== undefined) return authUserFromIdentity(cached);
  }
  const response = await fetch(`${apiConfig.BASE_URL}/auth/session`, {
    credentials: "include",
    headers: { "x-request-id": crypto.randomUUID() },
  });
  if (!response.ok) {
    writeSessionIdentityCache(null, sessionIdentityCacheTtlMs());
    return null;
  }
  const body: unknown = await response.json();
  if (!isSessionPayload(body)) {
    writeSessionIdentityCache(null, sessionIdentityCacheTtlMs());
    return null;
  }
  const user: AuthUser = {
    id: String(body.data.user.id),
    email: String(body.data.user.email ?? ""),
  };
  writeSessionIdentityCache(
    { userId: user.id, email: user.email || null },
    sessionIdentityCacheTtlMs(),
  );
  return user;
}

function isSessionPayload(value: unknown): value is {
  ok: true;
  data: { authenticated: true; user: { id: string | number; email?: string } };
} {
  if (!value || typeof value !== "object") return false;
  const body = value as Record<string, unknown>;
  const data = body.data as Record<string, unknown> | undefined;
  const user = data?.user as Record<string, unknown> | undefined;
  return body.ok === true && data?.authenticated === true && user?.id != null;
}

/**
 * Renova cookies via POST /auth/refresh (access expirado, refresh ainda válido).
 *
 * Returns:
 *   true se o backend renovou a sessão; false caso contrário.
 */
export async function renewAuthSessionCookies(): Promise<boolean> {
  if (!apiConfig.BASE_URL) return false;
  try {
    const response = await fetch(`${apiConfig.BASE_URL}/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: { "x-request-id": crypto.randomUUID() },
    });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Atualiza o usuário autenticado usando o cookie de sessão HTTP-only.
 *
 * Args:
 *   bypassCache: força nova leitura de `/auth/session` (visibility/login).
 */
export async function refreshAuthSession(bypassCache = false): Promise<AuthUser | null> {
  if (refreshInFlight && !bypassCache) return refreshInFlight;
  if (refreshInFlight && bypassCache) {
    await refreshInFlight.catch(() => null);
  }
  refreshInFlight = (async () => {
    const user = await fetchSessionUser(bypassCache);
    emit(user, false);
    return user;
  })().finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

/** Limpa apenas o snapshot local após logout. */
export function clearAuthSnapshot(): void {
  clearSessionIdentityCache();
  emit(null, false);
}

/** Expõe a sessão autenticada restaurada do backend. */
export function useAuth(): AuthSnapshot {
  useEffect(() => {
    if (initialized || typeof window === "undefined") return;
    initialized = true;
    void refreshAuthSession().catch((error: unknown) => {
      console.error("[AUTH_INIT_ERROR]", error);
      emit(null, false);
    });

    let wasHidden = document.hidden === true;
    const onVisibility = () => {
      const isVisible = document.hidden !== true;
      const now = Date.now();
      if (
        shouldRevalidateAuthOnVisibilityChange(isVisible, wasHidden) &&
        canRevalidateAuthNow(now, lastVisibilityRevalidateAt)
      ) {
        lastVisibilityRevalidateAt = now;
        void refreshAuthSession(true).catch((error: unknown) => {
          console.error("[AUTH_VISIBILITY_REFRESH_ERROR]", error);
        });
      }
      wasHidden = !isVisible;
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => snapshot,
    () => serverSnapshot,
  );
}
