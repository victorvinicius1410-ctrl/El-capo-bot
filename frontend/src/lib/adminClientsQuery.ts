/**
 * Query canônica da listagem admin de clientes (Acessos / Pedidos).
 *
 * Centraliza queryKey, staleTime e prefetch da 1ª página de Pedidos para
 * que o hover na sidebar e o loader da rota compartilhem o mesmo cache.
 */

import type { QueryClient } from "@tanstack/react-query";
import {
  ApiError,
  adminListClients,
  type ClientSegment,
  type Page,
  type AdminClient,
} from "@/lib/api";

export const ADMIN_CLIENTS_QUERY_ROOT = ["admin", "clients"] as const;

/** Pedidos: cache um pouco mais longo (fila muda só com approve/register). */
export const ADMIN_PENDING_STALE_TIME_MS = 45_000;

/** Demais segmentos. */
export const ADMIN_CLIENTS_STALE_TIME_MS = 30_000;

export type AdminClientsPage = Page<AdminClient>;

/**
 * Monta a queryKey da listagem infinita por segmento (e busca opcional).
 *
 * A busca entra na key para que cada termo tenha seu próprio cache/página,
 * sem misturar resultados de buscas diferentes.
 *
 * @param segment - pending | active | trial | marketing | inactive
 * @param search - Termo de busca por nome/email/ID trader (opcional)
 */
export function adminClientsQueryKey(segment: ClientSegment, search?: string) {
  const normalized = search?.trim() ?? "";
  return [...ADMIN_CLIENTS_QUERY_ROOT, segment, normalized] as const;
}

/**
 * Prefetch da primeira página de um segmento (hover / loader).
 *
 * @param queryClient - QueryClient do router
 * @param segment - Segmento a aquecer (default: Pedidos)
 * @param search - Termo de busca opcional (não usado no prefetch de hover)
 */
export async function prefetchAdminClientsSegment(
  queryClient: QueryClient,
  segment: ClientSegment = "pending",
  search?: string,
): Promise<void> {
  const staleTime =
    segment === "pending" ? ADMIN_PENDING_STALE_TIME_MS : ADMIN_CLIENTS_STALE_TIME_MS;
  await queryClient.prefetchInfiniteQuery({
    queryKey: adminClientsQueryKey(segment, search),
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const response = await adminListClients(segment, pageParam as number, 10, search);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    getNextPageParam: (lastPage: AdminClientsPage) =>
      lastPage.has_more ? lastPage.next_offset : undefined,
    staleTime,
  });
}
