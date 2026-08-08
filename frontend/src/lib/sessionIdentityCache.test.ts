import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";
import {
  clearSessionIdentityCache,
  readSessionIdentityCache,
  sessionIdentityCacheTtlMs,
  writeSessionIdentityCache,
} from "./sessionIdentityCache.ts";

describe("sessionIdentityCache", () => {
  beforeEach(() => {
    clearSessionIdentityCache();
  });

  it("retorna undefined quando vazio", () => {
    assert.equal(readSessionIdentityCache(1_000), undefined);
  });

  it("devolve identidade dentro do TTL", () => {
    writeSessionIdentityCache({ userId: "u1", email: "a@b.com" }, 120_000, 1_000);
    assert.deepEqual(readSessionIdentityCache(5_000), { userId: "u1", email: "a@b.com" });
  });

  it("expira após o TTL", () => {
    writeSessionIdentityCache({ userId: "u1", email: null }, 120_000, 1_000);
    assert.equal(readSessionIdentityCache(121_001), undefined);
  });

  it("clear remove imediatamente", () => {
    writeSessionIdentityCache({ userId: "u1", email: null }, 120_000, 1_000);
    clearSessionIdentityCache();
    assert.equal(readSessionIdentityCache(1_500), undefined);
  });

  it("permite cachear null (não autenticado)", () => {
    writeSessionIdentityCache(null, 120_000, 1_000);
    assert.equal(readSessionIdentityCache(2_000), null);
  });

  it("TTL padrão é 120s (reduz carga no gateway)", () => {
    assert.equal(sessionIdentityCacheTtlMs(), 120_000);
  });
});
