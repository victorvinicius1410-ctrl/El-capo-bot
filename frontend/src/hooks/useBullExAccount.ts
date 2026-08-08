import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { ApiError, bullexApi, type BullExAccount } from "@/lib/api";
import { preferStableBullExAccount } from "@/lib/bullexConnection";

export const BULLEX_ACCOUNT_QUERY_KEY = ["bullex-account"] as const;

/** Consulta periodicamente a conta Bullex vinculada à sessão atual. */
export function useBullExAccountQuery({
  userId,
  enabled = true,
  isDocumentVisible = true,
}: {
  userId?: string | null;
  enabled?: boolean;
  isDocumentVisible?: boolean;
} = {}): UseQueryResult<BullExAccount, Error> {
  return useQuery({
    queryKey: [...BULLEX_ACCOUNT_QUERY_KEY, userId],
    queryFn: async ({ client, queryKey }) => {
      const response = await bullexApi.account();
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      const previous = client.getQueryData<BullExAccount>(queryKey);
      return preferStableBullExAccount(previous, normalizeBullExAccount(response.data));
    },
    enabled: enabled && Boolean(userId),
    refetchInterval: isDocumentVisible ? 25_000 : false,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
    retry: false,
    staleTime: 24_000,
    // Evita apagar email/saldo no remount da aba Configurações.
    placeholderData: (previous) => previous,
  });
}

/** Remove estado auxiliar de polling; a chave é preservada por compatibilidade. */
export function resetBullExAccountState(_userId?: string | null): void {}

function normalizeBullExAccount(data: BullExAccount): BullExAccount {
  const connected = data.connected === true || data.status === "connected";
  return {
    connected,
    balance: typeof data.balance === "number" ? data.balance : null,
    currency: data.currency ?? null,
    mode: connected ? "REAL" : null,
    email: data.email ?? null,
    requires_2fa: Boolean(data.requires_2fa),
    status: connected ? "connected" : "disconnected",
  };
}
