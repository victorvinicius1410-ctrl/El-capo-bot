import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { CheckCircle2, Sparkles } from "lucide-react";
import { ApiError, getMyAccess } from "@/lib/api";
import {
  initTrial,
  remainingDaysFromExpiresAt,
  TRIAL_DAYS,
  TRIAL_DISCOUNT,
} from "@/lib/trial";

export const Route = createFileRoute("/_authenticated/welcome-trial")({
  ssr: false,
  head: () => ({ meta: [{ title: "Bem-vindo - Periodo gratis" }] }),
  component: WelcomeTrial,
});

function WelcomeTrial() {
  const navigate = useNavigate();
  const access = useQuery({
    queryKey: ["me", "access"],
    queryFn: async () => {
      const response = await getMyAccess();
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
  });

  useEffect(() => {
    initTrial();
  }, []);

  const trialDays =
    access.data?.expires_at != null
      ? remainingDaysFromExpiresAt(access.data.expires_at)
      : TRIAL_DAYS;

  return (
    <div className="min-h-[70vh] flex items-center justify-center">
      <div className="max-w-lg w-full rounded-2xl border border-border bg-card p-8 text-center">
        <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <Sparkles className="h-8 w-8" />
        </div>
        <h1 className="mb-2 text-2xl font-bold">Seu teste gratis comecou!</h1>
        <p className="mb-6 text-muted-foreground">
          Voce tem <strong>{trialDays} dias</strong> de acesso total ao ElCapo AutoBot, sem
          compromisso.
        </p>

        <ul className="mb-6 space-y-2 text-left">
          {[
            "Robo de operacoes automaticas",
            "Graficos em tempo real",
            "Configuracoes de Stop Win/Loss e Martingale",
          ].map((text) => (
            <li key={text} className="flex items-center gap-2 text-sm">
              <CheckCircle2 className="h-4 w-4 shrink-0 text-primary" />
              {text}
            </li>
          ))}
        </ul>

        <div className="mb-6 rounded-lg border border-border bg-background/40 px-4 py-3 text-sm">
          Assine agora e ganhe{" "}
          <span className="font-bold text-foreground">-{TRIAL_DISCOUNT}% de desconto</span>.
        </div>

        <button
          onClick={() => navigate({ to: "/dashboard", replace: true })}
          className="w-full rounded-lg bg-primary py-3 font-semibold text-primary-foreground transition hover:opacity-90"
        >
          Comecar agora
        </button>
      </div>
    </div>
  );
}
