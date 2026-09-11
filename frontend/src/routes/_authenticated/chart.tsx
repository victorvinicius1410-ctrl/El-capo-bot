import { useEffect, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { Building2, ExternalLink, Maximize2, Minimize2, ShieldAlert } from "lucide-react";
import { BullexLogo, BULLEX_BRAND_NAME } from "@/components/BullexLogo";
import {
  BULLEX_TRADEROOM_URL,
  getBrokerChartLayout,
  isBrokerIframeBlockedByBrowser,
  shouldCollapseBrokerChartOnKey,
} from "@/lib/brokerChartLayout";

export const Route = createFileRoute("/_authenticated/chart")({
  ssr: false,
  head: () => ({ meta: [{ title: "Corretora - ElCapo AutoBot" }] }),
  component: ChartPage,
});

/**
 * Aba Corretora: traderoom Bullex embutido.
 *
 * O login dentro do iframe falha em vários navegadores (cookies de terceiros).
 * Por isso o CTA principal abre a Bullex em nova aba — ver docs/CORRETORA.md.
 */
function ChartPage() {
  const [expanded, setExpanded] = useState(false);
  // Só depois da montagem: o UA não existe no prerender.
  const [iframeBlocked, setIframeBlocked] = useState(false);
  const [forceEmbed, setForceEmbed] = useState(false);
  const layout = getBrokerChartLayout(expanded);
  const showFallback = iframeBlocked && !forceEmbed;

  useEffect(() => {
    setIframeBlocked(isBrokerIframeBlockedByBrowser());
  }, []);

  useEffect(() => {
    document.body.classList.toggle("broker-chart-expanded", layout.lockBodyScroll);
    return () => {
      document.body.classList.remove("broker-chart-expanded");
    };
  }, [layout.lockBodyScroll]);

  useEffect(() => {
    if (!expanded) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (shouldCollapseBrokerChartOnKey(true, event.key)) {
        setExpanded(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [expanded]);

  return (
    <div className={`${layout.pageClassName} min-w-0 space-y-3`}>
      {!expanded ? (
        <header className="broker-header">
          <div className="min-w-0">
            <h1 className="flex items-center gap-2">
              <Building2 className="h-5 w-5 shrink-0 text-primary sm:h-6 sm:w-6" />
              <span className="page-title text-xl sm:text-2xl">Corretora</span>
            </h1>
          </div>

          <div className="broker-toolbar">
            <div className="broker-brand" aria-label={BULLEX_BRAND_NAME}>
              <BullexLogo compact className="broker-brand-logo" />
              <span className="sr-only">{BULLEX_BRAND_NAME}</span>
            </div>

            <a
              href={BULLEX_TRADEROOM_URL}
              target="_blank"
              rel="noreferrer"
              className="broker-open-external broker-open-external-primary"
            >
              Abrir Bullex
              <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            </a>
          </div>
        </header>
      ) : null}

      {!expanded && !showFallback ? (
        <p className="broker-login-hint" role="note">
          Se o login dentro do painel voltar para a tela inicial, use{" "}
          <strong>Abrir Bullex</strong> — o navegador bloqueia cookies da
          corretora no gráfico embutido. Para o robô operar, conecte em{" "}
          <strong>Configurações → Conta Corretora</strong>.
        </p>
      ) : null}

      <section className={layout.frameClassName} aria-label={`Gráfico ${BULLEX_BRAND_NAME}`}>
        <div className="broker-frame-bar">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="broker-live-dot" aria-hidden="true" />
            <BullexLogo className="h-4 w-auto max-w-[6.5rem]" />
            <div className="min-w-0 truncate text-sm font-medium text-muted-foreground">
              {BULLEX_BRAND_NAME}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <a
              href={BULLEX_TRADEROOM_URL}
              target="_blank"
              rel="noreferrer"
              className="broker-open-external"
              title="Abrir Bullex em nova aba"
            >
              Abrir
              <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            </a>
            <button
              type="button"
              className="broker-expand-btn"
              aria-label={layout.expandButtonLabel}
              title={layout.expandButtonLabel}
              aria-pressed={expanded}
              onClick={() => setExpanded((current) => !current)}
            >
              {expanded ? (
                <Minimize2 className="h-4 w-4" aria-hidden="true" />
              ) : (
                <Maximize2 className="h-4 w-4" aria-hidden="true" />
              )}
            </button>
            <span className="broker-frame-badge">Ativa</span>
          </div>
        </div>

        <div className="broker-frame-body">
          {showFallback ? (
            <div className="broker-blocked" role="note">
              <ShieldAlert className="broker-blocked-icon" aria-hidden="true" />
              <h2 className="broker-blocked-title">
                O Safari não deixa o gráfico abrir aqui dentro
              </h2>
              <p className="broker-blocked-text">
                Para desenhar o gráfico, a {BULLEX_BRAND_NAME} precisa gravar os
                cookies dela — e o Safari bloqueia cookies de outro site dentro
                do painel. A opção <strong>Impedir rastreamento entre sites</strong>{" "}
                já vem ligada de fábrica no Mac e no iPhone, por isso no
                Windows abre e aqui não.
              </p>
              <a
                href={BULLEX_TRADEROOM_URL}
                target="_blank"
                rel="noreferrer"
                className="broker-blocked-cta"
              >
                Abrir {BULLEX_BRAND_NAME} em nova aba
                <ExternalLink className="h-4 w-4" aria-hidden="true" />
              </a>
              <p className="broker-blocked-note">
                Para o robô operar, o login é outro:{" "}
                <strong>Configurações → Conta Corretora</strong>.
              </p>
              <button
                type="button"
                className="broker-blocked-retry"
                onClick={() => setForceEmbed(true)}
              >
                Tentar carregar aqui mesmo assim
              </button>
            </div>
          ) : (
            <iframe
              title={`${BULLEX_BRAND_NAME} Traderoom`}
              src={BULLEX_TRADEROOM_URL}
              className={layout.iframeClassName}
              allow="clipboard-read; clipboard-write; fullscreen"
              referrerPolicy="strict-origin-when-cross-origin"
            />
          )}
        </div>
      </section>
    </div>
  );
}
