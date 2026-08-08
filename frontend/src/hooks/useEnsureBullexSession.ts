import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { bullexApi } from "@/lib/api";
import { runAutoReconnectWithRetry, shouldAutoReconnectBullex } from "@/lib/ensureBullexSession";
import {
  completeBullExLogin,
  failBullExLogin,
  getBullExLoginPending,
  startBullExLogin,
  updateBullExLoginBackendStatus,
} from "@/lib/bullexLoginState";
import { BULLEX_ACCOUNT_QUERY_KEY } from "@/hooks/useBullExAccount";

/** Chaves espelhadas de useLiveTradingData (evita import circular). */
const BULLEX_STATUS_QUERY_KEY = ["bullex-status"] as const;
const ROBOT_STATE_QUERY_KEY = ["robot-state"] as const;

/**
 * Ao entrar no painel, se a sessão Bullex estiver morta mas o login estiver
 * salvo no servidor, reconecta automaticamente (sem pedir senha de novo).
 *
 * Importante: NÃO colocar `loginState.isPending` nas deps deste effect.
 * `startBullExLogin` muda isPending e re-rodava o effect, cancelava o
 * `completeBullExLogin` e deixava o botão Iniciar Operação do overlay preso.
 *
 * Ao voltar à aba após idle, libera uma nova tentativa de reconnect (antes
 * só o F5 zerava o `attemptedForUser` e “consertava” a tela).
 */
export function useEnsureBullexSession({
  userId,
  connected,
  accountLoading,
  statusLoading,
}: {
  userId?: string | null;
  connected: boolean;
  accountLoading: boolean;
  statusLoading: boolean;
}): void {
  const queryClient = useQueryClient();
  const attemptedForUser = useRef<string | null>(null);
  const [visibilityNonce, setVisibilityNonce] = useState(0);

  useEffect(() => {
    if (typeof document === "undefined") return;
    const onVisibility = () => {
      if (document.hidden) return;
      attemptedForUser.current = null;
      setVisibilityNonce((value) => value + 1);
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  useEffect(() => {
    if (!userId) {
      attemptedForUser.current = null;
      return;
    }
    if (connected) {
      attemptedForUser.current = null;
      if (getBullExLoginPending(userId)) {
        completeBullExLogin(userId);
      }
      return;
    }

    const alreadyAttempted = attemptedForUser.current === userId;
    let cancelled = false;

    async function run() {
      const credentials = await bullexApi.credentialsStatus();
      if (cancelled) return;
      const credentialsSaved = Boolean(credentials.ok && credentials.data?.credentials_saved);
      if (
        !shouldAutoReconnectBullex({
          connected: false,
          credentialsSaved,
          alreadyAttempted,
          pendingConnect: getBullExLoginPending(userId),
          stillLoading: accountLoading || statusLoading,
        })
      ) {
        return;
      }

      attemptedForUser.current = userId ?? null;
      const email = credentials.data?.email?.trim() || "Bullex";
      startBullExLogin(email, userId);
      // Retry com backoff: uma falha isolada (cooldown de 60s, rate limit da
      // corretora, blip logo após um deploy) não pode exigir clique manual.
      const response = await runAutoReconnectWithRetry(() => bullexApi.reconnect(), {
        isCancelled: () => cancelled,
        onRetryScheduled: () => updateBullExLoginBackendStatus("RECONNECTING", userId),
      });
      if (cancelled) return;
      if (!response.ok) {
        failBullExLogin(response.error || "Não foi possível reconectar a Bullex.", userId);
        return;
      }
      completeBullExLogin(userId);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: [...BULLEX_ACCOUNT_QUERY_KEY, userId] }),
        queryClient.invalidateQueries({ queryKey: [...BULLEX_STATUS_QUERY_KEY, userId] }),
        queryClient.invalidateQueries({ queryKey: [...ROBOT_STATE_QUERY_KEY, userId] }),
      ]);
    }

    void run().catch((error: unknown) => {
      if (cancelled) return;
      const message = error instanceof Error ? error.message : "Falha ao reconectar a Bullex.";
      failBullExLogin(message, userId);
    });

    return () => {
      cancelled = true;
    };
  }, [accountLoading, connected, queryClient, statusLoading, userId, visibilityNonce]);
}
