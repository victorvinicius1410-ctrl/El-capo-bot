import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { ApiError, bullexApi, type BullExAccount } from "@/lib/api";
import { preferStableBullExAccount } from "@/lib/bullexConnection";
import {
  isKnownAccountCurrency,
  recallAccountCurrency,
  rememberAccountCurrency,
} from "@/lib/accountCurrencyMemo";

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
      const account = preferStableBullExAccount(previous, normalizeBullExAccount(response.data));
      return withRememberedCurrency(userId, account);
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

/**
 * Preenche a moeda quando o snapshot vem sem ela (conta desconectada).
 *
 * O backend manda `currency: null` em toda resposta desconectada, mas continua
 * cobrando o mínimo da moeda real da conta (R$ 5 em BRL). Sem esta memória o
 * painel oferecia mínimo 1 para conta em real, e o salvar voltava
 * `ENTRY_VALUE_TOO_LOW`.
 */
function withRememberedCurrency(
  userId: string | null | undefined,
  account: BullExAccount,
): BullExAccount {
  if (isKnownAccountCurrency(account.currency)) {
    rememberAccountCurrency(userId, account.currency);
    return account;
  }
  const remembered = recallAccountCurrency(userId);
  return remembered ? { ...account, currency: remembered } : account;
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
