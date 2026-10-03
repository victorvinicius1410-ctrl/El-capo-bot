import { useEffect } from "react";
import {
  APP_VERSION_CHECK_INTERVAL_MS,
  APP_VERSION_RETRY_RELOAD_MS,
  canReloadForNewVersion,
  chunkReloadAllowed,
  isDifferentBuild,
  parseBuildId,
} from "@/lib/appVersion";

/** Id desta publicação, gravado pelo `vite.config.ts` no build (vazio em dev). */
declare const __ELCAPO_BUILD_ID__: string | undefined;

const CHUNK_RELOAD_KEY = "elcapo:chunk-reload-at";

function runningBuildId(): string {
  return typeof __ELCAPO_BUILD_ID__ === "string" ? __ELCAPO_BUILD_ID__ : "";
}

/**
 * Recarrega a página porque um chunk desta versão não existe mais no servidor.
 *
 * @returns true se a recarga foi disparada; false se já recarregou há pouco
 *   (o chunk continua faltando — recarregar de novo seria laço).
 */
export function reloadForMissingChunk(): boolean {
  if (typeof window === "undefined") return false;
  let last: number | null = null;
  try {
    const raw = window.sessionStorage.getItem(CHUNK_RELOAD_KEY);
    last = raw ? Number(raw) : null;
  } catch {
    last = null;
  }
  if (!chunkReloadAllowed(last, Date.now())) return false;
  try {
    window.sessionStorage.setItem(CHUNK_RELOAD_KEY, String(Date.now()));
  } catch {
    // Sem sessionStorage não há como evitar o laço: melhor não recarregar.
    return false;
  }
  window.location.reload();
  return true;
}

/**
 * Mantém a aba na publicação atual do painel (ver `lib/appVersion`).
 *
 * Pergunta `/version.json` ao voltar para a aba, ao voltar a rede e a cada
 * 5 minutos; havendo publicação nova, recarrega quando não atrapalha.
 */
export function useAppVersionWatcher(): void {
  useEffect(() => {
    const running = runningBuildId();
    if (!running) return;

    let disposed = false;
    let stale = false;
    let lastActivityAt = Date.now();
    let retryTimer: number | null = null;

    const markActivity = () => {
      lastActivityAt = Date.now();
    };

    const tryReload = () => {
      if (disposed || !stale) return;
      const focused = document.activeElement;
      const typing =
        focused instanceof HTMLInputElement ||
        focused instanceof HTMLTextAreaElement ||
        focused instanceof HTMLSelectElement ||
        (focused instanceof HTMLElement && focused.isContentEditable);
      const dialogOpen =
        document.querySelector('[role="dialog"], [role="alertdialog"]') != null;
      if (
        canReloadForNewVersion({
          hidden: document.hidden === true,
          idleMs: Date.now() - lastActivityAt,
          dialogOpen,
          typing,
        })
      ) {
        window.location.reload();
        return;
      }
      if (retryTimer == null) {
        retryTimer = window.setTimeout(() => {
          retryTimer = null;
          tryReload();
        }, APP_VERSION_RETRY_RELOAD_MS);
      }
    };

    const check = async () => {
      if (disposed) return;
      if (stale) {
        tryReload();
        return;
      }
      try {
        const response = await fetch(`/version.json?t=${Date.now()}`, { cache: "no-store" });
        if (!response.ok) return;
        const latest = parseBuildId(await response.json());
        if (isDifferentBuild(running, latest)) {
          stale = true;
          console.info("[APP_VERSION_STALE]", { running, latest });
          tryReload();
        }
      } catch {
        // Sem rede ou resposta que não é JSON: tenta de novo no próximo ciclo.
      }
    };

    const onVisibility = () => {
      if (document.hidden !== true) void check();
      else tryReload();
    };
    const onOnline = () => void check();

    const interval = window.setInterval(() => void check(), APP_VERSION_CHECK_INTERVAL_MS);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("online", onOnline);
    window.addEventListener("pointerdown", markActivity, { passive: true });
    window.addEventListener("keydown", markActivity, { passive: true });
    void check();

    return () => {
      disposed = true;
      window.clearInterval(interval);
      if (retryTimer != null) window.clearTimeout(retryTimer);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("online", onOnline);
      window.removeEventListener("pointerdown", markActivity);
      window.removeEventListener("keydown", markActivity);
    };
  }, []);
}
