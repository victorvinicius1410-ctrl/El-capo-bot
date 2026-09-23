import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate, useRouterState } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  CandlestickChart,
  Clock,
  History as HistoryIcon,
  LayoutDashboard,
  LogOut,
  Mail,
  Menu,
  MessageSquareQuote,
  Settings,
  Sparkles,
  UserRoundCog,
  Wallet,
  Webhook,
  X,
  type LucideIcon,
} from "lucide-react";
import { toast } from "sonner";
import { MarketingControlPanel } from "./MarketingControlPanel";
import { ResetScoreDialog } from "./ResetScoreDialog";
import { RobotOverlay } from "./RobotOverlay";
import { StartOperationDialog } from "./StartOperationDialog";
import {
  ApiError,
  apiConfig,
  endImpersonation,
  logoutSession,
  robotResetScore,
  robotStop,
  type BullExAccount,
} from "@/lib/api";
import {
  impersonationAllowedPaths,
  isAdminAccessSectionPath,
  shouldMountLiveTradingProvider,
  shouldShowRobotOverlay,
  shouldTreatSessionAsInactive,
} from "@/lib/adminPresentation";
import { isBullExConnected, canStartRobotOperation } from "@/lib/bullexConnection";
import { scheduleDialogOpen } from "@/lib/scheduleDialogOpen";
import { completeBullExLogin, useBullExLoginState } from "@/lib/bullexLoginState";
import { getBrokerChartLayout } from "@/lib/brokerChartLayout";
import { shouldToggleMarketingPanel } from "@/lib/marketingHotkey";
import {
  MarketingPanelProvider,
  useMarketingPanel,
} from "@/lib/marketingPanelContext";
import {
  loadMarketingDemoSettings,
  saveMarketingDemoSettings,
  type MarketingDemoSettings,
} from "@/lib/marketingDemoSettings";
import { isMarketingSimulationAccount } from "@/lib/marketingSimulation";
import { meAccessQueryOptions } from "@/lib/meAccessQuery";
import { formatBrasiliaDateTime } from "@/lib/brasiliaTime";
import { prefetchAdminClientsSegment } from "@/lib/adminClientsQuery";
import { resetBullExAccountState } from "@/hooks/useBullExAccount";
import { resetRobotSettingsForUser } from "@/lib/robotSettings";
import { shouldConfirmResetScore } from "@/lib/resetScoreConfirm";
import { isRobotOperationRunning, type RobotState } from "@/lib/robotState";
import { TRIAL_DISCOUNT, formatTrialRemaining, remainingMs } from "@/lib/trial";
import { useAuth, type AuthUser } from "@/lib/useAuth";
import {
  applyRobotMutationToCache,
  LiveTradingDataProvider,
  useLiveTradingData,
} from "@/hooks/useLiveTradingData";
import { useRobotNarrator } from "@/hooks/useRobotNarrator";
import { useRobotSettings } from "@/hooks/useRobotSettings";

const ADMIN_MODEL_EMAILS = (import.meta.env.VITE_ADMIN_EMAILS ?? "")
  .split(",")
  .map((email: string) => email.trim().toLowerCase())
  .filter(Boolean);

function isAdminModelUser(user: AuthUser | null): boolean {
  if (!user) return false;
  const email = typeof user.email === "string" ? user.email.toLowerCase() : "";
  return email.length > 0 && ADMIN_MODEL_EMAILS.includes(email);
}

interface NavItem {
  to: string;
  label: string;
  Icon: LucideIcon;
  search?: Record<string, string>;
  permission?: string | null;
}

const USER_NAV_ITEMS: NavItem[] = [
  { to: "/dashboard", label: "Dashboard", Icon: LayoutDashboard },
  { to: "/chart", label: "Corretora", Icon: CandlestickChart },
  { to: "/configuracoes", label: "Configurações", Icon: Settings, search: { secao: "conta" } },
  { to: "/history", label: "Histórico", Icon: HistoryIcon },
  { to: "/feedbacks", label: "Feedbacks", Icon: MessageSquareQuote },
  { to: "/payments", label: "Financeiro", Icon: Wallet },
];

