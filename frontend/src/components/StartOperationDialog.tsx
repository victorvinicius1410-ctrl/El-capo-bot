import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Clock3,
  History,
  Loader2,
  Lock,
  Play,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ApiError, apiConfig, robotConfig, robotMasanielloEndCycle, robotStart } from "@/lib/api";
import {
  ROBOT_STATE_QUERY_KEY,
  applyOptimisticOperation,
  revertOptimisticOperation,
} from "@/hooks/useLiveTradingData";
import {
  currencySymbol,
  entryValueBalanceError,
  formatBullExBalance,
} from "@/lib/bullexConnection";
import {
  type MasanielloCycle,
  masanielloPlanForProfile,
  masanielloWillContinue,
} from "@/lib/masaniello";
import { masanielloFormView } from "@/lib/masanielloPresentation";
import { MARKET_SHORT_LABEL, operationSummaryRows } from "@/lib/startOperationSummary";
import {
  DEFAULT_ROBOT_SETTINGS,
  ENTRY_VALUE_STEP,
  ROBOT_TIMEFRAME_OPTIONS,
  STOP_MONEY_MIN,
  STOP_OPERATIONS_MAX,
  STOP_OPERATIONS_MIN,
  clampEntryValueForCurrency,
  coerceSelectableMarketMode,
  cycleMinutesForTimeframe,
  entryLimitsForCurrency,
  entryValueHelperText,
  formatForexOpenCountdown,
  isForexOpenMarketAvailable,
  markRobotSettingsSynced,
  marketModeLabel,
  masanielloConfigPayload,
  normalizeRobotSettings,
  parseEntryValueInput,
  parseStopMoneyInput,
  parseStopOperationsInput,
  timeframeLabel,
  visibleRobotMarketModeOptions,
  type RobotSettings,
  type RobotStopMode,
} from "@/lib/robotSettings";
import { MoneyInput } from "./MoneyInput";
import { Switch } from "@/components/ui/switch";
import { ConsistentManagementFields, ConsistentManagementToggle } from "./ConsistentManagement";
import { MarketModeLockPopover } from "@/components/MarketModeLockPopover";
import {
  OPEN_MARKET_MAINTENANCE_SHORT,
  resolveMarketModeLock,
} from "@/lib/openMarketMaintenance";

/** Janela em ms para ignorar dismiss acidental ao abrir (toque residual no mobile). */
const OPEN_DISMISS_GRACE_MS = 450;

const MIN_CONFIDENCE = 80;
const MIN_PAYOUT = 80;
const LAST_CONFIG_STORAGE_PREFIX = "elcapo:last-operation-config:";

type OperationConfig = Pick<
  RobotSettings,
  | "timeframe"
  | "marketMode"
  | "entryValue"
  | "stopWin"
  | "stopLoss"
  | "stopWinMode"
  | "stopLossMode"
  | "stopWinOperations"
  | "stopLossOperations"
  | "martingaleEnabled"
  | "martingaleSteps"
  | "martingaleMultiplier"
  | "masanielloEnabled"
  | "masanielloCapital"
  | "masanielloProfile"
  | "masanielloOperations"
  | "masanielloWins"
>;

function pickOperationConfig(settings: RobotSettings): OperationConfig {
  return {
    timeframe: settings.timeframe,
    marketMode: settings.marketMode,
    entryValue: settings.entryValue,
    stopWin: settings.stopWin,
    stopLoss: settings.stopLoss,
    stopWinMode: settings.stopWinMode,
    stopLossMode: settings.stopLossMode,
    stopWinOperations: settings.stopWinOperations,
    stopLossOperations: settings.stopLossOperations,
    martingaleEnabled: settings.martingaleEnabled,
    martingaleSteps: settings.martingaleSteps,
    martingaleMultiplier: settings.martingaleMultiplier,
    masanielloEnabled: settings.masanielloEnabled,
    masanielloCapital: settings.masanielloCapital,
    masanielloProfile: settings.masanielloProfile,
    masanielloOperations: settings.masanielloOperations,
    masanielloWins: settings.masanielloWins,
  };
}

/**
 * O que impede iniciar por saldo/valor. No Gerenciamento Consistente quem
 * manda é o capital do ciclo (ou só a próxima entrada, se vai continuar um
 * ciclo em andamento); no valor fixo, a entrada.
 */
