/**
 * Atualização do painel em aba que fica aberta por dias.
 *
 * O painel é uma SPA: quem não recarrega a página continua rodando o bundle do
 * dia em que abriu a aba, por mais que se publique. Em 02/10/2026 o painel de
 * um usuário pedia chunks que não existiam em nenhuma das 6 publicações
 * anteriores (o `publish-frontend.sh` apaga assets com mais de 7 dias): a aba
 * tinha mais de uma semana, sem nenhuma das correções desde então — inclusive o
 * Iniciar/Parar instantâneo de 30/09. O sintoma relatado era "tem que clicar
 * duas vezes" e "demora para abrir".
 *
 * Duas defesas:
 * 1. `version.json` (gerado no build) dizendo qual é a publicação atual; a aba
 *    compara com a sua e recarrega num momento em que não atrapalha.
 * 2. Chunk que não carrega (`vite:preloadError`) recarrega a página em vez de
 *    deixar o clique sem efeito.
 *
 * Só lógica pura aqui; os efeitos de navegador ficam em `useAppVersionWatcher`.
 */

/** De quanto em quanto tempo a aba pergunta qual é a publicação atual. */
export const APP_VERSION_CHECK_INTERVAL_MS = 5 * 60_000;
/** Sem mexer por este tempo, recarregar não interrompe ninguém. */
export const APP_VERSION_IDLE_BEFORE_RELOAD_MS = 60_000;
/** Enquanto o usuário está ativo, tenta de novo neste intervalo. */
export const APP_VERSION_RETRY_RELOAD_MS = 15_000;
/** Recarga por chunk quebrado: no máximo uma por minuto (evita laço). */
export const CHUNK_RELOAD_MIN_INTERVAL_MS = 60_000;

/** Extrai o id de publicação do `version.json`. */
export function parseBuildId(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") return null;
  const id = (payload as { build?: unknown }).build;
  return typeof id === "string" && id.trim() ? id.trim() : null;
}

/**
 * Diz se existe uma publicação diferente da que esta aba está rodando.
 *
 * Sem id de um dos lados (build de desenvolvimento, `version.json` ausente ou
 * ilegível) a resposta é não: na dúvida a aba não recarrega sozinha.
 */
export function isDifferentBuild(running: string | null | undefined, latest: string | null): boolean {
  if (!running || !latest) return false;
  return running !== latest;
}

/**
 * Decide se dá para recarregar agora sem atrapalhar.
 *
 * Nunca com diálogo aberto (o rascunho do "Iniciar Operação" vive nele) nem com
 * o cursor num campo. Fora isso: aba oculta recarrega na hora, aba à vista só
 * com o usuário parado.
 *
 * O robô não é afetado em nenhum caso: ele roda no servidor.
 */
export function canReloadForNewVersion(input: {
  hidden: boolean;
  idleMs: number;
  dialogOpen: boolean;
  typing: boolean;
}): boolean {
  if (input.dialogOpen || input.typing) return false;
  if (input.hidden) return true;
  return input.idleMs >= APP_VERSION_IDLE_BEFORE_RELOAD_MS;
}

/** Evita laço de recargas quando o chunk continua faltando depois de recarregar. */
export function chunkReloadAllowed(lastReloadAtMs: number | null, nowMs: number): boolean {
  if (lastReloadAtMs == null || !Number.isFinite(lastReloadAtMs)) return true;
  return nowMs - lastReloadAtMs >= CHUNK_RELOAD_MIN_INTERVAL_MS;
}

/** Reconhece o erro de import dinâmico que sobra quando o chunk foi apagado. */
export function isChunkLoadError(error: unknown): boolean {
  const message =
    error instanceof Error ? error.message : typeof error === "string" ? error : "";
  return /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|Unable to preload CSS/i.test(
    message,
  );
}
