import { createFileRoute, redirect } from "@tanstack/react-router";

/** Rota legada: ElCapo/Bullex agora vive em `/configuracoes?secao=conta`. */
export const Route = createFileRoute("/_authenticated/bullex")({
  beforeLoad: () => {
    throw redirect({
      to: "/configuracoes",
      search: { secao: "conta" },
      replace: true,
    });
  },
});
