import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  createSessionIdentityResolver,
  singleFlight,
  type SessionIdentityRead,
} from "./sessionIdentityResolver.ts";
import type { SessionIdentity } from "./sessionIdentityCache.ts";

const SERGIO: SessionIdentity = { userId: "u1", email: "a@b.com" };

function bancada(respostas: SessionIdentityRead[], renova = true) {
  let cache: SessionIdentity | null | undefined;
  const chamadas = { read: 0, renew: 0 };
  const resolver = createSessionIdentityResolver({
    read: async () => {
      chamadas.read += 1;
      await Promise.resolve();
      return respostas.shift() ?? { kind: "unreachable" };
    },
    renew: async () => {
      chamadas.renew += 1;
      return renova;
    },
    readCache: () => cache,
    writeCache: (value) => {
      cache = value;
    },
  });
  return {
    resolver,
    chamadas,
    lerCache: () => cache,
    vencerCache: () => {
      cache = undefined;
    },
  };
}

describe("sessionIdentityResolver", () => {
  it("chamadas simultâneas dividem UMA leitura de /auth/session", async () => {
    // Voltar para a aba dispara conta, status, estado, ticket do WS e o clique
    // juntos: em 02/10/2026 eram 6 GET /auth/session no mesmo segundo.
    const b = bancada([{ kind: "ok", identity: SERGIO }]);
    const todas = await Promise.all([
      b.resolver.resolve(),
      b.resolver.resolve(),
      b.resolver.resolve(),
      b.resolver.resolve(),
      b.resolver.resolve(),
      b.resolver.resolve(),
    ]);
    assert.equal(b.chamadas.read, 1);
    assert.deepEqual(todas, Array(6).fill(SERGIO));
  });

  it("usa o cache sem ir à rede", async () => {
    const b = bancada([{ kind: "ok", identity: SERGIO }]);
    await b.resolver.resolve();
    await b.resolver.resolve();
    assert.equal(b.chamadas.read, 1);
  });

  it("rede fora do ar NÃO vira 'não autenticado' no cache", async () => {
    const b = bancada([{ kind: "unreachable" }, { kind: "ok", identity: SERGIO }]);
    assert.equal(await b.resolver.resolve(), null);
    assert.equal(b.lerCache(), undefined);
    assert.equal(b.chamadas.renew, 0);
    // A chamada seguinte tenta de novo em vez de herdar o erro por 2 minutos.
    assert.deepEqual(await b.resolver.resolve(), SERGIO);
  });

  it("rede fora do ar segue com a última identidade conhecida", async () => {
    const b = bancada([{ kind: "ok", identity: SERGIO }, { kind: "unreachable" }]);
    await b.resolver.resolve();
    b.vencerCache();
    assert.deepEqual(await b.resolver.resolve(), SERGIO);
    assert.equal(b.lerCache(), undefined);
  });

  it("sessão expirada: renova os cookies e lê de novo", async () => {
    const b = bancada([{ kind: "anonymous" }, { kind: "ok", identity: SERGIO }]);
    assert.deepEqual(await b.resolver.resolve(), SERGIO);
    assert.equal(b.chamadas.renew, 1);
    assert.deepEqual(b.lerCache(), SERGIO);
  });

  it("sem refresh válido: grava não autenticado", async () => {
    const b = bancada([{ kind: "anonymous" }], false);
    assert.equal(await b.resolver.resolve(), null);
    assert.equal(b.lerCache(), null);
  });

  it("depois de deslogado não reaproveita a identidade antiga", async () => {
    const b = bancada([{ kind: "ok", identity: SERGIO }, { kind: "anonymous" }, { kind: "unreachable" }], false);
    await b.resolver.resolve();
    b.vencerCache();
    assert.equal(await b.resolver.resolve(), null);
    b.vencerCache();
    assert.equal(await b.resolver.resolve(), null);
  });
});

describe("singleFlight", () => {
  it("dois 401 simultâneos viram um POST /auth/refresh", async () => {
    let chamadas = 0;
    const renovar = singleFlight(async () => {
      chamadas += 1;
      await Promise.resolve();
      return true;
    });
    assert.deepEqual(await Promise.all([renovar(), renovar(), renovar()]), [true, true, true]);
    assert.equal(chamadas, 1);
    await renovar();
    assert.equal(chamadas, 2);
  });
});
