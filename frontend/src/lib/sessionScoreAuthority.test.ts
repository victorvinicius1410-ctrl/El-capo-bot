/**
 * Operação excluída não pode voltar ao placar do El Capo pelo lado do painel.
 *
 * O backend já tem a marca de baixa intencional; o painel tinha a MESMA regra
 * "nunca rebaixa" e rejeitava o placar já corrigido. Cada teste aqui é um jeito
 * pelo qual a operação voltava.
 */

import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";
import {
  SESSION_SCORE_AUTHORITY_TTL_MS,
  clearSessionScoreAuthority,
  getSessionScoreAuthority,
  markSessionScoreAuthority,
  resetSessionScoreGateForTests,
  resolveSessionScoreGate,
} from "./sessionScoreAuthority.ts";

class MemoryStorage {
  private data = new Map<string, string>();
  get length(): number {
    return this.data.size;
  }
  clear(): void {
    this.data.clear();
  }
  getItem(key: string): string | null {
    return this.data.has(key) ? (this.data.get(key) as string) : null;
  }
  key(index: number): string | null {
    return [...this.data.keys()][index] ?? null;
  }
  removeItem(key: string): void {
    this.data.delete(key);
  }
  setItem(key: string, value: string): void {
    this.data.set(key, value);
  }
}

const USER = "marketing-user";

function score(wins: number, losses: number, profit: number) {
  return { wins, losses, profit };
}

function installStorage(storage: Storage | null): void {
  (globalThis as { window?: unknown }).window =
    storage === null
      ? {
          get localStorage(): Storage {
            throw new Error("cookies bloqueados");
          },
        }
      : { localStorage: storage };
}

beforeEach(() => {
  resetSessionScoreGateForTests();
  installStorage(new MemoryStorage() as unknown as Storage);
});

describe("marca de baixa intencional", () => {
  it("grava e lê o placar de antes e depois da exclusão", () => {
    markSessionScoreAuthority(USER, score(5, 3, 25), score(4, 3, 16.3));
    const stored = getSessionScoreAuthority(USER);
    assert.deepEqual(stored?.before, score(5, 3, 25));
    assert.deepEqual(stored?.after, score(4, 3, 16.3));
  });

  it("expira sozinha", () => {
    markSessionScoreAuthority(USER, score(5, 3, 25), score(4, 3, 16.3));
    const raw = JSON.parse(
      (globalThis as unknown as { window: { localStorage: Storage } }).window.localStorage.getItem(
        `elcapo:score-authority:${USER}`,
      ) as string,
    );
    raw.expiresAt = Date.now() - 1;
    (globalThis as unknown as { window: { localStorage: Storage } }).window.localStorage.setItem(
      `elcapo:score-authority:${USER}`,
      JSON.stringify(raw),
    );
    assert.equal(getSessionScoreAuthority(USER), null);
  });

  it("TTL espelha o do backend (120s)", () => {
    assert.equal(SESSION_SCORE_AUTHORITY_TTL_MS, 120_000);
  });

  it("clear apaga a marca", () => {
    markSessionScoreAuthority(USER, score(5, 3, 25), score(4, 3, 16.3));
    clearSessionScoreAuthority(USER);
    assert.equal(getSessionScoreAuthority(USER), null);
  });

  it("não quebra quando o localStorage lança (aba anônima)", () => {
    installStorage(null);
    markSessionScoreAuthority(USER, score(5, 3, 25), score(4, 3, 16.3));
    assert.equal(getSessionScoreAuthority(USER), null);
    const gate = resolveSessionScoreGate(USER, score(5, 3, 25), score(4, 3, 16.3));
    assert.equal(gate.allowScoreDecrease, false);
  });

  it("descarta marca corrompida no storage", () => {
    (globalThis as unknown as { window: { localStorage: Storage } }).window.localStorage.setItem(
      `elcapo:score-authority:${USER}`,
      "{isso não é json",
    );
    assert.equal(getSessionScoreAuthority(USER), null);
  });
});

