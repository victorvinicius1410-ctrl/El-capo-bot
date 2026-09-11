/**
 * Abas do painel Shift+O (conta marketing).
 * Separadas do componente para testes e documentação.
 */

export type MarketingPanelTab = "manual" | "score" | "history";

export const MARKETING_PANEL_TABS: ReadonlyArray<{
  id: MarketingPanelTab;
  label: string;
}> = [
  { id: "manual", label: "Manual" },
  { id: "score", label: "Placar" },
  { id: "history", label: "Histórico" },
];

/**
 * Decide se, após criar/gerar operações, o painel deve ir para a aba Histórico.
 *
 * Args:
 *   actionSucceeded: se a mutação (create/generate) concluiu com sucesso
 *
 * Returns:
 *   A aba alvo ("history" quando sucesso; null se não deve trocar)
 */
export function tabAfterMarketingHistoryMutation(
  actionSucceeded: boolean,
): MarketingPanelTab | null {
  return actionSucceeded ? "history" : null;
}