function operationBlockReason(
  config: OperationConfig,
  balance: number | null | undefined,
  currency: string | null | undefined,
  continuing: boolean,
): string | null {
  if (!config.masanielloEnabled) return entryValueBalanceError(balance, config.entryValue);
  return masanielloFormView({
    capital: config.masanielloCapital,
    profile: config.masanielloProfile,
    operations: config.masanielloOperations,
    wins: config.masanielloWins,
    payoutRef: MIN_PAYOUT,
    currency,
    balance,
    continuing,
  }).error;
}

function mergeOperationConfig(
  config: OperationConfig,
  base?: RobotSettings,
  currency?: string | null,
): RobotSettings {
  return normalizeRobotSettings({ ...(base ?? DEFAULT_ROBOT_SETTINGS), ...config }, currency);
}

function loadLastOperationConfig(userId: string | null | undefined): OperationConfig | null {
  if (typeof window === "undefined" || !userId) return null;
  try {
    const raw = window.localStorage.getItem(`${LAST_CONFIG_STORAGE_PREFIX}${userId}`);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<OperationConfig>;
    return pickOperationConfig(normalizeRobotSettings({ ...DEFAULT_ROBOT_SETTINGS, ...parsed }));
  } catch {
    return null;
  }
}

function persistLastOperationConfig(userId: string | null | undefined, config: OperationConfig): void {
  if (typeof window === "undefined" || !userId) return;
  const snapshot = pickOperationConfig(mergeOperationConfig(config));
  window.localStorage.setItem(`${LAST_CONFIG_STORAGE_PREFIX}${userId}`, JSON.stringify(snapshot));
}

function hasLastOperationConfig(userId: string | null | undefined): boolean {
  return loadLastOperationConfig(userId) != null;
}

export interface StartOperationDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  userId: string | null | undefined;
  settings: RobotSettings;
  setSettings: (settings: RobotSettings) => void;
  connected: boolean;
  robotRunning: boolean;
  accountBalance?: number | null;
  accountCurrency?: string | null;
  onStarted?: (startedPayload?: unknown) => void | Promise<void>;
}

/**
 * Diálogo de confirmação usado antes de ligar o robô: escolhe timeframe,
 * mercado, valores e gale, aplicando as últimas configurações salvas.
 */
