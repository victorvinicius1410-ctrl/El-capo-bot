import { QueryClient } from "@tanstack/react-query";
import { createRouter } from "@tanstack/react-router";
import { routeTree } from "./routeTree.gen";

export const getRouter = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        // Evita refetch automático ao remountar abas admin (staleTime default = 0).
        staleTime: 30_000,
        refetchOnWindowFocus: false,
      },
    },
  });

  const router = createRouter({
    routeTree,
    context: { queryClient },
    scrollRestoration: true,
    defaultPreload: "intent",
    // Pequeno delay evita disparar beforeLoad/rede no hover acidental da sidebar.
    defaultPreloadDelay: 80,
    defaultPreloadStaleTime: 30_000,
  });

  return router;
};