describe("resolveSessionScoreGate com baixa registrada", () => {
  beforeEach(() => {
    markSessionScoreAuthority(USER, score(5, 3, 25), score(4, 3, 16.3));
  });

  it("aceita na hora o placar já corrigido pelo servidor", () => {
    const gate = resolveSessionScoreGate(USER, score(5, 3, 25), score(4, 3, 16.3));
    assert.equal(gate.allowScoreDecrease, true);
    assert.equal(gate.rejectAsStale, false);
  });

  it("libera a marca depois que o servidor confirma", () => {
    resolveSessionScoreGate(USER, score(5, 3, 25), score(4, 3, 16.3));
    assert.equal(getSessionScoreAuthority(USER), null);
  });

  it("descarta a réplica atrasada com o placar de antes da exclusão", () => {
    const gate = resolveSessionScoreGate(USER, score(4, 3, 16.3), score(5, 3, 25));
    assert.equal(gate.rejectAsStale, true);
    assert.equal(gate.allowScoreDecrease, false);
  });

  it("aguenta a réplica atrasada chegando várias vezes", () => {
    for (let i = 0; i < 10; i += 1) {
      const gate = resolveSessionScoreGate(USER, score(4, 3, 16.3), score(5, 3, 25));
      assert.equal(gate.rejectAsStale, true);
    }
    assert.notEqual(getSessionScoreAuthority(USER), null);
  });

  it("aceita placar ainda menor (segunda exclusão em sequência)", () => {
    const gate = resolveSessionScoreGate(USER, score(4, 3, 16.3), score(3, 3, 7.6));
    assert.equal(gate.allowScoreDecrease, true);
  });

  it("não bloqueia um WIN novo, maior que o placar da baixa", () => {
    const gate = resolveSessionScoreGate(USER, score(4, 3, 16.3), score(5, 4, 20));
    assert.equal(gate.allowScoreDecrease, false);
    assert.equal(gate.rejectAsStale, false);
  });

  it("vale para outra aba do mesmo navegador", () => {
    // A outra aba nunca chamou mark — lê a marca do localStorage.
    resetSessionScoreGateForTests();
    const gate = resolveSessionScoreGate(USER, score(5, 3, 25), score(4, 3, 16.3));
    assert.equal(gate.allowScoreDecrease, true);
  });
});

describe("resolveSessionScoreGate sem marca (confirmação)", () => {
  it("segura a primeira queda — pode ser snapshot atrasado", () => {
    const gate = resolveSessionScoreGate(USER, score(5, 3, 42), score(2, 0, 12));
    assert.equal(gate.allowScoreDecrease, false);
  });

  it("aceita a queda quando ela se repete", () => {
    // Sem isso a rejeição era permanente: o cache guardava o valor alto e toda
    // comparação seguinte era contra ele. Exclusão feita noutro navegador
    // nunca chegava a esta aba.
    resolveSessionScoreGate(USER, score(5, 3, 42), score(4, 3, 33.3));
    const gate = resolveSessionScoreGate(USER, score(5, 3, 42), score(4, 3, 33.3));
    assert.equal(gate.allowScoreDecrease, true);
  });

  it("um placar diferente reinicia a confirmação", () => {
    resolveSessionScoreGate(USER, score(5, 3, 42), score(4, 3, 33.3));
    const gate = resolveSessionScoreGate(USER, score(5, 3, 42), score(2, 0, 12));
    assert.equal(gate.allowScoreDecrease, false);
  });

  it("subida limpa a confirmação pendente", () => {
    resolveSessionScoreGate(USER, score(5, 3, 42), score(4, 3, 33.3));
    resolveSessionScoreGate(USER, score(5, 3, 42), score(6, 3, 50));
    const gate = resolveSessionScoreGate(USER, score(5, 3, 42), score(4, 3, 33.3));
    assert.equal(gate.allowScoreDecrease, false);
  });

  it("não interfere quando o placar sobe", () => {
    const gate = resolveSessionScoreGate(USER, score(5, 3, 42), score(6, 3, 50));
    assert.equal(gate.allowScoreDecrease, false);
    assert.equal(gate.rejectAsStale, false);
  });

  it("sem placar anterior não há o que decidir", () => {
    const gate = resolveSessionScoreGate(USER, null, score(4, 3, 16.3));
    assert.equal(gate.allowScoreDecrease, false);
    assert.equal(gate.rejectAsStale, false);
  });

  it("sem usuário não há o que decidir", () => {
    const gate = resolveSessionScoreGate(null, score(5, 3, 42), score(4, 3, 33.3));
    assert.equal(gate.allowScoreDecrease, false);
  });
});