const ADMIN_NAV_ITEMS: NavItem[] = [
  { to: "/admin/dashboard", label: "Dashboard", Icon: LayoutDashboard, permission: null },
  { to: "/admin/clientes", label: "Acessos", Icon: UserRoundCog, permission: null },
  { to: "/admin/financeiro", label: "Financeiro", Icon: Wallet, permission: "finance.view" },
  { to: "/admin/emails", label: "E-mails", Icon: Mail, permission: null },
  { to: "/admin/webhooks-api", label: "Webhooks e API", Icon: Webhook, permission: "webhooks.view" },
  { to: "/admin/feedbacks", label: "Feedbacks", Icon: MessageSquareQuote, permission: null },
];

function userInitials(email?: string | null): string {
  if (!email) return "EC";
  const localPart = email.split("@")[0] || "EC";
  const pieces = localPart.split(/[._-]+/).filter(Boolean);
  if (pieces.length >= 2) return `${pieces[0][0] ?? ""}${pieces[1][0] ?? ""}`.toUpperCase();
  return localPart.slice(0, 2).toUpperCase();
}

function isNavItemActive(pathname: string, to: string): boolean {
  if (to === "/admin/clientes") return isAdminAccessSectionPath(pathname);
  return pathname === to || pathname.startsWith(to + "/");
}

function TrialBanner({ expiresAt }: { expiresAt: string }) {
  const [remaining, setRemaining] = useState(() => remainingMs(expiresAt));
  useEffect(() => {
    setRemaining(remainingMs(expiresAt));
    const interval = setInterval(() => setRemaining(remainingMs(expiresAt)), 1_000);
    return () => clearInterval(interval);
  }, [expiresAt]);
  if (remaining <= 0) return null;
  return (
    <div className="shell-trial mb-6 flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:gap-4">
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <div className="shell-trial-icon shrink-0">
          <Sparkles className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <div className="text-sm font-semibold">Periodo gratis ativo</div>
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Clock className="h-3.5 w-3.5" />
            Tempo restante:{" "}
            <span className="font-mono font-semibold text-foreground">
              {formatTrialRemaining(remaining)}
            </span>
          </div>
        </div>
      </div>
      <Link to="/payments" className="shell-trial-cta">
        Tornar-se membro
        <span className="rounded-md bg-background/15 px-2 py-0.5 text-xs font-bold">-{TRIAL_DISCOUNT}%</span>
      </Link>
    </div>
  );
}

const ADMIN_MODEL_CYCLE_MS = 45_000;