export function StartOperationDialog({
  open,
  onOpenChange,
  userId,
  settings,
  setSettings,
  connected,
  robotRunning,
  accountBalance = null,
  accountCurrency = null,
  onStarted,
}: StartOperationDialogProps) {
  const [draft, setDraft] = useState<OperationConfig>(() => pickOperationConfig(settings));
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ignoreOutsideUntilRef = useRef(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const hasSavedConfig = useMemo(() => Boolean(userId && hasLastOperationConfig(userId)), [userId, open]);
  const queryClient = useQueryClient();
  const [endingCycle, setEndingCycle] = useState(false);
  const [endedCycleId, setEndedCycleId] = useState<string | null>(null);
  // Ciclo do Gerenciamento Consistente e modo LIVE como o servidor os conhece.
  // Lidos do cache pelo mesmo motivo do mercado aberto, mais abaixo.
  const cachedRobotState = queryClient.getQueryData<{
    masaniello_cycle?: MasanielloCycle | null;
    masaniello_enabled?: boolean;
    live_demo?: boolean;
  }>([...ROBOT_STATE_QUERY_KEY, userId]);
  // O servidor só manda `masaniello_enabled` se conhece o Gerenciamento
  // Consistente. Onde ele ainda não subiu (backend antigo), a chave não
  // aparece: ligá-la ali seria uma opção que o robô ignora em silêncio.
  const consistentSupported = cachedRobotState?.masaniello_enabled !== undefined;
  const cachedCycle = cachedRobotState?.masaniello_cycle ?? null;
  const masanielloCycle = cachedCycle && cachedCycle.id !== endedCycleId ? cachedCycle : null;
  // Modo LIVE ligado (conta marketing): o robô ignora o gerenciamento, então o
  // formulário volta para valor fixo + stops e a chave fica travada.
  const liveOn = cachedRobotState?.live_demo === true;
  const masanielloOn = consistentSupported && draft.masanielloEnabled && !liveOn;
  // O que vale para validar saldo/capital nesta partida. A preferência
  // gravada (`draft.masanielloEnabled`) NÃO muda por causa do LIVE.
  const effectiveDraft: OperationConfig = masanielloOn
    ? draft
    : { ...draft, masanielloEnabled: false };
  const continuingCycle =
    masanielloOn &&
    masanielloWillContinue(masanielloCycle, {
      capital: draft.masanielloCapital,
      ...masanielloPlanForProfile(
        draft.masanielloProfile,
        draft.masanielloOperations,
        draft.masanielloWins,
      ),
      payoutRef: MIN_PAYOUT,
    });
  const balanceError = useMemo(
    () => operationBlockReason(effectiveDraft, accountBalance, accountCurrency, continuingCycle),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `effectiveDraft` deriva de draft/masanielloOn
    [accountBalance, accountCurrency, draft, masanielloOn, continuingCycle],
  );

  // Só hidrata o rascunho ao abrir o diálogo. Não reage a `settings` do poll —
  // isso resetava timeframe/mercado/valores para M1/OTC enquanto a pessoa editava.
  useEffect(() => {
    if (!open) return;
    ignoreOutsideUntilRef.current = Date.now() + OPEN_DISMISS_GRACE_MS;
    const base = pickOperationConfig(settings);
    setDraft({
      ...base,
      entryValue: clampEntryValueForCurrency(base.entryValue, accountCurrency),
      marketMode: coerceSelectableMarketMode(base.marketMode),
    });
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intencional: só ao abrir
  }, [open]);

  const marketModeOptions = useMemo(() => visibleRobotMarketModeOptions(), [open]);
  const [nowTick, setNowTick] = useState(() => Date.now());
  // O relógio do navegador só conhece a janela semanal do forex — e erra em
  // FERIADO, que foi o que enganou a medição de 07/09/2026. O servidor cruza
  // essa janela com o que a corretora responde por ativo, então sabe mais.
  // Lido do cache (e não por hook de contexto) para o diálogo não passar a
  // depender do provider de dados ao vivo. Sem resposta ainda, vale o relógio.
  const openMarketDoServidor = queryClient.getQueryData<{
    open_market_available?: boolean;
  }>([...ROBOT_STATE_QUERY_KEY, userId])?.open_market_available;
  const openMarketAvailable =
    typeof openMarketDoServidor === "boolean"
      ? openMarketDoServidor
      : isForexOpenMarketAvailable(new Date(nowTick));
  const openMarketCountdown = formatForexOpenCountdown(new Date(nowTick));

  useEffect(() => {
    if (!open || openMarketAvailable) return;
    const timer = window.setInterval(() => setNowTick(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, [open, openMarketAvailable]);

  function shouldIgnoreOutsideDismiss(): boolean {
    return Date.now() < ignoreOutsideUntilRef.current;
  }

  function patchDraft(patch: Partial<OperationConfig>): void {
    setDraft((current) => ({ ...current, ...patch }));
  }

  async function endMasanielloCycle(): Promise<void> {
    if (!masanielloCycle || endingCycle) return;
    setEndingCycle(true);
    try {
      const response = await robotMasanielloEndCycle();
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      setEndedCycleId(masanielloCycle.id);
      void queryClient.invalidateQueries({ queryKey: [...ROBOT_STATE_QUERY_KEY, userId] });
      toast.success("Ciclo encerrado. O próximo Iniciar começa um ciclo novo.");
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : "Não foi possível encerrar o ciclo.");
    } finally {
      setEndingCycle(false);
    }
  }

  function applyLastConfig(): void {
    if (!userId) return;
    const saved = loadLastOperationConfig(userId);
    if (!saved) {
      toast.info("Nenhuma configuração anterior encontrada.");
      return;
    }
    setDraft({
      ...saved,
      entryValue: clampEntryValueForCurrency(saved.entryValue, accountCurrency),
    });
    toast.success("Últimas configurações aplicadas.");
  }

  async function confirmAndStart(): Promise<void> {
    if (starting) return;
    if (!userId) {
      const message = "Sessão inválida. Recarregue a página e entre de novo.";
      setError(message);
      toast.error(message);
      return;
    }
    if (!apiConfig.BASE_URL) {
      const message = "API do painel não configurada. Recarregue a página.";
      setError(message);
      toast.error(message);
      return;
    }
    if (robotRunning) {
      toast.info("A operação já está em andamento.");
      onOpenChange(false);
      return;
    }
    // Não bloquear por `connected` do poll (backoff/cache falso). O start no
    // backend limpa a sessão e devolve BULLEX_NOT_CONNECTED se realmente
    // precisar reconectar em Configurações → Conta Corretora.
    const invalidBalance = operationBlockReason(
      effectiveDraft,
      accountBalance,
      accountCurrency,
      continuingCycle,
    );
    if (invalidBalance) {
      setError(invalidBalance);
      toast.error(invalidBalance);
      return;
    }
    setStarting(true);
    setError(null);
    // Instantâneo: o painel mostra "em operação" no clique e o diálogo fecha;
    // salvar a configuração e iniciar correm por trás. Se o servidor recusar
    // (sem saldo, stop batido), volta ao estado de antes e a mensagem aparece
    // (30/09: "às vezes tinha que clicar várias vezes para iniciar").
    const anterior = applyOptimisticOperation(queryClient, userId, true);
    onOpenChange(false);
    try {
      const safeDraft: OperationConfig = {
        ...draft,
        entryValue: clampEntryValueForCurrency(draft.entryValue, accountCurrency),
        marketMode: coerceSelectableMarketMode(draft.marketMode),
      };
      const nextSettings = mergeOperationConfig(safeDraft, settings, accountCurrency);
      setSettings(nextSettings);
      persistLastOperationConfig(userId, safeDraft);
      const configResponse = await robotConfig({
        enabled: true,
        account_mode: "REAL",
        allow_real: true,
        confirm_real: true,
        entry_value: safeDraft.entryValue,
        cycle_minutes: cycleMinutesForTimeframe(safeDraft.timeframe),
        timeframe: safeDraft.timeframe,
        market_mode: safeDraft.marketMode,
        min_confidence: MIN_CONFIDENCE,
        min_payout: MIN_PAYOUT,
        stop_win: safeDraft.stopWin,
        stop_loss: safeDraft.stopLoss,
        stop_win_mode: safeDraft.stopWinMode,
        stop_loss_mode: safeDraft.stopLossMode,
        stop_win_operations: safeDraft.stopWinOperations,
        stop_loss_operations: safeDraft.stopLossOperations,
        // `nextSettings` já passou pela normalização: com o Gerenciamento
        // Consistente ligado o gale vai desligado.
        martingale_enabled: nextSettings.martingaleEnabled,
        martingale_steps: safeDraft.martingaleSteps,
        martingale_multiplier: safeDraft.martingaleMultiplier,
        ...masanielloConfigPayload(nextSettings),
        ai_analysis_enabled: false,
        ai_confirmation_required: false,
        ai_min_confidence: undefined,
      });
      if (!configResponse.ok) {
        throw new ApiError(configResponse.error, configResponse.code, configResponse.status);
      }
      // Marca como sincronizado com o que a pessoa confirmou, para o poll
      // seguinte não sobrescrever com estado antigo/incompleto.
      markRobotSettingsSynced(userId, nextSettings);
      const startResponse = await robotStart();
      if (!startResponse.ok) {
        throw new ApiError(startResponse.error, startResponse.code, startResponse.status);
      }
      const startedData = startResponse.data as { enabled?: boolean; status?: string } | undefined;
      if (startedData && startedData.enabled === false) {
        const status = String(startedData.status ?? "").toUpperCase();
        throw new ApiError(
          status === "INSUFFICIENT_BALANCE"
            ? "Você está sem saldo para iniciar. Faça um depósito na BullEx."
            : "A corretora não liberou o start. Reconecte a Bullex e tente de novo.",
          status || "START_NOT_ENABLED",
        );
      }
      await onStarted?.(startResponse.data);
      toast.success(
        `Operação iniciada em ${timeframeLabel(safeDraft.timeframe)} · ${marketModeLabel(safeDraft.marketMode)}`,
      );
    } catch (caught) {
      revertOptimisticOperation(queryClient, userId, anterior);
      const message = caught instanceof Error ? caught.message : "Não foi possível iniciar a operação.";
      setError(message);
      toast.error(message);
    } finally {
      setStarting(false);
    }
  }

  const selectedMarketOption = marketModeOptions.find(
    (option) => option.value === draft.marketMode,
  );
  const galeOn = draft.martingaleEnabled && !masanielloOn;
  // Por que o mercado aberto está travado (manutenção ou forex fechado), para
  // a linha de ajuda: os botões agora só têm o nome.
  const openMarketLock = marketModeOptions.some((option) => option.value === "OPEN")
    ? resolveMarketModeLock("OPEN", { openMarketAvailable, forexClosedMessage: openMarketCountdown })
    : null;
  // Números do plano quando o Gerenciamento Consistente vale nesta partida.
  const masanielloPlan = masanielloOn
    ? masanielloFormView({
        capital: draft.masanielloCapital,
        profile: draft.masanielloProfile,
        operations: draft.masanielloOperations,
        wins: draft.masanielloWins,
        payoutRef: MIN_PAYOUT,
        currency: accountCurrency,
        balance: accountBalance,
        continuing: continuingCycle,
      })
    : null;
  const summaryRows = operationSummaryRows(
    { ...draft, martingaleEnabled: galeOn },
    accountCurrency,
    masanielloPlan,
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-h-[92vh] gap-0 overflow-y-auto border-border bg-card p-0 sm:max-w-[840px]"
        // Sem isto o foco inicial cai no primeiro botão ("1 min") e ele ganha
        // um contorno que parece seleção. O foco continua preso no diálogo.
        onOpenAutoFocus={(event) => event.preventDefault()}
        onPointerDownOutside={(event) => {
          if (shouldIgnoreOutsideDismiss()) event.preventDefault();
        }}
        onInteractOutside={(event) => {
          if (shouldIgnoreOutsideDismiss()) event.preventDefault();
        }}
      >
        <DialogHeader className="space-y-1 border-b border-border px-5 py-3.5 pr-12 text-left">
          <DialogTitle className="text-base">Iniciar operação</DialogTitle>
          <DialogDescription className="text-xs">
            O El Capo analisa o mercado e entra quando encontra um padrão. Saldo da conta:{" "}
            <span className="font-semibold text-foreground">
              {formatBullExBalance(accountBalance, accountCurrency)}
            </span>
          </DialogDescription>
        </DialogHeader>

        <div className="grid md:grid-cols-[minmax(0,1fr)_260px]">
          <div className="min-w-0 space-y-4 px-5 py-4">
            <FormSection icon={Clock3} title="Operação">
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <FieldLabel>Tempo da vela</FieldLabel>
                  <div className="grid grid-cols-3 gap-1 rounded-lg border border-border bg-background/40 p-1">
                    {ROBOT_TIMEFRAME_OPTIONS.map((option) => (
                      <button
                        key={option.value}
                        type="button"
                        disabled={starting}
                        onClick={() => patchDraft({ timeframe: option.value })}
                        className={segmentClass(draft.timeframe === option.value)}
                      >
                        {cycleMinutesForTimeframe(option.value)} min
                      </button>
                    ))}
                  </div>
                  <FieldHint>
                    Expira em {cycleMinutesForTimeframe(draft.timeframe)} min · analisa a cada vela
                  </FieldHint>
                </div>
                <div>
                  <FieldLabel>Mercado</FieldLabel>
                  <div className="grid grid-cols-3 gap-1 rounded-lg border border-border bg-background/40 p-1">
                    {marketModeOptions.map((option) => {
                      const lock = resolveMarketModeLock(option.value, {
                        openMarketAvailable,
                        forexClosedMessage: openMarketCountdown,
                      });
                      const selected = draft.marketMode === option.value && !lock;
                      const optionButton = (
                        <button
                          key={option.value}
                          type="button"
                          // `aria-disabled` em vez de `disabled`: elemento desabilitado
                          // não dispara hover nem clique, e o cadeado precisa explicar.
                          disabled={!lock && starting}
                          aria-disabled={lock ? true : undefined}
                          title={lock ? undefined : option.description}
                          onClick={() => {
                            if (lock) return;
                            patchDraft({ marketMode: option.value });
                          }}
                          className={
                            lock
                              ? "inline-flex cursor-not-allowed items-center justify-center gap-1 rounded-md px-2 py-1.5 text-xs font-semibold text-muted-foreground opacity-60"
                              : segmentClass(selected)
                          }
                        >
                          {lock ? <Lock className="h-3 w-3 shrink-0" aria-hidden /> : null}
                          {MARKET_SHORT_LABEL[option.value] ?? option.label}
                        </button>
                      );
                      if (!lock) return optionButton;
                      return (
                        <MarketModeLockPopover key={option.value} lock={lock}>
                          {optionButton}
                        </MarketModeLockPopover>
                      );
                    })}
                  </div>
                  <FieldHint>
                    {selectedMarketOption?.description ?? "Escolha onde o robô procura entradas."}
                    {openMarketLock
                      ? ` Mercado aberto: ${
                          openMarketLock.reason === "maintenance"
                            ? OPEN_MARKET_MAINTENANCE_SHORT
                            : (openMarketCountdown ?? "fechado até a sessão forex")
                        }.`
                      : ""}
                  </FieldHint>
                </div>
              </div>
            </FormSection>

            <FormSection icon={ShieldCheck} title="Gerenciamento">
              <div className={`grid gap-2 ${consistentSupported ? "sm:grid-cols-2" : ""}`}>
                <div
                  className={`flex items-center justify-between gap-3 rounded-lg border bg-background/40 px-3 py-2 text-xs font-medium ${
                    galeOn ? "border-primary/60" : "border-border"
                  }`}
                >
                  <label htmlFor="start-dialog-gale">Gale</label>
                  <Switch
                    id="start-dialog-gale"
                    className="data-[state=unchecked]:bg-muted-foreground/30"
                    checked={galeOn}
                    disabled={starting || masanielloOn}
                    // Os dois não convivem: ligar o gale desliga o Gerenciamento
                    // Consistente (só dá para chegar aqui com ele fora de ação).
                    onCheckedChange={(checked) =>
                      patchDraft(
                        checked
                          ? { martingaleEnabled: true, masanielloEnabled: false }
                          : { martingaleEnabled: false },
                      )
                    }
                  />
                </div>
                {consistentSupported ? (
                  <ConsistentManagementToggle
                    compact
                    checked={masanielloOn}
                    disabled={starting}
                    liveOn={liveOn}
                    currency={accountCurrency}
                    onChange={(checked) =>
                      patchDraft(
                        checked
                          ? { masanielloEnabled: true, martingaleEnabled: false }
                          : { masanielloEnabled: false },
                      )
                    }
                  />
                ) : null}
              </div>

              {masanielloOn ? (
                <ConsistentManagementFields
                  compact
                  hideSummary
                  accent="dialog"
                  value={draft}
                  onChange={patchDraft}
                  disabled={starting}
                  currency={accountCurrency}
                  balance={accountBalance}
                  payoutRef={MIN_PAYOUT}
                  cycle={masanielloCycle}
                  onEndCycle={() => void endMasanielloCycle()}
                  endingCycle={endingCycle}
                />
              ) : (
                <div className="grid gap-3 sm:grid-cols-3">
                  <MoneyInput
                    label="Valor por entrada"
                    size="compact"
                    currency={accountCurrency}
                    min={entryLimitsForCurrency(accountCurrency).min}
                    step={ENTRY_VALUE_STEP}
                    value={draft.entryValue}
                    disabled={starting}
                    helperText={entryValueHelperText(accountCurrency)}
                    onChange={(value) => {
                      const parsed = parseEntryValueInput(value, draft.entryValue, accountCurrency);
                      if (parsed == null) return;
                      patchDraft({ entryValue: parsed });
                    }}
                  />
                  <StopField
                    kind="win"
                    mode={draft.stopWinMode}
                    moneyValue={draft.stopWin}
                    operationsValue={draft.stopWinOperations}
                    currency={accountCurrency}
                    disabled={starting}
                    onModeChange={(mode) => patchDraft({ stopWinMode: mode })}
                    onMoneyChange={(value) => {
                      const parsed = parseStopMoneyInput(value, draft.stopWin);
                      if (parsed == null) return;
                      patchDraft({ stopWin: parsed });
                    }}
                    onOperationsChange={(value) => {
                      const parsed = parseStopOperationsInput(value, draft.stopWinOperations);
                      if (parsed == null) return;
                      patchDraft({ stopWinOperations: parsed });
                    }}
                  />
                  <StopField
                    kind="loss"
                    mode={draft.stopLossMode}
                    moneyValue={draft.stopLoss}
                    operationsValue={draft.stopLossOperations}
                    currency={accountCurrency}
                    disabled={starting}
                    onModeChange={(mode) => patchDraft({ stopLossMode: mode })}
                    onMoneyChange={(value) => {
                      const parsed = parseStopMoneyInput(value, draft.stopLoss);
                      if (parsed == null) return;
                      patchDraft({ stopLoss: parsed });
                    }}
                    onOperationsChange={(value) => {
                      const parsed = parseStopOperationsInput(value, draft.stopLossOperations);
                      if (parsed == null) return;
                      patchDraft({ stopLossOperations: parsed });
                    }}
                  />
                  {galeOn ? (
                    <>
                      <NumberField
                        label="Quantidade de gales"
                        min={1}
                        max={10}
                        step={1}
                        value={draft.martingaleSteps}
                        disabled={starting}
                        onChange={(value) =>
                          patchDraft({
                            martingaleSteps: Math.max(
                              1,
                              Math.floor(clampNumber(value, 1, 10, draft.martingaleSteps)),
                            ),
                          })
                        }
                      />
                      <NumberField
                        label="Multiplicador do gale"
                        min={1}
                        max={20}
                        step={0.1}
                        value={draft.martingaleMultiplier}
                        disabled={starting}
                        onChange={(value) =>
                          patchDraft({
                            martingaleMultiplier: clampNumber(
                              value,
                              1,
                              20,
                              draft.martingaleMultiplier,
                            ),
                          })
                        }
                      />
                    </>
                  ) : null}
                </div>
              )}
            </FormSection>

            {!connected ? (
              <p className="text-xs text-amber-600 dark:text-amber-400">
                O painel ainda não confirma a Bullex. Ao confirmar, o servidor tenta iniciar mesmo
                assim; se falhar, reconecte em Configurações → Conta Corretora.
              </p>
            ) : null}
          </div>

          {/* Resumo: o que o robô vai fazer com o que está na tela, e o botão. */}
          <aside className="flex flex-col border-t border-border bg-primary/[0.06] px-4 py-4 md:border-l md:border-t-0">
            <p className="text-[11px] font-bold uppercase tracking-wider text-primary">Resumo</p>
            <p className="mt-0.5 text-[11px] text-muted-foreground">O que o robô vai fazer</p>
            <dl className="mt-2">
              {summaryRows.map((row) => (
                <div
                  key={row.label}
                  className="flex items-baseline justify-between gap-2 border-b border-border/60 py-1.5 last:border-b-0"
                >
                  <dt className="text-[11px] text-muted-foreground">{row.label}</dt>
                  <dd
                    className={`whitespace-nowrap text-[13px] font-bold tabular-nums ${
                      row.tone === "positive"
                        ? "text-primary"
                        : row.tone === "negative"
                          ? "text-destructive"
                          : "text-foreground"
                    }`}
                  >
                    {row.value}
                  </dd>
                </div>
              ))}
            </dl>
            {masanielloOn ? (
              <p className="mt-2 text-[11px] leading-snug text-muted-foreground">
                O ciclo é o stop. Se os erros se esgotarem,{" "}
                <strong className="text-foreground">
                  o capital do ciclo é perdido por inteiro
                </strong>
                .
              </p>
            ) : null}

            <div className="mt-auto space-y-2 pt-4">
              {balanceError && !masanielloOn ? (
                <p className="text-xs text-destructive">{balanceError}</p>
              ) : null}
              {error && error !== balanceError ? (
                <p className="text-xs text-destructive">{error}</p>
              ) : null}
              <button
                type="button"
                data-testid="confirm-start-operation"
                aria-busy={starting}
                onPointerDown={(event) => {
                  // Evita que o gesto do mobile “vaze” para o overlay Radix e
                  // cancele o clique de confirmar no mesmo toque.
                  event.stopPropagation();
                }}
                onClick={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  void confirmAndStart();
                }}
                disabled={starting || robotRunning || Boolean(balanceError)}
                className="inline-flex min-h-10 w-full cursor-pointer items-center justify-center gap-2 rounded-lg bg-success px-5 py-2 text-sm font-semibold text-success-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {starting ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                ) : (
                  <Play className="h-4 w-4" aria-hidden />
                )}
                {starting ? "Iniciando..." : "Confirmar e iniciar"}
              </button>
              <button
                type="button"
                onClick={applyLastConfig}
                disabled={starting || !hasSavedConfig}
                className="inline-flex w-full cursor-pointer items-center justify-center gap-1.5 rounded-lg px-2 py-1 text-xs font-semibold text-muted-foreground transition hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
              >
                <History className="h-3.5 w-3.5" />
                Últimas configurações
              </button>
            </div>
          </aside>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function segmentClass(selected: boolean): string {
  return `inline-flex cursor-pointer items-center justify-center gap-1 rounded-md px-2 py-1.5 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${
    selected
      ? "bg-primary/15 text-foreground ring-1 ring-primary/60"
      : "text-muted-foreground hover:bg-accent hover:text-foreground"
  }`;
}

function FormSection({
  icon: Icon,
  title,
  children,
}: {
  icon: LucideIcon;
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-2.5">
      <h3 className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
        <Icon className="h-3.5 w-3.5 text-primary" aria-hidden />
        {title}
      </h3>
      {children}
    </section>
  );
}

function FieldLabel({ children }: { children: ReactNode }) {
  return <p className="mb-1 text-xs font-medium text-muted-foreground">{children}</p>;
}

function FieldHint({ children }: { children: ReactNode }) {
  return <p className="mt-1 text-[11px] leading-snug text-muted-foreground">{children}</p>;
}

interface NumberFieldProps {
  label: string;
  value: number;
  onChange: (value: string) => void;
  disabled?: boolean;
  min?: number;
  max?: number;
  step?: number | string;
}

function NumberField({ label, value, onChange, disabled, min, max, step }: NumberFieldProps) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-muted-foreground">{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className={COMPACT_INPUT}
      />
    </label>
  );
}

