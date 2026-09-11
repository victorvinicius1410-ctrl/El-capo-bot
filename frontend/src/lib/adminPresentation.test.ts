import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  shouldMountLiveTradingProvider,
  shouldShowRobotOverlay,
  shouldTreatSessionAsInactive,
} from "./adminPresentation.ts";

describe("shouldMountLiveTradingProvider", () => {
  it("monta o provider no dashboard do cliente com acesso", () => {
    assert.equal(
      shouldMountLiveTradingProvider({
        impersonating: false,
        isAdminRoute: false,
        accessReady: true,
        hasOperationalAccess: true,
      }),
      true,
    );
  });

  it("monta o provider enquanto /me/access ainda carrega (dashboard chama o hook)", () => {
    assert.equal(
      shouldMountLiveTradingProvider({
        impersonating: false,
        isAdminRoute: false,
        accessReady: false,
        hasOperationalAccess: false,
      }),
      true,
    );
  });

  it("monta o provider na sessão de suporte (admin dentro da conta do lead)", () => {
    assert.equal(
      shouldMountLiveTradingProvider({
        impersonating: true,
        isAdminRoute: false,
        accessReady: true,
        hasOperationalAccess: false,
      }),
      true,
    );
  });

  it("não monta o provider em /admin/* (performance da navegação)", () => {
    assert.equal(
      shouldMountLiveTradingProvider({
        impersonating: false,
        isAdminRoute: true,
        accessReady: true,
        hasOperationalAccess: true,
      }),
      false,
    );
  });

  it("monta o provider para lead inativo (dashboard ainda chama o hook sob o overlay)", () => {
    assert.equal(
      shouldMountLiveTradingProvider({
        impersonating: false,
        isAdminRoute: false,
        accessReady: true,
        hasOperationalAccess: false,
      }),
      true,
    );
  });
});

describe("shouldShowRobotOverlay", () => {
  it("esconde o overlay durante a sessão de suporte", () => {
    assert.equal(
      shouldShowRobotOverlay({
        impersonating: true,
        isAdminRoute: false,
        hasOperationalAccess: true,
      }),
      false,
    );
  });

  it("mostra o overlay no painel operacional do cliente", () => {
    assert.equal(
      shouldShowRobotOverlay({
        impersonating: false,
        isAdminRoute: false,
        hasOperationalAccess: true,
      }),
      true,
    );
  });
});

describe("shouldTreatSessionAsInactive", () => {
  it("não trata sessão de suporte como inativa (lead pendente ainda renderiza o dashboard)", () => {
    assert.equal(
      shouldTreatSessionAsInactive({
        impersonating: true,
        accessReady: true,
        hasOperationalAccess: false,
      }),
      false,
    );
  });

  it("trata lead pendente sem suporte como inativo", () => {
    assert.equal(
      shouldTreatSessionAsInactive({
        impersonating: false,
        accessReady: true,
        hasOperationalAccess: false,
      }),
      true,
    );
  });
});
