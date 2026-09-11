/**
 * Marca de baixa intencional do placar, no cliente.
 *
 * O painel também tem a regra "nunca rebaixa" (`preserveRobotSessionScore`):
 * um snapshot de controle atrasado do gateway (5x3 → 2x0) não pode derrubar o
 * placar vivo. A regra está certa para atraso e errada para EXCLUSÃO — a única
 * operação que baixa o placar de propósito. Sem saber que a queda foi pedida
 * pelo usuário, o painel rejeitava o placar já corrigido pelo servidor e a
 * operação excluída continuava no El Capo até o F5.
 *
 * O caso pior era excluir a ÚLTIMA operação: 1x0 → 0x0 caía no ramo "snapshot
 * chegou em branco" e o placar antigo voltava para sempre.
 *
 * Esta marca é o espelho de `robot:score_authority:{user_id}` no backend
 * (mesmo TTL). Fica em `localStorage` para valer também nas outras abas — o
 * placar é o mesmo em todas — e some sozinha se o processo morrer no meio.
 */

export const SESSION_SCORE_AUTHORITY_TTL_MS = 120_000;

const STORAGE_PREFIX = "elcapo:score-authority:";

export interface SessionScore {
  wins: number;
  losses: number;
  profit: number;
}

interface StoredAuthority {
  /** Placar exibido ANTES da exclusão — é o valor que as réplicas atrasadas repetem. */
  before: SessionScore;
  /** Placar correto DEPOIS da exclusão. */
  after: SessionScore;
  expiresAt: number;
}

export interface SessionScoreGate {
  /** Aceitar um placar menor que o exibido (a queda foi pedida). */
  allowScoreDecrease: boolean;
  /** Descartar o placar recebido: é a réplica atrasada repetindo o valor antigo. */
  rejectAsStale: boolean;
}

/** Queda ainda não confirmada, por usuário (memória do módulo). */
const pendingDecrease = new Map<string, SessionScore>();

function readStorage(): Storage | null {
  // Aba anônima / cookies bloqueados fazem o acesso lançar, não devolver null.
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function storageKey(userId: string): string {
  return `${STORAGE_PREFIX}${userId}`;
}

function normalize(score: SessionScore): SessionScore {
  return {
    wins: Math.max(0, Math.trunc(Number(score.wins) || 0)),
    losses: Math.max(0, Math.trunc(Number(score.losses) || 0)),
    profit: Number.isFinite(Number(score.profit)) ? Number(score.profit) : 0,
  };
}

function total(score: Pick<SessionScore, "wins" | "losses">): number {
  return score.wins + score.losses;
}

function sameScore(
  a: Pick<SessionScore, "wins" | "losses">,
  b: Pick<SessionScore, "wins" | "losses">,
): boolean {
  return a.wins === b.wins && a.losses === b.losses;
}

/**
 * Registra que o placar caiu porque o usuário excluiu/reescreveu operações.
 *
 * @param userId - Usuário autenticado
 * @param before - Placar exibido antes da baixa
 * @param after - Placar correto depois da baixa
 */
export function markSessionScoreAuthority(
  userId: string | null | undefined,
  before: SessionScore,
  after: SessionScore,
): void {
  if (!userId) return;
  pendingDecrease.delete(userId);
  const storage = readStorage();
  if (!storage) return;
  const entry: StoredAuthority = {
    before: normalize(before),
    after: normalize(after),
    expiresAt: Date.now() + SESSION_SCORE_AUTHORITY_TTL_MS,
  };
  try {
    storage.setItem(storageKey(userId), JSON.stringify(entry));
  } catch {
    // Cota cheia: sem a marca o fallback de confirmação ainda cobre a queda.
  }
}

/** Lê a marca vigente, ou null se não existe / expirou. */
export function getSessionScoreAuthority(
  userId: string | null | undefined,
): StoredAuthority | null {
  if (!userId) return null;
  const storage = readStorage();
  if (!storage) return null;
  let raw: string | null = null;
  try {
    raw = storage.getItem(storageKey(userId));
  } catch {
    return null;
  }
  if (!raw) return null;
  let parsed: StoredAuthority | null = null;
  try {
    parsed = JSON.parse(raw) as StoredAuthority;
  } catch {
    parsed = null;
  }
  if (!parsed || typeof parsed.expiresAt !== "number" || !parsed.before || !parsed.after) {
    clearSessionScoreAuthority(userId);
    return null;
  }
  if (parsed.expiresAt <= Date.now()) {
    clearSessionScoreAuthority(userId);
    return null;
  }
  return parsed;
}

/** Libera a marca (servidor já confirmou, ou "Reiniciar placar"). */
export function clearSessionScoreAuthority(userId: string | null | undefined): void {
  if (!userId) return;
  pendingDecrease.delete(userId);
  const storage = readStorage();
  if (!storage) return;
  try {
    storage.removeItem(storageKey(userId));
  } catch {
    // Nada a fazer: a marca expira sozinha.
  }
}

/**
 * Decide o que fazer com um placar que chegou do servidor.
 *
 * Duas travas, nesta ordem:
 * 1. **Marca de baixa** — o usuário excluiu nesta ou noutra aba: a queda é
 *    aceita na hora e a réplica atrasada repetindo o placar anterior é
 *    descartada.
 * 2. **Confirmação** — sem marca (exclusão feita noutro navegador, aba aberta
 *    depois, ajuste pelo admin), uma queda que se repete no update seguinte é
 *    real e passa. Sem isso a rejeição seria permanente: o cache guardava o
 *    valor alto e toda comparação seguinte era contra ele.
 *
 * @param userId - Usuário autenticado
 * @param previous - Placar já exibido no painel
 * @param incoming - Placar recebido (HTTP ou WS)
 * @returns O que fazer com a queda
 */
export function resolveSessionScoreGate(
  userId: string | null | undefined,
  previous: SessionScore | null | undefined,
  incoming: SessionScore,
): SessionScoreGate {
  const nothingToDo: SessionScoreGate = { allowScoreDecrease: false, rejectAsStale: false };
  if (!userId || !previous) return nothingToDo;

  const authority = getSessionScoreAuthority(userId);
  if (authority) {
    if (sameScore(incoming, authority.after)) {
      // Servidor confirmou a baixa: a marca cumpriu o papel.
      clearSessionScoreAuthority(userId);
      return { allowScoreDecrease: true, rejectAsStale: false };
    }
    if (sameScore(incoming, authority.before)) {
      // Réplica atrasada repetindo o placar de antes da exclusão.
      return { allowScoreDecrease: false, rejectAsStale: true };
    }
    if (total(incoming) <= total(authority.after)) {
      return { allowScoreDecrease: true, rejectAsStale: false };
    }
    return nothingToDo;
  }

  if (total(incoming) >= total(previous)) {
    pendingDecrease.delete(userId);
    return nothingToDo;
  }
  const pending = pendingDecrease.get(userId);
  if (pending && sameScore(pending, incoming)) {
    pendingDecrease.delete(userId);
    return { allowScoreDecrease: true, rejectAsStale: false };
  }
  pendingDecrease.set(userId, normalize(incoming));
  return nothingToDo;
}

/** Só para testes: limpa a memória de quedas pendentes. */
export function resetSessionScoreGateForTests(): void {
  pendingDecrease.clear();
}
