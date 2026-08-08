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
