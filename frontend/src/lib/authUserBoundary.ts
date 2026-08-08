/**
 * Planeja a reação do AuthUserBoundary quando o id do usuário autenticado muda.
 *
 * O gate (desmontar Outlet → spinner → remontar) só deve ocorrer em troca real
 * entre dois usuários concretos. No primeiro bind e no login (null → user) o
 * unmount agressivo dispara NotFoundError removeChild no Chrome quando
 * extensões (ex.: Tradutor do Google) alteram o DOM.
 *
 * `cancelInFlightQueries` só deve ser true na troca userA→userB. No primeiro
 * bind / login, `cancelQueries()` aborta o `ensureQueryData` do beforeLoad de
 * `/admin` e o TanStack Router sobe CancelledError no errorComponent.
 */
export type AuthIdentityTransition = {
  shouldGateRender: boolean;
  shouldResetState: boolean;
  /**
   * Se true, cancela queries em voo e limpa o QueryClient.
   * Se false (soft reset), só limpa stores locais (Bullex/robô) — seguro
   * enquanto o router carrega `/me/access`.
   */
  cancelInFlightQueries: boolean;
};

export function planAuthIdentityTransition(
  previousUserId: string | null | undefined,
  nextUserId: string | null,
): AuthIdentityTransition {
  if (previousUserId === nextUserId) {
    return {
      shouldGateRender: false,
      shouldResetState: false,
      cancelInFlightQueries: false,
    };
  }

  // Primeira resolução da sessão (undefined → user|null): limpa stores locais
  // sem cancelar queries — o beforeLoad de /admin já pode ter ensureQueryData
  // em voo na mesma pintura.
  if (previousUserId === undefined) {
    return {
      shouldGateRender: false,
      shouldResetState: true,
      cancelInFlightQueries: false,
    };
  }

  // Login (null → user) ou logout (user → null): limpa stores locais sem
  // cancelQueries. O logoutSession já faz queryClient.clear(); o redirect
  // pós-login não deve abortar o gate de /admin.
  if (previousUserId === null || nextUserId === null) {
    return {
      shouldGateRender: false,
      shouldResetState: true,
      cancelInFlightQueries: false,
    };
  }

  // Troca concreta userA → userB (ex.: sessão recriada com outro id).
  return {
    shouldGateRender: true,
    shouldResetState: true,
    cancelInFlightQueries: true,
  };
}
