/** URL canônica do traderoom Bullex (iframe + abertura em nova aba). */
export const BULLEX_TRADEROOM_URL = "https://trade.bull-ex.com/traderoom";

export interface BrokerChartLayout {
  expanded: boolean;
  pageClassName: string;
  frameClassName: string;
  iframeClassName: string;
  expandButtonLabel: string;
  lockBodyScroll: boolean;
  compressShellMenu: boolean;
  robotZIndexClass: string;
}

/** Resolve classes e comportamento do gráfico conforme a expansão. */
export function getBrokerChartLayout(expanded: boolean): BrokerChartLayout {
  return {
    expanded,
    pageClassName: expanded ? "broker-page broker-page-expanded" : "broker-page",
    frameClassName: expanded ? "broker-frame broker-frame-expanded" : "broker-frame",
    iframeClassName: expanded ? "broker-iframe broker-iframe-expanded" : "broker-iframe",
    expandButtonLabel: expanded ? "Recolher gráfico" : "Expandir gráfico",
    lockBodyScroll: expanded,
    compressShellMenu: expanded,
    robotZIndexClass: "z-[60]",
  };
}

/** Indica se a tecla pressionada recolhe o gráfico expandido. */
export function shouldCollapseBrokerChartOnKey(_expanded: boolean, key: string): boolean {
  return key === "Escape";
}

/**
 * Indica se o navegador bloqueia o traderoom embutido.
 *
 * O bootstrap da Bullex lê e grava `document.cookie` e faz um
 * `fetch(..., credentials: "include")` para `api.trade.bull-ex.com` **antes**
 * de desenhar o gráfico. Dentro do nosso iframe tudo isso é terceiro-parte, e
 * o Safari/WebKit bloqueia por padrão (ITP — "Impedir rastreamento entre
 * sites" vem ligado de fábrica). Resultado: volta ao login ou fica preto.
 *
 * Chrome/Edge/Firefox ainda entregam o cookie, por isso no Windows funciona.
 * Ver docs/CORRETORA.md.
 *
 * Args:
 *   userAgent: string do navegador (opcional; default `navigator.userAgent`)
 *
 * Returns:
 *   true quando o UA é WebKit puro (Safari desktop) ou iOS
 */
export function isBrokerIframeBlockedByBrowser(userAgent?: string): boolean {
  const ua = userAgent ?? (typeof navigator !== "undefined" ? navigator.userAgent : "");
  if (!ua) return false;
  if (/iPad|iPhone|iPod/.test(ua)) return true;
  return (
    /\bSafari\b/.test(ua) &&
    !/\bChrome\b|\bChromium\b|\bCriOS\b|\bFxiOS\b|\bEdg\b|\bOPR\b/.test(ua)
  );
}