function FloatingRobot({ userId }: { userId?: string | null }) {
  const [visible, setVisible] = useState(true);
  const [startDialogOpen, setStartDialogOpen] = useState(false);
  const [resetDialogOpen, setResetDialogOpen] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [modelWins, setModelWins] = useState(0);
  const [modelCycleStartedAt, setModelCycleStartedAt] = useState(() => Date.now());
  const [modelResult, setModelResult] = useState<{ at: number; wins: number } | null>(null);
  const liveTrading = useLiveTradingData();
  const account = liveTrading.account;
  const accountStatus = liveTrading.accountStatus;
  const robotState = liveTrading.robotState;
  const queryClient = useQueryClient();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const { user } = useAuth();
  const loginState = useBullExLoginState(userId);
  const liveState = robotState.data;
  const { settings, setSettings, saveSettings } = useRobotSettings(userId);
  const adminModelMode = isAdminModelUser(user) && pathname === "/admin";
  const layerClassName = pathname === "/chart" ? getBrokerChartLayout(false).robotZIndexClass : "z-50";
  const secondsUntilNextCycle =
    liveState?.display_countdown_seconds ?? liveState?.seconds_until_next_cycle ?? 0;
  const modelState = useMemo(
    () => buildAdminModelState(modelWins, modelCycleStartedAt, modelResult),
    [modelCycleStartedAt, modelResult, modelWins],
  );
  const displayState = adminModelMode ? modelState : liveState;
  const modelAccount = adminModelMode ? buildAdminModelAccount(modelWins) : account.data;
  const displayAccount = adminModelMode ? modelAccount : account.data;
  const narratorCountdown = adminModelMode ? 42 : secondsUntilNextCycle;
  const narrator = useRobotNarrator(
    displayState,
    settings.narratorEnabled && !adminModelMode,
    narratorCountdown,
    displayAccount?.currency ?? account.data?.currency ?? "BRL",
  );
  const cachedGrace = liveState?.connection_status_source === "cached_grace";
  const connected = isBullExConnected({
    account: account.data,
    accountStatus: accountStatus.data,
    cachedGrace,
    pendingConnect: loginState.isPending,
  });
  const operationRunning = isRobotOperationRunning(displayState);
  // Mesma regra prática do painel Configurações: conta conectada + saldo ok.
  // Não usar loginState.isPending como bloqueio — o auto-reconnect pode deixar
  // isPending preso quando o effect é cancelado após a sessão já ter voltado.
  const canStart = canStartRobotOperation({
    apiConfigured: Boolean(apiConfig.BASE_URL),
    connected,
    operationRunning,
    balance: account.data?.balance,
  });

  useEffect(() => {
    if (connected && loginState.isPending && userId) {
      completeBullExLogin(userId);
    }
  }, [connected, loginState.isPending, userId]);

  useEffect(() => {
    if (!modelResult) return;
    const timeout = window.setTimeout(() => setModelResult(null), 2_200);
    return () => window.clearTimeout(timeout);
  }, [modelResult]);
  useEffect(() => {
    if (!adminModelMode || modelResult) return;
    const elapsed = Date.now() - modelCycleStartedAt;
    const waitMs = Math.max(0, ADMIN_MODEL_CYCLE_MS - elapsed);
    const timeout = window.setTimeout(() => {
      setModelWins((wins) => {
        const next = wins + 1;
        setModelResult({ at: Date.now(), wins: next });
        return next;
      });
    }, waitMs);
    return () => window.clearTimeout(timeout);
  }, [adminModelMode, modelCycleStartedAt, modelResult]);
  useEffect(() => {
    if (adminModelMode && !modelResult) setModelCycleStartedAt(Date.now());
  }, [adminModelMode, modelResult]);

  if (!visible) {
    return (
      <button
        type="button"
        onClick={() => setVisible(true)}
        className={`fixed bottom-20 right-4 ${layerClassName} flex items-center gap-2 rounded-full border border-border bg-card px-3 py-2 text-xs font-semibold text-foreground transition hover:bg-accent`}
      >
        <Bot className="h-4 w-4 text-primary" />
        Mostrar robo
      </button>
    );
  }

  async function stopOperation(): Promise<void> {
    if (!apiConfig.BASE_URL || stopping || !operationRunning) return;
    setStopping(true);
    try {
      const response = await robotStop();
      if (!response.ok) throw new ApiError(response.error, response.code);
      if (userId && response.data) {
        applyRobotMutationToCache(queryClient, userId, response.data);
      }
      // Refetch em background — não bloquear o botão no RTT de /robot/state.
      void robotState.refetch();
      toast.success("Operações automáticas paradas");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Não foi possível parar a operação.";
      toast.error(message);
    } finally {
      setStopping(false);
    }
  }

  /**
   * Zera o placar de fato. Só chamado depois da confirmação (ou com 0-0).
   *
   * O botão vive colado no Iniciar/Parar Operação e não tem desfazer: em
   * 23/09/2026 os relatos de "parei, iniciei e o placar zerou" eram todos
   * este clique, alguns segundos antes do start.
   */
  async function confirmResetScore(): Promise<void> {
    if (!apiConfig.BASE_URL || resetting || adminModelMode) return;
    setResetting(true);
    try {
      const response = await robotResetScore();
      if (!response.ok) throw new ApiError(response.error, response.code);
      // Aplica o payload da mutação na hora (como start/stop). Em mode=external
      // um await refetch lia o snapshot Redis ainda com wins/losses antigos.
      if (userId && response.data) {
        applyRobotMutationToCache(queryClient, userId, response.data, {
          allowBlankOverwrite: true,
        });
      }
      void robotState.refetch();
      toast.success("Placar reiniciado");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Não foi possível reiniciar o placar.";
      toast.error(message);
    } finally {
      setResetting(false);
      setResetDialogOpen(false);
    }
  }

  /** Clique no "Reiniciar placar": pergunta antes quando há placar do dia. */
  function requestResetScore(): void {
    if (!apiConfig.BASE_URL || resetting || adminModelMode) return;
    if (!shouldConfirmResetScore(displayState)) {
      void confirmResetScore();
      return;
    }
    // Mesmo adiamento de um tick do Iniciar Operação: no mobile o próprio
    // toque que abre o diálogo chegava a fechá-lo (dismiss do overlay Radix).
    scheduleDialogOpen(() => setResetDialogOpen(true));
  }

  return (
    <>
      <RobotOverlay
        robotState={displayState}
        account={displayAccount}
        narratorEnabled={settings.narratorEnabled}
        narratorSpeaking={narrator.speaking}
        narratorMuted={narrator.muted}
        onSilenceNarrator={narrator.toggleSilence}
        settings={settings}
        onSettingsChange={(next) =>
          void saveSettings(next, {
            enabled: displayState?.enabled ?? false,
            cycleMinutes: displayState?.cycle_minutes ?? 5,
            accountMode: "REAL",
            allowReal: true,
            confirmReal: true,
          })
        }
        onClose={() => setVisible(false)}
        layerClassName={layerClassName}
        operationRunning={operationRunning}
        interactionLocked={startDialogOpen || resetDialogOpen}
        onStartOperation={
          adminModelMode
            ? undefined
            : () => {
                // Gesto do usuário: desbloqueia TTS no Safari/macOS/iOS.
                // Optional: regressão sem unlockAudio quebrava o clique inteiro
                // (TypeError) e o pop-up de Iniciar Operação nunca abria.
                narrator.unlockAudio?.();
                // Sempre abre o diálogo. Não bloquear por `connected` do poll:
                // backoff/cache do gateway às vezes marca desconectado com a
                // Bullex viva; o POST /robot/start limpa backoff e confirma.
                // Adia 1 tick: no mobile o mesmo toque fechava o Dialog
                // (overlay Radix via dismiss) e o botão “não fazia nada”.
                scheduleDialogOpen(() => setStartDialogOpen(true));
              }
        }
        onStopOperation={
          adminModelMode
            ? undefined
            : () => {
                void stopOperation();
              }
        }
        onResetScore={
          adminModelMode
            ? undefined
            : () => {
                requestResetScore();
              }
        }
        startOperationDisabled={!canStart}
        stopOperationDisabled={stopping || !operationRunning}
        resetScoreDisabled={resetting || !apiConfig.BASE_URL}
        startOperationLabel="Iniciar Operação"
        stopOperationLabel={stopping ? "Parando..." : "Parar Operação"}
        resetScoreLabel={resetting ? "Reiniciando..." : "Reiniciar placar"}
      />
      {adminModelMode ? null : (
        <StartOperationDialog
          open={startDialogOpen}
          onOpenChange={setStartDialogOpen}
          userId={userId}
          settings={settings}
          setSettings={setSettings}
          connected={connected}
          robotRunning={operationRunning}
          accountBalance={account.data?.balance ?? null}
          accountCurrency={account.data?.currency ?? null}
          onStarted={(startedPayload) => {
            if (userId && startedPayload) {
              applyRobotMutationToCache(queryClient, userId, startedPayload);
            }
            void robotState.refetch();
          }}
        />
      )}
      {adminModelMode ? null : (
        <ResetScoreDialog
          open={resetDialogOpen}
          onOpenChange={setResetDialogOpen}
          score={displayState}
          currency={account.data?.currency ?? null}
          resetting={resetting}
          onConfirm={() => {
            void confirmResetScore();
          }}
        />
      )}
    </>
  );
}

