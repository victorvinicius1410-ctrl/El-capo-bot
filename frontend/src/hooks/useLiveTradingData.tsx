import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import { ApiError, bullexApi, robotState as fetchRobotState, robotSyncConnection, type BullExAccount } from "@/lib/api";
import {
  getStoppedRobotState,
  isRobotOperationRunning,
  mergeRobotSessionScore,
  normalizeRobotState,
  preserveRobotSessionScore,
  registerRobotStateFailure,
  resetRobotStateBackoff,
  robotStateRefetchInterval,
  setRobotStateWsLive,
  type RobotState,
} from "@/lib/robotState";
import { rememberRobotSettingsFromState } from "@/lib/robotSettings";
import { connectRobotStateWs } from "@/lib/robotStateWs";
import { isManualBullexDisconnectActive } from "@/lib/manualBullexDisconnect";
import { useBullExAccountQuery } from "./useBullExAccount";
import { useEnsureBullexSession } from "./useEnsureBullexSession";
import {
  clearSessionScoreAuthority,
  markSessionScoreAuthority,
  resolveSessionScoreGate,
} from "@/lib/sessionScoreAuthority";

export type { RobotState, RobotTrade } from "@/lib/robotState";
export { isRobotOperationRunning } from "@/lib/robotState";

export const BULLEX_STATUS_QUERY_KEY = ["bullex-status"] as const;
export const ROBOT_STATE_QUERY_KEY = ["robot-state"] as const;

export interface BullExStatusData {
  status: string;
}

interface LiveTradingData {
  account: UseQueryResult<BullExAccount, Error>;
  accountStatus: UseQueryResult<BullExStatusData, Error>;
  robotState: UseQueryResult<RobotState, Error>;
  userId?: string | null;
}

const LiveTradingDataContext = createContext<LiveTradingData | null>(null);

function useBullExStatusQuery({
  userId,
  enabled = true,
  isDocumentVisible = true,
}: {
  userId?: string | null;
  enabled?: boolean;
  isDocumentVisible?: boolean;
}): UseQueryResult<BullExStatusData, Error> {
  return useQuery({
    queryKey: [...BULLEX_STATUS_QUERY_KEY, userId],
    queryFn: async () => {
      const response = await bullexApi.status();
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      const data = response.data as { status?: string; connected?: boolean } | undefined;
      const rawStatus = typeof data?.status === "string" ? data.status.trim().toUpperCase() : "";
      // Backoff do gateway não é desconexão — mapear para DISCONNECTED
      // apagava o pill "Conectado" ao entrar em Configurações.
      if (rawStatus === "BACKOFF") {
        return { status: "BACKOFF" };
      }
      const statusFromFlag =
        data?.connected === true ? "CONNECTED" : data?.connected === false ? "DISCONNECTED" : null;
      return {
        status:
          statusFromFlag ??
          (typeof data?.status === "string" ? data.status : "disconnected"),
      };
    },
    enabled: enabled && Boolean(userId),
    refetchInterval: isDocumentVisible ? 25_000 : false,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
    retry: false,
    staleTime: 24_000,
  });
}

export function applyRobotStateSideEffects(userId: string, normalized: RobotState): void {
  rememberRobotSettingsFromState(userId, {
    entryValue: normalized.entry_value ?? undefined,
    stopWin: normalized.stop_win ?? undefined,
    stopLoss: normalized.stop_loss ?? undefined,
    stopWinMode: normalized.stop_win_mode ?? undefined,
    stopLossMode: normalized.stop_loss_mode ?? undefined,
    stopWinOperations: normalized.stop_win_operations ?? undefined,
    stopLossOperations: normalized.stop_loss_operations ?? undefined,
    martingaleEnabled: normalized.martingale_enabled,
    martingaleSteps: normalized.martingale_steps,
    martingaleMultiplier: normalized.martingale_multiplier,
    timeframe: normalized.timeframe ?? undefined,
    marketMode: normalized.market_mode ?? undefined,
    aiAnalysisEnabled: normalized.ai_analysis_enabled,
    aiConfirmationRequired: normalized.ai_confirmation_required,
    aiMinConfidence: normalized.ai_min_confidence ?? undefined,
  });
}

