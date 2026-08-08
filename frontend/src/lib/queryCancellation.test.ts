import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { CancelledError } from "@tanstack/query-core";
import {
  isBenignRouteLoadError,
  isQueryCancellationError,
} from "./queryCancellation.ts";

describe("isQueryCancellationError", () => {
  it("reconhece CancelledError do TanStack Query", () => {
    assert.equal(isQueryCancellationError(new CancelledError()), true);
  });

  it("reconhece erro com nome CancelledError (bundle minificado)", () => {
    const err = new Error("CancelledError");
    err.name = "CancelledError";
    assert.equal(isQueryCancellationError(err), true);
  });

  it("nao trata ApiError / Error generico como cancelamento", () => {
    assert.equal(isQueryCancellationError(new Error("boom")), false);
    assert.equal(isQueryCancellationError({ code: "FORBIDDEN" }), false);
    assert.equal(isQueryCancellationError(null), false);
  });
});

describe("isBenignRouteLoadError", () => {
  it("trata null/undefined como recuperavel (evita tela azul)", () => {
    assert.equal(isBenignRouteLoadError(undefined), true);
    assert.equal(isBenignRouteLoadError(null), true);
  });

  it("trata CancelledError como recuperavel", () => {
    assert.equal(isBenignRouteLoadError(new CancelledError()), true);
  });

  it("nao mascara erros reais de negocio", () => {
    assert.equal(isBenignRouteLoadError(new Error("boom")), false);
    assert.equal(isBenignRouteLoadError({ code: "FORBIDDEN" }), false);
  });
});
