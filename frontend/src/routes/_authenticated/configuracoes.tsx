import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Bot, Plug, Sparkles } from "lucide-react";
import { BullexConnectionPanel } from "@/components/BullexConnectionPanel";
import { RobotControlPanel } from "@/components/RobotControlPanel";
import { canUseRobotControls } from "@/lib/adminPresentation";
import { meAccessQueryOptions } from "@/lib/meAccessQuery";

type ConfigSecao = "conta" | "robo";

type ConfiguracoesSearch = {
  secao: ConfigSecao;
};

export const Route = createFileRoute("/_authenticated/configuracoes")({
  validateSearch: (search: Record<string, unknown>): ConfiguracoesSearch => ({
    secao: search.secao === "robo" ? "robo" : "conta",
  }),
  head: ({ match }) => ({
    meta: [
      {
        title:
          match.search.secao === "robo"
            ? "Robô - Configurações - ElCapo AutoBot"
            : "Conta Corretora - Configurações - ElCapo AutoBot",
      },
    ],
  }),
  component: ConfiguracoesPage,
});

const SECOES = [
  {
    id: "conta" as const,
    label: "Conta Corretora",
    hint: "Vincular sessão",
    Icon: Plug,
  },
  {
    id: "robo" as const,
    label: "Robô",
    hint: "Ligar e operar",
    Icon: Bot,
  },
] as const;

/**
 * Aba única no menu; no cabeçalho separa Conta Corretora e Robô em opções.
 */
function ConfiguracoesPage() {
  const navigate = Route.useNavigate();
  const { secao } = Route.useSearch();
  const access = useQuery(meAccessQueryOptions());
  const supportSession = access.data?.impersonating === true;
  const robotControlsAllowed = canUseRobotControls(supportSession);
  const visibleSections = robotControlsAllowed
    ? SECOES
    : SECOES.filter((section) => section.id === "conta");

  useEffect(() => {
    if (!robotControlsAllowed && secao === "robo") {
      void navigate({
        to: "/configuracoes",
        search: { secao: "conta" },
        replace: true,
      });
    }
  }, [navigate, robotControlsAllowed, secao]);

  return (
    <div className="config-page mx-auto max-w-3xl">
      <div className="config-atmosphere" aria-hidden="true">
        <span className="config-bg-base" />
        <span className="config-aurora config-aurora-a" />
        <span className="config-aurora config-aurora-b" />
        <span className="config-orb config-orb-a" />
        <span className="config-orb config-orb-b" />
        <span className="config-orb config-orb-c" />
        <span className="config-beam" />
        <span className="config-horizon" />
        <span className="config-stars" />
        <span className="config-grid" />
        <span className="config-ring config-ring-a" />
        <span className="config-ring config-ring-b" />
        <span className="config-sparks">
          <i />
          <i />
          <i />
          <i />
          <i />
          <i />
        </span>
        <span className="config-vignette" />
      </div>

      <header className="config-hero">
        <p className="config-kicker">
          <Sparkles className="config-kicker-icon h-3.5 w-3.5" />
          ElCapo AutoBot
        </p>
        <h1 className="config-title">Configurações</h1>
        <p className="config-lead">
          Conecte a corretora e liberte o robô para operar — setup rápido, visual limpo, pronto para
          o próximo passo.
        </p>

        <nav className="config-tabs" role="tablist" aria-label="Opções de configuração">
          {visibleSections.map(({ id, label, hint, Icon }) => {
            const active = secao === id;
            return (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={active}
                className={`config-tab ${active ? "config-tab-active" : ""}`}
                onClick={() => {
                  void navigate({
                    to: "/configuracoes",
                    search: { secao: id },
                    replace: true,
                  });
                }}
              >
                <span className="config-tab-icon">
                  <Icon className="h-4 w-4" />
                </span>
                <span className="config-tab-copy">
                  <span className="config-tab-label">{label}</span>
                  <span className="config-tab-hint">{hint}</span>
                </span>
                {active ? <span className="config-tab-glow" aria-hidden="true" /> : null}
              </button>
            );
          })}
        </nav>
      </header>

      <div
        key={secao}
        className="config-stage"
        role="tabpanel"
        aria-label={secao === "conta" ? "Conta Corretora" : "Robô"}
      >
        {secao === "conta" || !robotControlsAllowed ? (
          <BullexConnectionPanel />
        ) : (
          <RobotControlPanel />
        )}
      </div>
    </div>
  );
}