export interface ApplyRobotMutationOptions {
  /** True só em POST /robot/reset-score — permite gravar placar 0-0. */
  allowBlankOverwrite?: boolean;
}

function commitRobotStateToCache(
  queryClient: QueryClient,
  userId: string,
  incoming: RobotState,
  options?: ApplyRobotMutationOptions,
): RobotState {
  const previous = queryClient.getQueryData<RobotState>([...ROBOT_STATE_QUERY_KEY, userId]);
  if (options?.allowBlankOverwrite) {
    // "Reiniciar placar" substitui qualquer baixa anterior.
    clearSessionScoreAuthority(userId);
  }
  const gate = resolveSessionScoreGate(userId, previous, incoming);
  const withScore = preserveRobotSessionScore(previous, incoming, {
    ...options,
    allowScoreDecrease: gate.allowScoreDecrease,
    forceScorePreserve: gate.rejectAsStale,
  });
  const safeState = isManualBullexDisconnectActive()
    ? {
        ...withScore,
        ...getStoppedRobotState(true),
        wins: withScore.wins,
        losses: withScore.losses,
        profit: withScore.profit,
      }
    : withScore;
  applyRobotStateSideEffects(userId, safeState);
  resetRobotStateBackoff(userId);
  queryClient.setQueryData([...ROBOT_STATE_QUERY_KEY, userId], safeState);
  return safeState;
}

/**
 * Aplica o payload de POST /robot/start|stop|reset-score no cache do React Query.
 *
 * Evita `await refetch()` (RTT extra + risco de ler snapshot Redis antigo
 * antes do publish do controle). O WS/refetch em background sincronizam depois.
 *
 * Start/stop **não** substituem um placar vivo por 0-0 (snapshot de controle
 * do gateway). Só `allowBlankOverwrite` (Reiniciar placar) zera o overlay.
 *
 * @param queryClient - Cliente React Query do painel
 * @param userId - Usuário autenticado
 * @param payload - `data` da resposta da mutação (ou envelope completo)
 * @param options - `allowBlankOverwrite` no reset de placar
 * @returns Estado canônico gravado no cache
 */
export function applyRobotMutationToCache(
  queryClient: QueryClient,
  userId: string,
  payload: unknown,
  options?: ApplyRobotMutationOptions,
): RobotState {
  return commitRobotStateToCache(queryClient, userId, normalizeRobotState(payload), options);
}

/**
 * Aplica o placar do Shift+O no overlay imediatamente.
 *
 * Generate/create não devolvem `POST /robot/*`; sem isto o Redis/WS ainda
 * 0-0 apagam o visual até o runtime receber `apply_score`.
 *
 * @param queryClient - Cliente React Query do painel
 * @param userId - Usuário autenticado
 * @param score - Wins/loss/lucro do lote gerado ou da operação avulsa
 * @param options.accumulate - True em "Nova operação"
 * @param options.subtract - True na exclusão do histórico
 * @returns Estado gravado no cache
 */
export function applyRobotSessionScoreToCache(
  queryClient: QueryClient,
  userId: string,
  score: { wins: number; losses: number; profit: number },
  options?: { accumulate?: boolean; subtract?: boolean },
): RobotState {
  const previous = queryClient.getQueryData<RobotState>([...ROBOT_STATE_QUERY_KEY, userId]);
  const merged = mergeRobotSessionScore(previous, score, options);
  // Exclusão (ou lote do Shift+O que baixa o total) é queda PEDIDA: sem
  // registrar, o `preserveRobotSessionScore` do poll/WS seguinte rejeitaria o
  // placar já corrigido pelo servidor e a operação voltava ao El Capo.
  if (previous && merged.wins + merged.losses < previous.wins + previous.losses) {
    markSessionScoreAuthority(userId, previous, merged);
  }
  applyRobotStateSideEffects(userId, merged);
  queryClient.setQueryData([...ROBOT_STATE_QUERY_KEY, userId], merged);
  return merged;
}

