/**
 * Texto da confirmação do "Reiniciar placar".
 *
 * O botão fica logo abaixo de Iniciar/Parar Operação e zerava o placar do dia
 * no primeiro clique, sem pergunta. Em 23/09/2026 os clientes que "pararam e
 * iniciaram de novo e viram o placar zerado" tinham todos um
 * `POST /robot/reset-score` alguns segundos antes do start — o servidor só
 * obedeceu. Antes de zerar, o painel agora diz exatamente o que vai sumir.
 */

/** Placar da sessão que o reset apaga. */
export interface ResetScoreSummary {
  wins: number;
  losses: number;
  profit: number;
}

function safeCount(value: unknown): number {
  const parsed = Math.trunc(Number(value) || 0);
  return parsed > 0 ? parsed : 0;
}

/**
 * Frase com o placar que será zerado ("4 WIN × 1 LOSS"), ou null se já está 0-0.
 *
 * @param score - Placar exibido no overlay
 * @returns Texto curto, ou null quando não há nada para apagar
 */
export function resetScoreHighlight(score: ResetScoreSummary | null | undefined): string | null {
  if (!score) return null;
  const wins = safeCount(score.wins);
  const losses = safeCount(score.losses);
  if (wins === 0 && losses === 0) return null;
  const winLabel = wins === 1 ? "WIN" : "WINs";
  const lossLabel = losses === 1 ? "LOSS" : "LOSSes";
  return `${wins} ${winLabel} × ${losses} ${lossLabel}`;
}

/**
 * Rótulo do botão que confirma, já com o peso da ação.
 *
 * @param score - Placar exibido no overlay
 */
export function resetScoreConfirmLabel(score: ResetScoreSummary | null | undefined): string {
  return resetScoreHighlight(score) ? "Sim, zerar o placar" : "Reiniciar placar";
}

/**
 * True quando vale a pena perguntar antes.
 *
 * Com o placar já em 0-0 o clique não apaga nada — perguntar só atrapalharia
 * quem usa o botão para destravar o start depois de um stop batido.
 *
 * @param score - Placar exibido no overlay
 */
export function shouldConfirmResetScore(score: ResetScoreSummary | null | undefined): boolean {
  return resetScoreHighlight(score) !== null;
}
