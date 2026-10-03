/**
 * Resolve a identidade da sessão para o cliente HTTP do painel.
 *
 * Três defeitos do caminho antigo (dentro de `api.ts`), todos sentidos como
 * "voltei para a aba e o primeiro clique não funcionou":
 *
 * 1. **Sem fila única.** Ao voltar para a aba, conta, status, estado do robô,
 *    ticket do WS e o clique do usuário chegavam juntos com o cache vencido e
 *    cada um disparava o seu `GET /auth/session` (vistos 6 no mesmo segundo no
 *    log de 02/10/2026). Com o access token expirado, eram 6 renovações
 *    simultâneas com o mesmo refresh token.
 * 2. **Falha de rede virava "não autenticado" por 2 minutos.** Rede ainda
 *    voltando (notebook saindo da suspensão, celular trocando de antena) fazia
 *    o `fetch` lançar; o `null` resultante era gravado no cache e TODA chamada
 *    seguinte respondia `NO_AUTH` sem nem ir à rede.
 * 3. O clique esperava essa leitura inteira antes de sair.
 *
 * Aqui: uma leitura só por vez (as demais esperam a mesma), e rede fora do ar
 * não é resposta — segue com a última identidade conhecida e deixa o servidor
 * decidir pelo cookie, que é quem autentica de verdade.
 */

import type { SessionIdentity } from "./sessionIdentityCache";

export type SessionIdentityRead =
  | { kind: "ok"; identity: SessionIdentity }
  /** O servidor respondeu e disse que não há sessão válida. */
  | { kind: "anonymous" }
  /** Não deu para falar com o servidor (rede, timeout, 5xx). */
  | { kind: "unreachable" };

export interface SessionIdentityResolverDeps {
  /** Lê `GET /auth/session` uma vez. */
  read: () => Promise<SessionIdentityRead>;
  /** `POST /auth/refresh`; true se os cookies foram renovados. */
  renew: () => Promise<boolean>;
  /** Cache compartilhado com `useAuth`: `undefined` = vazio/vencido. */
  readCache: () => SessionIdentity | null | undefined;
  writeCache: (value: SessionIdentity | null) => void;
}

export interface SessionIdentityResolver {
  resolve: () => Promise<SessionIdentity | null>;
}

export function createSessionIdentityResolver(
  deps: SessionIdentityResolverDeps,
): SessionIdentityResolver {
  let inFlight: Promise<SessionIdentity | null> | null = null;
  let lastKnown: SessionIdentity | null = null;

  async function readFromServer(): Promise<SessionIdentity | null> {
    const first = await deps.read();
    if (first.kind === "ok") {
      lastKnown = first.identity;
      deps.writeCache(first.identity);
      return first.identity;
    }
    if (first.kind === "unreachable") {
      // Não grava nada: a próxima chamada tenta de novo em vez de herdar um
      // "não autenticado" que o servidor nunca disse.
      return lastKnown;
    }
    const renewed = await deps.renew();
    if (renewed) {
      const second = await deps.read();
      if (second.kind === "ok") {
        lastKnown = second.identity;
        deps.writeCache(second.identity);
        return second.identity;
      }
      if (second.kind === "unreachable") return lastKnown;
    }
    lastKnown = null;
    deps.writeCache(null);
    return null;
  }

  return {
    resolve(): Promise<SessionIdentity | null> {
      const cached = deps.readCache();
      if (cached !== undefined) {
        if (cached) lastKnown = cached;
        return Promise.resolve(cached);
      }
      if (!inFlight) {
        inFlight = readFromServer().finally(() => {
          inFlight = null;
        });
      }
      return inFlight;
    },
  };
}

/**
 * Compartilha uma operação assíncrona em andamento entre chamadas simultâneas.
 *
 * Usado na renovação de cookies: dois `401` ao mesmo tempo não podem virar dois
 * `POST /auth/refresh` com o mesmo refresh token.
 */
export function singleFlight<T>(run: () => Promise<T>): () => Promise<T> {
  let inFlight: Promise<T> | null = null;
  return () => {
    if (!inFlight) {
      inFlight = run().finally(() => {
        inFlight = null;
      });
    }
    return inFlight;
  };
}