function useRobotStateQuery(
  userId: string | null | undefined,
  isDocumentVisible: boolean,
): UseQueryResult<RobotState, Error> {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!userId || !isDocumentVisible) {
      setRobotStateWsLive(false);
      return;
    }
    const disconnect = connectRobotStateWs({
      onOpen: () => setRobotStateWsLive(true),
      onClose: () => setRobotStateWsLive(false),
      onError: () => setRobotStateWsLive(false),
      onState: (data) => {
        commitRobotStateToCache(queryClient, userId, normalizeRobotState(data));
      },
    });
    return () => {
      disconnect();
      setRobotStateWsLive(false);
    };
  }, [isDocumentVisible, queryClient, userId]);

  return useQuery({
    queryKey: [...ROBOT_STATE_QUERY_KEY, userId],
    queryFn: async () => {
      if (!userId) throw new ApiError("Não autenticado", "NO_AUTH");
      const response = await fetchRobotState(userId);
      if (!response.ok) {
        if (response.code === "SESSION_NOT_FOUND" || response.code === "SESSION_DISCONNECTED") {
          resetRobotStateBackoff(userId);
          const previous = queryClient.getQueryData<RobotState>([...ROBOT_STATE_QUERY_KEY, userId]);
          return preserveRobotSessionScore(previous, getStoppedRobotState(true));
        }
        registerRobotStateFailure(userId);
        throw new ApiError(response.error, response.code);
      }
      resetRobotStateBackoff(userId);
      const previous = queryClient.getQueryData<RobotState>([...ROBOT_STATE_QUERY_KEY, userId]);
      const fresh = normalizeRobotState(response.data);
      const gate = resolveSessionScoreGate(userId, previous, fresh);
      const normalized = preserveRobotSessionScore(previous, fresh, {
        allowScoreDecrease: gate.allowScoreDecrease,
        forceScorePreserve: gate.rejectAsStale,
      });
      const safeState = isManualBullexDisconnectActive()
        ? {
            ...normalized,
            ...getStoppedRobotState(true),
            wins: normalized.wins,
            losses: normalized.losses,
            profit: normalized.profit,
          }
        : normalized;
      applyRobotStateSideEffects(userId, safeState);
      return safeState;
    },
    enabled: Boolean(userId),
    refetchInterval: (query) =>
      isDocumentVisible ? robotStateRefetchInterval(query.state.data, userId) : false,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
    retry: 1,
    staleTime: 0,
  });
}

function robotStateLooksDisconnected(state?: RobotState): boolean {
  if (state?.connection_status_source === "cached_grace") return false;
  return (
    state?.connected === false ||
    state?.disconnected === true ||
    state?.status === "ACCOUNT_DISCONNECTED" ||
    state?.status === "DISCONNECTED"
  );
}

function accountLooksConnected({
  account,
  accountStatus,
  connectionStatusSource,
}: {
  account?: BullExAccount;
  accountStatus?: BullExStatusData;
  connectionStatusSource?: string | null;
}): boolean {
  // Desconectar Bullex: não reconciliar o robô como "conectado" nem disparar
  // sync — o snapshot Redis/WS pode ainda dizer connected=true por alguns s.
  if (isManualBullexDisconnectActive()) return false;
  if (connectionStatusSource === "cached_grace") return true;
  const status = accountStatus?.status?.toLowerCase();
  if (status === "connected") return true;
  if (account?.connected === true) return true;
  // Backoff: não tratar como offline se ainda temos snapshot de conta.
  // O caso conectado já retornou acima, então aqui `connected` só pode ser
  // false/undefined — comparar com `true` era expressão sempre falsa.
  if (status === "backoff") return false;
  if (account?.connected === false || account?.status === "disconnected") return false;
  return false;
}

const SYNC_THROTTLE_MS = 10_000;
const lastSyncByUser = new Map<string, number>();