function buildAdminModelState(
  wins: number,
  cycleStartedAt: number,
  result: { at: number; wins: number } | null,
): RobotState {
  const now = Date.now();
  const nextCycleAt = cycleStartedAt + ADMIN_MODEL_CYCLE_MS;
  const secondsUntilNextCycle = Math.max(1, Math.ceil((nextCycleAt - now) / 1_000));
  const showingResult = result != null && now - result.at < 2_200;
  return {
    enabled: true,
    paused_by_maintenance: false,
    worker_running: true,
    connected: true,
    status: showingResult ? "RESULT_RECEIVED" : "WAITING_NEXT_CYCLE",
    status_message: showingResult ? "WIN" : "Buscando melhor oportunidade",
    cycle_id: `admin-model-${wins}`,
    allow_real: true,
    confirm_real: true,
    account_mode: "REAL",
    active_mode: "REAL",
    connection_status_source: null,
    real_ready: true,
    real_block_reason: null,
    stop_reason: null,
    next_cycle_at: new Date(nextCycleAt).toISOString(),
    server_time: new Date(now).toISOString(),
    cycle_minutes: 5,
    entry_value: 10,
    stop_win: null,
    stop_loss: null,
    stop_win_mode: null,
    stop_loss_mode: null,
    stop_win_operations: null,
    stop_loss_operations: null,
    timeframe: "M1",
    market_mode: "OTC",
    ai_analysis_enabled: false,
    ai_confirmation_required: false,
    ai_min_confidence: null,
    seconds_until_entry: 0,
    martingale_enabled: false,
    martingale_multiplier: 2,
    martingale_steps: 1,
    cycle_result: showingResult ? "WIN" : null,
    gale_step: null,
    gale_pending: false,
    gale_in_progress: false,
    gale_active: null,
    gale_direction: null,
    gale_amount: null,
    seconds_until_analysis_window: secondsUntilNextCycle,
    seconds_until_next_cycle: secondsUntilNextCycle,
    seconds_until_entry_window: 0,
    display_countdown_label: showingResult ? null : "Buscando melhor oportunidade",
    display_countdown_seconds: showingResult ? null : 0,
    expiration_seconds: 0,
    expires_at: null,
    entry_window_open: false,
    entry_target: null,
    operation_in_progress: false,
    result_waiting: false,
    operation_message: null,
    result_display_until: showingResult ? new Date(now + 2_000).toISOString() : null,
    // Estado de demonstracao do modelo no admin: `stop_reset_at` e
    // obrigatorio em RobotState e nunca foi preenchido aqui. Nao ha placar
    // real para preservar nesta tela, entao null e o valor correto.
    stop_reset_at: null,
    unseen_result: false,
    result_voice: null,
    pending_signal: null,
    best_candidate: null,
    last_signal: null,
    last_trade: showingResult
      ? {
          active: "EURUSD-OTC",
          direction: "CALL",
          amount: 10,
          order_id: `admin-win-${wins}`,
          confidence: 90,
          payout: 80,
          strategy_score: 90,
          strategy_name: "Admin model",
          strategy_key: null,
          strategy_summary: null,
          analysis_detail: null,
          speech_preview: null,
          used_strategies: [],
          strategy_reason: null,
          entry_reason: null,
          result: "WIN",
          expires_at: null,
          sent_at: new Date(now - 60_000).toISOString(),
          finished_at: new Date(now).toISOString(),
          profit: 8,
          gale_step: null,
          is_gale: false,
          account_mode: "REAL",
        }
      : null,
    wins,
    losses: 0,
    profit: wins * 8,
    last_order_error: null,
    order_fallback_in_progress: false,
    order_fallback_attempt: 0,
    order_fallback_max_attempts: 3,
    rejection_reason: null,
    last_rejection_reason: null,
    rejected_at: null,
    disconnected: false,
    fetched_at: now,
  };
}

