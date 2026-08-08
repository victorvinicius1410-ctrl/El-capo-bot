import { createFileRoute, Outlet, redirect } from "@tanstack/react-router";
import { ensureMeAccessData } from "@/lib/meAccessQuery";

export const Route = createFileRoute("/_authenticated/admin")({
  head: () => ({ meta: [{ title: "Administração — ElCapo AutoBot" }] }),
  beforeLoad: async ({ context }) => {
    // Reaproveita cache do AppShell (`meAccessQueryOptions`) em vez de
    // `getMyAccess()` frio a cada troca de aba — isso bloqueava a pintura.
    // `ensureMeAccessData` ainda faz retry se a query foi cancelada / rejeitada
    // sem erro no bind da sessão (AuthUserBoundary), evitando tela azul.
    const access = await ensureMeAccessData(context.queryClient);
    if (!access?.is_admin) {
      throw redirect({ to: "/dashboard", replace: true });
    }
  },
  component: AdminLayout,
});

function AdminLayout() {
  return <Outlet />;
}
