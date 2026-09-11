import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  PRODUCTION_API_BASE_URL,
  STAGING_API_BASE_URL,
  resolveApiBaseUrl,
} from "./apiBaseUrl.ts";

describe("resolveApiBaseUrl", () => {
  it("aceita API de produção", () => {
    assert.equal(
      resolveApiBaseUrl("https://api.elcapobot.online"),
      PRODUCTION_API_BASE_URL,
    );
  });

  it("aceita API de staging elcapo2.shop", () => {
    assert.equal(
      resolveApiBaseUrl("https://api.elcapo2.shop/"),
      STAGING_API_BASE_URL,
    );
  });

  it("aceita localhost http", () => {
    assert.equal(resolveApiBaseUrl("http://127.0.0.1:8080"), "http://127.0.0.1:8080");
  });

  it("rejeita host desconhecido e cai em produção", () => {
    assert.equal(
      resolveApiBaseUrl("https://evil.example.com"),
      PRODUCTION_API_BASE_URL,
    );
  });

  it("undefined cai em produção", () => {
    assert.equal(resolveApiBaseUrl(undefined), PRODUCTION_API_BASE_URL);
  });
});
