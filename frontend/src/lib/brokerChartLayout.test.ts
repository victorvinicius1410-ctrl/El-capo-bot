import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  BULLEX_TRADEROOM_URL,
  getBrokerChartLayout,
  isBrokerIframeBlockedByBrowser,
  shouldCollapseBrokerChartOnKey,
} from "./brokerChartLayout.ts";

describe("getBrokerChartLayout", () => {
  it("mantém classes do iframe recolhido e expandido", () => {
    const collapsed = getBrokerChartLayout(false);
    assert.equal(collapsed.expanded, false);
    assert.match(collapsed.iframeClassName, /broker-iframe$/);
    assert.equal(collapsed.lockBodyScroll, false);

    const expanded = getBrokerChartLayout(true);
    assert.equal(expanded.expanded, true);
    assert.match(expanded.iframeClassName, /broker-iframe-expanded/);
    assert.equal(expanded.lockBodyScroll, true);
  });
});

describe("shouldCollapseBrokerChartOnKey", () => {
  it("só Escape recolhe o gráfico", () => {
    assert.equal(shouldCollapseBrokerChartOnKey(true, "Escape"), true);
    assert.equal(shouldCollapseBrokerChartOnKey(true, "Enter"), false);
  });
});

describe("BULLEX_TRADEROOM_URL", () => {
  it("aponta para o traderoom oficial da Bullex", () => {
    assert.equal(BULLEX_TRADEROOM_URL, "https://trade.bull-ex.com/traderoom");
  });
});

const UA = {
  safariMac:
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
  chromeWindows:
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
  edgeWindows:
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0",
  chromeMac:
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
  firefox:
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
  safariIOS:
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
  chromeIOS:
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/128.0.0.0 Mobile/15E148 Safari/604.1",
};

describe("isBrokerIframeBlockedByBrowser", () => {
  it("bloqueia no Safari do Mac e no iOS (ITP mata o cookie da corretora)", () => {
    assert.equal(isBrokerIframeBlockedByBrowser(UA.safariMac), true);
    assert.equal(isBrokerIframeBlockedByBrowser(UA.safariIOS), true);
    // WebKit obrigatório no iOS: Chrome/iOS também é bloqueado.
    assert.equal(isBrokerIframeBlockedByBrowser(UA.chromeIOS), true);
  });

  it("libera onde o cookie de terceiro ainda passa", () => {
    assert.equal(isBrokerIframeBlockedByBrowser(UA.chromeWindows), false);
    assert.equal(isBrokerIframeBlockedByBrowser(UA.edgeWindows), false);
    assert.equal(isBrokerIframeBlockedByBrowser(UA.chromeMac), false);
    assert.equal(isBrokerIframeBlockedByBrowser(UA.firefox), false);
  });

  it("sem UA não assume bloqueio", () => {
    assert.equal(isBrokerIframeBlockedByBrowser(""), false);
  });
});
