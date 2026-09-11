import { queryOptions, type QueryClient } from "@tanstack/react-query";
import { ApiError, getMyAccess, type MyAccessData } from "@/lib/api";
import {
  isBenignRouteLoadError,
  isQueryCancellationError,
} from "@/lib/queryCancellation";

export { isBenignRouteLoadError, isQueryCancellationError } from "@/lib/queryCancellation";

/**
 * Chave canônica de `/me/access` compartilhada entre AppShell, beforeLoad admin
 * e páginas do painel. Qualquer divergência impede reaproveitar o cache e gera
 * refetch em toda troca de aba.
 */
export const ME_ACCESS_QUERY_KEY = ["me", "access"] as const;

/** Tempo em que o perfil de acesso é considerado fresco (sem refetch). */
export const ME_ACCESS_STALE_TIME_MS = 60_000;

/**
 * Opções React Query para o perfil de acesso do usuário autenticado.
 *
 * Usado no `beforeLoad` de `/admin` via `ensureQueryData` e nos `useQuery` do
 * shell/páginas, garantindo uma única fonte de verdade em memória.
 *
 * Returns:
 *   queryOptions tipado com `MyAccessData`.
 *
 * Raises:
 *   ApiError: quando `getMyAccess` retorna `ok: false` ou payload vazio.
 */
export function meAccessQueryOptions() {
  return queryOptions({
    queryKey: ME_ACCESS_QUERY_KEY,
    queryFn: async (): Promise<MyAccessData> => {
      const response = await getMyAccess();
      // Guardado antes dos guards: depois deles o tipo estreita para `never` e
      // `response.status` deixa de existir, embora o payload vazio ainda possa
      // chegar em tempo de execucao.
      const httpStatus = response.status;
      if (!response.ok) {
        throw new ApiError(
          response.error || "Falha ao carregar perfil de acesso",
          response.code,
          response.status,
        );
      }
      if (!response.data) {
        throw new ApiError("Perfil de acesso vazio", "ME_ACCESS_EMPTY", httpStatus);
      }
      return response.data;
    },
    staleTime: ME_ACCESS_STALE_TIME_MS,
  });
}

/**
 * Garante o perfil `/me/access` no cache, com retry se a query foi cancelada
 * ou rejeitada sem erro (undefined) durante o bind da sessão.
 *
 * Args:
 *   queryClient: cliente React Query do router.
 *
 * Returns:
 *   Dados de acesso do usuário autenticado.
 *
 * Raises:
 *   ApiError ou erro de rede real (nunca relança `undefined`/`null`).
 */
export async function ensureMeAccessData(queryClient: QueryClient): Promise<MyAccessData> {
  const fetchFresh = async (): Promise<MyAccessData> => {
    const data = await queryClient.fetchQuery(meAccessQueryOptions());
    if (!data) {
      throw new ApiError("Perfil de acesso indisponível", "ME_ACCESS_EMPTY");
    }
    return data;
  };

  try {
    const data = await queryClient.ensureQueryData(meAccessQueryOptions());
    if (!data) return await fetchFresh();
    return data;
  } catch (error) {
    // Nunca faça `throw error` quando error é nullish — vira “Uncaught undefined”
    // e deixa a tela no fundo escuro sem error boundary.
    if (isBenignRouteLoadError(error) || isQueryCancellationError(error)) {
      return await fetchFresh();
    }
    throw error;
  }
}