/**
 * Se a conta está conectada mas o estado do robô diz o contrário, dispara a
 * sincronização de conexão no backend e reconcilia o estado exibido.
 */
function useReconciledRobotState({
  userId,
  accountConnected,
  robotState,
}: {
  userId?: string | null;
  accountConnected: boolean;
  robotState?: RobotState;
}): RobotState | undefined {
  const queryClient = useQueryClient();
  useEffect(() => {
    if (!userId || !accountConnected || !robotStateLooksDisconnected(robotState)) return;
    const now = Date.now();
    const last = lastSyncByUser.get(userId) ?? 0;
    if (now - last < SYNC_THROTTLE_MS) return;
    lastSyncByUser.set(userId, now);
    robotSyncConnection()
      .catch((error: unknown) => {
        console.warn("[ROBOT SYNC CONNECTION ERROR]", error);
      })
      .finally(() => {
        void queryClient.refetchQueries({ queryKey: [...ROBOT_STATE_QUERY_KEY, userId], exact: true });
      });
  }, [accountConnected, queryClient, robotState, userId]);

  return useMemo(() => {
    if (!robotState || !accountConnected || !robotStateLooksDisconnected(robotState)) return robotState;
    const clearDisconnectMessage = (value: string | null) =>
      value === "Conta Bullex desconectada" || value === "Conta BullEx desconectada" ? null : value;
    return {
      ...robotState,
      connected: true,
      disconnected: false,
      status:
        robotState.status === "ACCOUNT_DISCONNECTED" || robotState.status === "DISCONNECTED"
          ? "STOPPED"
          : robotState.status,
      real_block_reason: clearDisconnectMessage(robotState.real_block_reason),
      rejection_reason: clearDisconnectMessage(robotState.rejection_reason),
      last_rejection_reason: clearDisconnectMessage(robotState.last_rejection_reason),
    };
  }, [accountConnected, robotState]);
}

/** Compartilha conta, status e estado do robô entre as telas autenticadas. */
export function LiveTradingDataProvider({
  userId,
  children,
}: {
  userId?: string | null;
  children: ReactNode;
}) {
  const visible = usePageVisibility();
  const account = useBullExAccountQuery({ userId, enabled: Boolean(userId), isDocumentVisible: visible });
  const accountStatus = useBullExStatusQuery({ userId, enabled: Boolean(userId), isDocumentVisible: visible });
  const robotStateQuery = useRobotStateQuery(userId, visible);
  const connected = accountLooksConnected({
    account: account.data,
    accountStatus: accountStatus.data,
    connectionStatusSource: robotStateQuery.data?.connection_status_source,
  });
  useEnsureBullexSession({
    userId,
    connected,
    robotEnabled: Boolean(robotStateQuery.data?.enabled),
    accountLoading: account.isLoading,
    statusLoading: accountStatus.isLoading,
  });
  const reconciled = useReconciledRobotState({
    userId,
    accountConnected: connected,
    robotState: robotStateQuery.data,
  });
  const robotState = useMemo(
    () => ({ ...robotStateQuery, data: reconciled }) as UseQueryResult<RobotState, Error>,
    [reconciled, robotStateQuery],
  );
  const value = useMemo(
    () => ({ account, accountStatus, robotState, userId }),
    [account, accountStatus, robotState, userId],
  );
  return <LiveTradingDataContext.Provider value={value}>{children}</LiveTradingDataContext.Provider>;
}

/** Lê os dados operacionais compartilhados pelo AppShell. */
export function useLiveTradingData(): LiveTradingData {
  const context = useContext(LiveTradingDataContext);
  if (!context) throw new Error("useLiveTradingData precisa ser usado dentro de LiveTradingDataProvider.");
  return context;
}

function usePageVisibility(): boolean {
  const [visible, setVisible] = useState(() =>
    typeof document === "undefined" ? true : document.hidden !== true,
  );
  useEffect(() => {
    const update = () => setVisible(document.hidden !== true);
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  return visible;
}
