import assert from "node:assert/strict";
import { describe, it, beforeEach } from "node:test";
import {
  forgetAccountCurrency,
  isKnownAccountCurrency,
  recallAccountCurrency,
  rememberAccountCurrency,
  resetAccountCurrencyMemo,
} from "./accountCurrencyMemo.ts";

function fakeStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key: string) => data.get(key) ?? null,
    key: (index: number) => Array.from(data.keys())[index] ?? null,
    removeItem: (key: string) => {
      data.delete(key);
    },
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
  } as Storage;
}

function installStorage(storage: Storage | undefined): void {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: storage,
  });
}

describe("accountCurrencyMemo", () => {
  beforeEach(() => {
    resetAccountCurrencyMemo();
    installStorage(fakeStorage());
  });

  it("reconhece só código de moeda preenchido", () => {
    assert.equal(isKnownAccountCurrency("BRL"), true);
    assert.equal(isKnownAccountCurrency("usd"), true);
    assert.equal(isKnownAccountCurrency(""), false);
    assert.equal(isKnownAccountCurrency("   "), false);
    assert.equal(isKnownAccountCurrency(null), false);
    assert.equal(isKnownAccountCurrency(undefined), false);
  });

  it("lembra a moeda depois que a conta desconecta", () => {
    rememberAccountCurrency("u-brl", "BRL");
    assert.equal(recallAccountCurrency("u-brl"), "BRL");
  });

  it("normaliza o código e separa por usuário", () => {
    rememberAccountCurrency("u-usd", "us$");
    rememberAccountCurrency("u-brl", "BRL");
    assert.equal(recallAccountCurrency("u-usd"), "USD");
    assert.equal(recallAccountCurrency("u-brl"), "BRL");
    assert.equal(recallAccountCurrency("u-novo"), null);
  });

  it("não grava moeda vazia por cima da conhecida", () => {
    rememberAccountCurrency("u-brl", "BRL");
    rememberAccountCurrency("u-brl", "");
    rememberAccountCurrency("u-brl", null);
    assert.equal(recallAccountCurrency("u-brl"), "BRL");
  });

  it("sobrescreve quando a conta conectada troca de moeda", () => {
    rememberAccountCurrency("u-1", "BRL");
    rememberAccountCurrency("u-1", "USD");
    assert.equal(recallAccountCurrency("u-1"), "USD");
  });

  it("esquece quando pedido", () => {
    rememberAccountCurrency("u-1", "BRL");
    forgetAccountCurrency("u-1");
    assert.equal(recallAccountCurrency("u-1"), null);
  });

  it("sobrevive ao reload lendo do storage", () => {
    rememberAccountCurrency("u-brl", "BRL");
    resetAccountCurrencyMemo();
    assert.equal(recallAccountCurrency("u-brl"), "BRL");
  });

  it("não quebra sem storage (aba anônima/SSR)", () => {
    installStorage(undefined);
    rememberAccountCurrency("u-brl", "BRL");
    assert.equal(recallAccountCurrency("u-brl"), "BRL");
    resetAccountCurrencyMemo();
    assert.equal(recallAccountCurrency("u-brl"), null);
  });

  it("ignora usuário ausente", () => {
    rememberAccountCurrency(null, "BRL");
    assert.equal(recallAccountCurrency(null), null);
    assert.equal(recallAccountCurrency(undefined), null);
  });
});
