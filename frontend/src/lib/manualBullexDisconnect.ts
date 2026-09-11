/**
 * Marca que o cliente clicou em "Desconectar Bullex".
 *
 * Enquanto a marca existe, auto-reconnect e `preferStableBullExAccount` não
 * podem desfazer o clique.
 *
 * A marca vale até o cliente conectar de novo — não expira sozinha. Antes ela
 * durava 60s, e o efeito era o bug relatado em 09/08: o painel mostrava
 * "Desconectado" por um minuto e depois voltava para "Conectado" sozinho,
 * porque `preferStableBullExAccount` voltava a mascarar o `connected:false`
 * do servidor com o último snapshot bom. Só recarregar a página resolvia (o
 * cache da query nasce vazio, então não havia snapshot para mascarar).
 *
 * Fica em `sessionStorage` para sobreviver ao reload — é a mesma semântica do
 * `bullex_manual_disconnect` no gateway, que também só é limpo no connect.
 */

const STORAGE_KEY = "bullex:manual-disconnect";

/** Espelho em memória: cobre modo privado / SSR, onde o storage lança. */
let manualDisconnected = false;

function readStorage(): boolean {
  try {
    return globalThis.sessionStorage?.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function writeStorage(active: boolean): void {
  try {
    if (active) globalThis.sessionStorage?.setItem(STORAGE_KEY, "1");
    else globalThis.sessionStorage?.removeItem(STORAGE_KEY);
  } catch {
    /* sem storage — o espelho em memória basta para esta aba */
  }
}

/** Marca desconexão explícita do usuário (Configurações → Desconectar). */
export function markManualBullexDisconnect(): void {
  manualDisconnected = true;
  writeStorage(true);
}

/** Limpa a marca — só o connect/reconnect explícito do cliente faz isso. */
export function clearManualBullexDisconnect(): void {
  manualDisconnected = false;
  writeStorage(false);
}

/** True enquanto o cliente não reconectou por vontade própria. */
export function isManualBullexDisconnectActive(): boolean {
  return manualDisconnected || readStorage();
}

/** Só para testes — zera a marca. */
export function resetManualBullexDisconnectForTests(): void {
  clearManualBullexDisconnect();
}
