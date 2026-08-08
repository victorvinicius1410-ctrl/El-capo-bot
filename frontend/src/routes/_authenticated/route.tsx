import { createFileRoute, Outlet, useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";
import { AppShell } from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";

export const Route = createFileRoute("/_authenticated")({
  ssr: false,
  component: AuthenticatedLayout,
});

function AuthenticatedLayout() {
  const navigate = useNavigate();
  const { user, loading } = useAuth();

  useEffect(() => {
    if (loading || user) return;
    navigate({ to: "/login", replace: true });
  }, [loading, navigate, user]);

  if (loading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4">
        <div className="text-center">
          <div className="text-lg font-semibold text-foreground">Carregando sessao</div>
          <div className="mt-2 text-sm text-muted-foreground">
            Aguarde enquanto validamos seu acesso.
          </div>
        </div>
      </div>
    );
  }

  return (
    <AppShell key={user.id}>
      <Outlet />
    </AppShell>
  );
}