const COMPACT_INPUT =
  "w-full rounded-lg border border-border bg-background/40 px-2 py-2 text-xs font-semibold outline-none ring-primary focus:ring-2 disabled:opacity-50";

interface StopFieldProps {
  kind: "win" | "loss";
  mode: RobotStopMode;
  moneyValue: number;
  operationsValue: number;
  currency?: string | null;
  disabled?: boolean;
  onModeChange: (mode: RobotStopMode) => void;
  onMoneyChange: (value: string) => void;
  onOperationsChange: (value: string) => void;
}

/**
 * Stop Win/Loss em uma coluna: o rótulo leva o seletor de modo (valor ou
 * quantidade de operações) e o campo muda junto.
 */
function StopField({
  kind,
  mode,
  moneyValue,
  operationsValue,
  currency,
  disabled,
  onModeChange,
  onMoneyChange,
  onOperationsChange,
}: StopFieldProps) {
  const title = kind === "win" ? "Stop Win" : "Stop Loss";
  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-muted-foreground">{title}</span>
        <div className="flex rounded-md border border-border p-0.5">
          {(
            [
              { value: "money" as const, label: currencySymbol(currency), hint: "Por valor" },
              { value: "operations" as const, label: "Qtd", hint: "Por operações" },
            ] as const
          ).map((option) => (
            <button
              key={option.value}
              type="button"
              disabled={disabled}
              title={option.hint}
              aria-label={`${title}: ${option.hint.toLowerCase()}`}
              aria-pressed={mode === option.value}
              onClick={() => onModeChange(option.value)}
              className={`cursor-pointer rounded px-1.5 py-0.5 text-[10px] font-bold transition disabled:cursor-not-allowed disabled:opacity-50 ${
                mode === option.value
                  ? "bg-primary/20 text-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
      {mode === "money" ? (
        <MoneyInput
          label={title}
          hideLabel
          size="compact"
          currency={currency}
          min={STOP_MONEY_MIN}
          step="any"
          value={moneyValue}
          disabled={disabled}
          helperText={`Mínimo ${formatBullExBalance(STOP_MONEY_MIN, currency)}`}
          onChange={onMoneyChange}
        />
      ) : (
        <>
          <input
            type="number"
            min={STOP_OPERATIONS_MIN}
            max={STOP_OPERATIONS_MAX}
            step={1}
            value={operationsValue}
            disabled={disabled}
            aria-label={`${title} em quantidade de operações`}
            onChange={(event) => onOperationsChange(event.target.value)}
            className={COMPACT_INPUT}
          />
          <span className="mt-1 block text-[11px] text-muted-foreground">
            {kind === "win" ? "Para após estes WINs" : "Para após estes LOSSes"}
          </span>
        </>
      )}
    </div>
  );
}

function clampNumber(raw: string, min: number, max: number, fallback: number): number {
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? Math.min(max, Math.max(min, parsed)) : fallback;
}
