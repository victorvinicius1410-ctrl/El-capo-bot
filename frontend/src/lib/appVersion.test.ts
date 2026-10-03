import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  APP_VERSION_IDLE_BEFORE_RELOAD_MS,
  CHUNK_RELOAD_MIN_INTERVAL_MS,
  canReloadForNewVersion,
  chunkReloadAllowed,
  isChunkLoadError,
  isDifferentBuild,
  parseBuildId,
} from "./appVersion.ts";

describe("parseBuildId", () => {
  it("lê o id do version.json", () => {
    assert.equal(parseBuildId({ build: "mg9x2k" }), "mg9x2k");
  });
  it("devolve null para resposta ilegível (ex.: index.html no lugar do JSON)", () => {
    assert.equal(parseBuildId(null), null);
    assert.equal(parseBuildId("<!DOCTYPE html>"), null);
    assert.equal(parseBuildId({ build: "" }), null);
    assert.equal(parseBuildId({ build: 7 }), null);
  });
});

describe("isDifferentBuild", () => {
  it("acusa publicação nova", () => {
    assert.equal(isDifferentBuild("a1", "b2"), true);
  });
  it("mesma publicação não recarrega", () => {
    assert.equal(isDifferentBuild("a1", "a1"), false);
  });
  it("na dúvida não recarrega", () => {
    assert.equal(isDifferentBuild(undefined, "b2"), false);
    assert.equal(isDifferentBuild("a1", null), false);
    assert.equal(isDifferentBuild("", "b2"), false);
  });
});

describe("canReloadForNewVersion", () => {
  const parado = { hidden: false, idleMs: APP_VERSION_IDLE_BEFORE_RELOAD_MS, dialogOpen: false, typing: false };
  it("aba oculta recarrega na hora", () => {
    assert.equal(canReloadForNewVersion({ ...parado, hidden: true, idleMs: 0 }), true);
  });
  it("aba oculta com diálogo aberto espera (não perde o rascunho)", () => {
    assert.equal(canReloadForNewVersion({ ...parado, hidden: true, dialogOpen: true }), false);
  });
  it("aba à vista só com o usuário parado", () => {
    assert.equal(canReloadForNewVersion(parado), true);
    assert.equal(canReloadForNewVersion({ ...parado, idleMs: 5_000 }), false);
  });
  it("não recarrega com o Iniciar Operação aberto nem no meio da digitação", () => {
    assert.equal(canReloadForNewVersion({ ...parado, dialogOpen: true }), false);
    assert.equal(canReloadForNewVersion({ ...parado, typing: true }), false);
  });
});

describe("chunkReloadAllowed", () => {
  it("primeira falha recarrega", () => {
    assert.equal(chunkReloadAllowed(null, 1_000), true);
  });
  it("não entra em laço se o chunk continua faltando", () => {
    assert.equal(chunkReloadAllowed(1_000, 1_000 + 5_000), false);
    assert.equal(chunkReloadAllowed(1_000, 1_000 + CHUNK_RELOAD_MIN_INTERVAL_MS), true);
  });
});

describe("isChunkLoadError", () => {
  it("reconhece o erro de chunk apagado nos três navegadores", () => {
    assert.equal(
      isChunkLoadError(new TypeError("Failed to fetch dynamically imported module: https://app/assets/configuracoes-DKx_HQMM.js")),
      true,
    );
    assert.equal(isChunkLoadError(new TypeError("error loading dynamically imported module")), true);
    assert.equal(isChunkLoadError(new TypeError("Importing a module script failed.")), true);
  });
  it("não confunde com erro comum", () => {
    assert.equal(isChunkLoadError(new Error("Falha de comunicação com a API")), false);
    assert.equal(isChunkLoadError(undefined), false);
  });
});
