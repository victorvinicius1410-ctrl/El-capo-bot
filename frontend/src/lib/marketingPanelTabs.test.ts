import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  MARKETING_PANEL_TABS,
  tabAfterMarketingHistoryMutation,
} from "./marketingPanelTabs.ts";

describe("marketingPanelTabs", () => {
  it("expõe as três abas na ordem Manual → Placar → Histórico", () => {
    assert.deepEqual(
      MARKETING_PANEL_TABS.map((tab) => tab.id),
      ["manual", "score", "history"],
    );
  });

  it("inclui aba Histórico para acesso à lixeira sem scroll longo", () => {
    const history = MARKETING_PANEL_TABS.find((tab) => tab.id === "history");
    assert.ok(history);
    assert.equal(history.label, "Histórico");
  });

  it("após create/generate bem-sucedido, navega para Histórico", () => {
    assert.equal(tabAfterMarketingHistoryMutation(true), "history");
    assert.equal(tabAfterMarketingHistoryMutation(false), null);
  });
});
