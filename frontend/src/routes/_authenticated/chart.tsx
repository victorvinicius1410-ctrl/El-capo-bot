import { useEffect, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { Building2, ExternalLink, Maximize2, Minimize2 } from "lucide-react";
import { BullexLogo, BULLEX_BRAND_NAME } from "@/components/BullexLogo";
import {
  getBrokerChartLayout,
  shouldCollapseBrokerChartOnKey,
} from "@/lib/brokerChartLayout";

const BULLEX_TRADEROOM_URL = "https://trade.bull-ex.com/traderoom";

export const Route = createFileRoute("/_authenticated/chart")({
  ssr: false,
  head: () => ({ meta: [{ title: "Corretora - ElCapo AutoBot" }] }),
  component: ChartPage,
});

function ChartPage() {
  const [expanded, setExpanded] = useState(false);
  const layout = getBrokerChartLayout(expanded);

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
              className="broker-open-external"
            >
              Abrir
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          </div>
        </header>
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
          <iframe
            title={`${BULLEX_BRAND_NAME} Traderoom`}
            src={BULLEX_TRADEROOM_URL}
            className={layout.iframeClassName}
            allow="clipboard-read; clipboard-write; fullscreen"
          />
        </div>
      </section>
    </div>
  );
}
