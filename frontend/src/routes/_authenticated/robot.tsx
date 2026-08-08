import { createFileRoute, redirect } from "@tanstack/react-router";

/** Rota legada: Robô agora vive em `/configuracoes?secao=robo`. */
export const Route = createFileRoute("/_authenticated/robot")({
  beforeLoad: () => {
    throw redirect({
      to: "/configuracoes",
      search: { secao: "robo" },
      replace: true,
    });
  },
});
