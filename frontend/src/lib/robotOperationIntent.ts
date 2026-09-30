/**
 * Intenção do usuário ao clicar em Iniciar/Parar Operação.
 *
 * Relato de 30/09/2026: "às vezes tinha que clicar 3 vezes para parar (e às
 * vezes para iniciar)". O servidor atendia o primeiro clique (23 de 23 com
 * resposta), mas uma atualização atrasada do robô — WebSocket ou consulta
 * periódica, ainda com o estado de antes do clique — chegava logo depois e o
 * botão voltava sozinho. A pessoa clicava de novo.
 *
 * Regra: depois do clique, por {@link OPERATION_INTENT_WINDOW_MS}, estado que
 * CONTRADIZ o clique é tratado como atrasado e o painel mantém o que o usuário
 * pediu. A janela vale inteira mesmo depois da resposta do próprio clique: é
 * justamente DEPOIS dela que chega o estado atrasado. Termina antes só se o
 * servidor recusar o clique. Custo aceito: um stop automático nesses 10 s
 * aparece com até 10 s de atraso.
 */

export const OPERATION_INTENT_WINDOW_MS = 10_000;

interface OperationIntent {
  enabled: boolean;
  until: number;
}

const intents = new Map<string, OperationIntent>();

/** Registra o que o usuário acabou de pedir (true = iniciar, false = parar). */
export function registerOperationIntent(userId: string, enabled: boolean, now = Date.now()): void {
  if (!userId) return;
  intents.set(userId, { enabled, until: now + OPERATION_INTENT_WINDOW_MS });
}

/** Desfaz a intenção (o servidor recusou o clique). */
export function clearOperationIntent(userId: string): void {
  intents.delete(userId);
}

/** Intenção vigente, ou null. */
export function getOperationIntent(userId: string, now = Date.now()): boolean | null {
  const intent = intents.get(userId);
  if (!intent) return null;
  if (now > intent.until) {
    intents.delete(userId);
    return null;
  }
  return intent.enabled;
}

/**
 * Aplica a intenção a um estado que chegou do servidor.
 *
 * @returns O próprio estado quando ele já confirma o clique ou quando não há
 *   intenção; senão uma cópia com ``enabled`` igual ao que o usuário pediu.
 */
export function applyOperationIntent<T extends { enabled?: boolean | null; worker_running?: boolean | null }>(
  userId: string,
  state: T,
  now = Date.now(),
): T {
  const wanted = getOperationIntent(userId, now);
  if (wanted === null || state == null) return state;
  if (Boolean(state.enabled) === wanted) return state;
  return { ...state, enabled: wanted, worker_running: wanted ? state.worker_running : false };
}

/** Só para testes. */
export function resetOperationIntentsForTests(): void {
  intents.clear();
}