function buildAdminModelAccount(wins: number): BullExAccount {
  return {
    connected: true,
    email: "admin@elcapo.local",
    mode: "REAL",
    balance: 5_550 + wins * 350,
    currency: "USD",
    status: "connected",
    requires_2fa: false,
  };
}

/**
 * Layout autenticado do painel: sidebar de navegação, banners de trial,
 * simulação de marketing e impersonação, e o robô flutuante.
 */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <MarketingPanelProvider>
      <AppShellContent>{children}</AppShellContent>
    </MarketingPanelProvider>
  );
}

function AppShellContent({ children }: { children: ReactNode }) {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const { open: marketingPanelOpen, setOpen: setMarketingPanelOpen } = useMarketingPanel();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const access = useQuery({
    ...meAccessQueryOptions(),
    enabled: Boolean(user?.id),
  });
  const isAdmin = access.data?.is_admin === true;
  const pendingApproval = access.data?.access_status === "pending_approval" && !isAdmin;
  const accessReady = access.isFetched || access.isError;
  const hasOperationalAccess = Boolean(isAdmin || access.data?.grant_access === true);
  // Enquanto /me/access carrega, NÃO mostrar o overlay do robô (evita lead
  // pendente clicando Iniciar). O provider de dados, porém, precisa montar
  // nesse intervalo — dashboard/configurações usam useLiveTradingData().
  // Sessão de suporte também monta o provider: o dashboard do lead chama o
  // hook, mesmo com overlay e controles bloqueados.
  const marketingSimulation = isMarketingSimulationAccount(access.data);
  const [marketingSettings, setMarketingSettings] = useState<MarketingDemoSettings>(() =>
    loadMarketingDemoSettings(user?.id),
  );
  const impersonating = access.data?.impersonating === true;
  const isAdminRoute = pathname.startsWith("/admin");
  const inactive = shouldTreatSessionAsInactive({
    impersonating,
    accessReady,
    hasOperationalAccess,
  });
  const mountLiveTrading = shouldMountLiveTradingProvider({
    impersonating,
    isAdminRoute,
    accessReady,
    hasOperationalAccess,
  });
  const showRobot = shouldShowRobotOverlay({
    impersonating,
    isAdminRoute,
    hasOperationalAccess,
  });
  const initials = userInitials(user?.email);
  const endImpersonationMutation = useMutation({
    mutationFn: async () => {
      const response = await endImpersonation();
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
    },
    onSuccess: async () => {
      sessionStorage.removeItem("elcapo_impersonation");
      await queryClient.invalidateQueries({ queryKey: ["me", "access"] });
      window.location.assign("/admin/clientes");
    },
  });

  useEffect(() => {
    if (!inactive || pathname === "/payments" || pathname === "/feedbacks") return;
    navigate({ to: "/payments", replace: true });
  }, [inactive, navigate, pathname]);
  useEffect(() => {
    if (!impersonating || impersonationAllowedPaths(true, false, true).includes(pathname)) return;
    navigate({ to: "/dashboard", replace: true });
  }, [impersonating, navigate, pathname]);
  useEffect(() => {
    setMarketingSettings(loadMarketingDemoSettings(user?.id));
  }, [user?.id]);
  useEffect(() => {
    setMobileNavOpen(false);
  }, [pathname]);
  useEffect(() => {
    if (!mobileNavOpen) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setMobileNavOpen(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previous;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [mobileNavOpen]);
  useEffect(() => {
    if (!marketingSimulation) {
      setMarketingPanelOpen(false);
      return;
    }
    function onKeyDown(event: KeyboardEvent) {
      if (!shouldToggleMarketingPanel(event, true)) return;
      event.preventDefault();
      setMarketingPanelOpen((open) => !open);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [marketingSimulation]);

  async function handleLogout(): Promise<void> {
    if (access.data?.impersonating) {
      try {
        await endImpersonation();
      } catch (error) {
        console.error("[LOGOUT_END_IMPERSONATION]", error);
      }
    }
    await logoutSession();
    resetRobotSettingsForUser(user?.id);
    resetBullExAccountState(user?.id);
    queryClient.clear();
    navigate({ to: "/login", replace: true });
  }

  const navItems = impersonating
    ? USER_NAV_ITEMS.filter((item) => impersonationAllowedPaths(true, false, true).includes(item.to))
    : isAdmin
      ? ADMIN_NAV_ITEMS.filter(
          (item) => item.permission == null || access.data?.permissions.includes(item.permission),
        )
      : inactive
        ? USER_NAV_ITEMS.filter((item) => item.to === "/feedbacks" || item.to === "/payments")
        : USER_NAV_ITEMS;

  const shell = (
    <div className="shell-layout flex min-h-screen w-full flex-col text-foreground md:flex-row">
      <header className="shell-mobile-bar mobile-safe-top">
        <div className="shell-brand shell-brand-compact">
          <div className="shell-logo">
            <Bot className="h-5 w-5 text-primary-foreground" />
          </div>
          <div className="shell-brand-copy min-w-0">
            <div className="shell-brand-name">ElCapo</div>
            <div className="shell-brand-tag">AutoBot · Painel</div>
          </div>
        </div>
        <button
          type="button"
          className="shell-hamburger"
          aria-label={mobileNavOpen ? "Fechar menu" : "Abrir menu"}
          aria-expanded={mobileNavOpen}
          aria-controls="shell-main-nav"
          onClick={() => setMobileNavOpen((open) => !open)}
        >
          {mobileNavOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
      </header>
      {mobileNavOpen ? (
        <button
          type="button"
          className="shell-mobile-backdrop"
          aria-label="Fechar menu"
          onClick={() => setMobileNavOpen(false)}
        />
      ) : null}
      <aside
        id="shell-main-nav"
        className={`shell-aside mobile-safe-top ${mobileNavOpen ? "shell-aside-open" : ""}`}
      >
        <div className="shell-brand shell-brand-drawer">
          <div className="shell-logo">
            <Bot className="h-5 w-5 text-primary-foreground" />
          </div>
          <div className="shell-brand-copy min-w-0">
            <div className="shell-brand-name">ElCapo</div>
            <div className="shell-brand-tag">AutoBot · Painel</div>
          </div>
          <button
            type="button"
            className="shell-drawer-close"
            aria-label="Fechar menu"
            onClick={() => setMobileNavOpen(false)}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <p className="shell-nav-label">Navegação</p>
        <nav className="shell-nav scrollbar-none" aria-label="Menu principal">
          {navItems.map((item) => {
            const { to, label, Icon } = item;
            const active = isNavItemActive(pathname, to);
            return (
              <Link
                key={to}
                to={to}
                search={item.search}
                preload="intent"
                title={label}
                className={`shell-nav-item ${active ? "shell-nav-item-active" : ""}`}
                aria-current={active ? "page" : undefined}
                onClick={() => setMobileNavOpen(false)}
                onMouseEnter={() => {
                  if (to === "/admin/clientes") {
                    void prefetchAdminClientsSegment(queryClient, "pending");
                  }
                }}
                onFocus={() => {
                  if (to === "/admin/clientes") {
                    void prefetchAdminClientsSegment(queryClient, "pending");
                  }
                }}
              >
                <span className="shell-nav-icon">
                  <Icon className="h-4 w-4" />
                </span>
                <span className="shell-nav-text">{label}</span>
                {active ? <span className="shell-nav-dot" aria-hidden="true" /> : null}
              </Link>
            );
          })}
        </nav>
        <div className="shell-footer">
          <div className="shell-user">
            <div className="shell-avatar" aria-hidden="true">
              {initials}
            </div>
            <div className="shell-user-meta min-w-0 flex-1">
              <div className="truncate text-xs font-semibold text-foreground">
                {user?.email?.split("@")[0] || "Operador"}
              </div>
              <div className="truncate text-[11px] text-muted-foreground">{user?.email}</div>
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              setMobileNavOpen(false);
              void handleLogout();
            }}
            className="shell-logout"
            title="Sair"
          >
            <LogOut className="h-4 w-4" />
            <span className="shell-logout-text">Sair</span>
          </button>
        </div>
      </aside>
      <div className="shell-content shell-content-fx relative min-w-0 flex-1">
        <div className="shell-content-glow" aria-hidden="true" />
        {/* Em /chart a atmosfera sai: blur(64px) + mix-blend-mode animados por
            baixo do traderoom estouram a GPU do WebKit (mesmo motivo do
            config-atmosphere-lite — ver docs/CONFIGURACOES.md). */}
        {pathname !== "/chart" ? (
          <div className="shell-fx-backdrop" aria-hidden="true">
            <span className="shell-fx-aurora shell-fx-aurora-a" />
            <span className="shell-fx-aurora shell-fx-aurora-b" />
            <span className="shell-fx-mesh" />
            <span className="shell-fx-scan" />
            <span className="shell-fx-noise" />
            <span className="shell-fx-sparks">
              <i />
              <i />
              <i />
              <i />
              <i />
              <i />
            </span>
          </div>
        ) : null}
        <main
          className={`relative z-10 ${
            pathname === "/chart"
              ? "mx-auto max-w-[1680px] px-3 py-3 sm:px-4 md:px-5 md:py-4"
              : "mx-auto max-w-7xl px-4 py-5 sm:px-5 md:px-8 md:py-8"
          }`}
        >
          {access.data?.impersonating ? (
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
              <span>
                Acesso temporário de suporte à conta <strong>{access.data.user_id}</strong>
                {access.data.impersonation_expires_at
                  ? ` até ${formatBrasiliaDateTime(access.data.impersonation_expires_at)}`
                  : ""}
                . Operações, Corretora e controles do ElCapo estão bloqueados.
              </span>
              <button
                type="button"
                disabled={endImpersonationMutation.isPending}
                onClick={() => endImpersonationMutation.mutate()}
                className="rounded-lg border border-amber-500/40 px-3 py-1.5 font-semibold"
              >
                {endImpersonationMutation.isPending ? "Encerrando..." : "Encerrar"}
              </button>
            </div>
          ) : null}
          {pathname !== "/welcome-trial" && access.data?.account_type === "trial" && access.data.expires_at ? (
            <TrialBanner expiresAt={access.data.expires_at} />
          ) : null}
          {children}
        </main>
      </div>
      {showRobot ? <FloatingRobot userId={user?.id} /> : null}
      {marketingSimulation ? (
        <MarketingControlPanel
          open={marketingPanelOpen}
          onClose={() => setMarketingPanelOpen(false)}
          settings={marketingSettings}
          onSettingsChange={(partial) => {
            if (!user?.id) return;
            setMarketingSettings(saveMarketingDemoSettings(user.id, partial));
          }}
          targetWinRate={access.data?.marketing_win_rate}
          userId={user?.id}
        />
      ) : null}
      {inactive && pathname !== "/payments" && pathname !== "/feedbacks" ? (
        <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/70 p-4">
          <section className="w-full max-w-md rounded-2xl border border-border bg-card p-6 text-center shadow-2xl">
            <Wallet className="mx-auto h-8 w-8 text-primary" />
            <h2 className="mt-3 text-xl font-semibold">
              {pendingApproval ? "Aguardando aprovação" : "Assinatura inativa"}
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">
              {pendingApproval
                ? "Seu cadastro foi recebido. Aguarde um administrador liberar o acesso ou compre um plano para liberar na hora."
                : "Regularize sua assinatura para liberar novamente as funções operacionais do ElCapo."}
            </p>
            <Link to="/payments" className="page-cta mt-5 inline-flex rounded-lg px-4 py-2.5 text-sm font-semibold">
              {pendingApproval ? "Ver planos / liberar agora" : "Ir para o Financeiro"}
            </Link>
          </section>
        </div>
      ) : null}
    </div>
  );

  return mountLiveTrading ? (
    <LiveTradingDataProvider userId={user?.id}>
      {shell}
    </LiveTradingDataProvider>
  ) : (
    shell
  );
}
