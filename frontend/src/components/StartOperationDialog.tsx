import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { History, Loader2, Lock, Play } from "lucide-react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ApiError, apiConfig, robotConfig, robotStart } from "@/lib/api";
import { ROBOT_STATE_QUERY_KEY } from "@/hooks/useLiveTradingData";
import { entryValueBalanceError, formatBullExBalance } from "@/lib/bullexConnection";
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
  };
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
  const balanceError = useMemo(
    () => entryValueBalanceError(accountBalance, draft.entryValue),
    [accountBalance, draft.entryValue],
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
  const queryClient = useQueryClient();
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
    const invalidBalance = entryValueBalanceError(accountBalance, draft.entryValue);
    if (invalidBalance) {
      setError(invalidBalance);
      toast.error(invalidBalance);
      return;
    }
    setStarting(true);
    setError(null);
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
        martingale_enabled: safeDraft.martingaleEnabled,
        martingale_steps: safeDraft.martingaleSteps,
        martingale_multiplier: safeDraft.martingaleMultiplier,
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
      onOpenChange(false);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Não foi possível iniciar a operação.";
      setError(message);
      toast.error(message);
    } finally {
      setStarting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-h-[90vh] overflow-y-auto border-border bg-card sm:max-w-xl"
        onPointerDownOutside={(event) => {
          if (shouldIgnoreOutsideDismiss()) event.preventDefault();
        }}
        onInteractOutside={(event) => {
          if (shouldIgnoreOutsideDismiss()) event.preventDefault();
        }}
      >
        <DialogHeader>
          <DialogTitle>Iniciar operação</DialogTitle>
          <DialogDescription>
            Escolha o timeframe e o tipo de mercado. A IA monitora o mercado o tempo todo e só
            entra quando identifica um padrão das estratégias (Price Action, Psicologia de velas e
            Padrões de vela), respeitando a janela de compra do timeframe (M1/M5/M15). Valores
            seguem a moeda da conta conectada ({formatBullExBalance(accountBalance, accountCurrency)}).
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <p className="text-sm font-medium">Timeframe da operação</p>
            <div className="mt-2 grid gap-2 sm:grid-cols-3">
              {ROBOT_TIMEFRAME_OPTIONS.map((option) => {
                const selected = draft.timeframe === option.value;
                return (
                  <button
                    key={option.value}
                    type="button"
                    disabled={starting}
                    onClick={() => patchDraft({ timeframe: option.value })}
                    className={`rounded-xl border px-3 py-3 text-left transition disabled:opacity-50 ${selected ? "border-primary bg-primary/15 text-foreground" : "border-border bg-background/40 text-muted-foreground hover:bg-accent"}`}
                  >
                    <span className="block text-sm font-semibold">{option.label}</span>
                    <span className="mt-1 block text-xs opacity-80">
                      Monitora a cada vela · expira em {cycleMinutesForTimeframe(option.value)} min
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
          <div>
            <p className="text-sm font-medium">Mercado analisado</p>
            <div className="mt-2 grid gap-2 sm:grid-cols-3">
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
                    onClick={() => {
                      if (lock) return;
                      patchDraft({ marketMode: option.value });
                    }}
                    className={`rounded-xl border px-3 py-3 text-left transition disabled:opacity-50 ${
                      lock
                        ? "cursor-not-allowed border-border/60 bg-background/20 text-muted-foreground opacity-60"
                        : selected
                          ? "border-primary bg-primary/15 text-foreground"
                          : "border-border bg-background/40 text-muted-foreground hover:bg-accent"
                    }`}
                  >
                    <span className="flex items-center gap-1.5 text-sm font-semibold">
                      {lock ? <Lock className="h-3.5 w-3.5 shrink-0 opacity-80" aria-hidden /> : null}
                      {option.label}
                    </span>
                    <span className="mt-1 block text-xs opacity-80">
                      {lock
                        ? lock.reason === "maintenance"
                          ? OPEN_MARKET_MAINTENANCE_SHORT
                          : (openMarketCountdown ?? "Fechado até a sessão forex")
                        : option.description}
                    </span>
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
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <MoneyInput
              label="Valor por entrada"
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
            <div className="space-y-2 sm:col-span-2">
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
            </div>
            <label className="flex items-center justify-between gap-3 rounded-xl border border-border bg-background/40 px-4 py-3 text-sm font-medium">
              <span>Gale ativado</span>
              <input
                type="checkbox"
                checked={draft.martingaleEnabled}
                disabled={starting}
                onChange={(event) => patchDraft({ martingaleEnabled: event.target.checked })}
                className="h-4 w-4 accent-primary"
              />
            </label>
            <NumberField
              label="Quantidade de Gales"
              type="number"
              min={1}
              step={1}
              value={draft.martingaleSteps}
              disabled={starting || !draft.martingaleEnabled}
              onChange={(value) =>
                patchDraft({
                  martingaleSteps: Math.max(1, Math.floor(clampNumber(value, 1, 10, draft.martingaleSteps))),
                })
              }
            />
            <NumberField
              label="Multiplicador do Gale"
              type="number"
              min={1}
              step={0.1}
              value={draft.martingaleMultiplier}
              disabled={starting || !draft.martingaleEnabled}
              onChange={(value) =>
                patchDraft({ martingaleMultiplier: clampNumber(value, 1, 20, draft.martingaleMultiplier) })
              }
            />
          </div>
          {balanceError ? <p className="text-sm text-destructive">{balanceError}</p> : null}
          {!connected ? (
            <p className="text-sm text-amber-600 dark:text-amber-400">
              O painel ainda não confirma a Bullex (pode ser cache/backoff). Ao confirmar, o
              servidor tenta iniciar mesmo assim; se falhar, reconecte em Configurações → Conta
              Corretora.
            </p>
          ) : null}
          {error && error !== balanceError ? <p className="text-sm text-destructive">{error}</p> : null}
        </div>
        <DialogFooter className="gap-2 sm:justify-between">
          <button
            type="button"
            onClick={applyLastConfig}
            disabled={starting || !hasSavedConfig}
            className="inline-flex items-center justify-center gap-2 rounded-lg border border-border bg-background/40 px-4 py-2 text-sm font-semibold transition hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
          >
            <History className="h-4 w-4" />
            Últimas configurações
          </button>
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
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-success px-4 py-2.5 text-sm font-semibold text-success-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {starting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />}
            {starting ? "Iniciando..." : "Confirmar e iniciar"}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface NumberFieldProps {
  label: string;
  value: number;
  onChange: (value: string) => void;
  disabled?: boolean;
  type?: string;
  min?: number;
  max?: number;
  step?: number | string;
}

function NumberField({ label, value, onChange, disabled, type = "number", min, max, step }: NumberFieldProps) {
  return (
    <label className="block space-y-1.5 text-sm">
      <span className="font-medium">{label}</span>
      <input
        type={type}
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-xl border border-border bg-background/40 px-3 py-2 outline-none ring-primary focus:ring-2 disabled:opacity-50"
      />
    </label>
  );
}

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
 * Campo de Stop Win/Loss com escolha entre valor em R$ e quantidade de operações.
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
  const opsHelper =
    kind === "win" ? "Para após esta quantidade de WINs" : "Para após esta quantidade de LOSSes";

  return (
    <div className="rounded-xl border border-border bg-background/30 p-3 space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">{title}</p>
        <div className="flex gap-1">
          {(
            [
              { value: "money" as const, label: "Por valor" },
              { value: "operations" as const, label: "Por operações" },
            ] as const
          ).map((option) => {
            const selected = mode === option.value;
            return (
              <button
                key={option.value}
                type="button"
                disabled={disabled}
                onClick={() => onModeChange(option.value)}
                className={`rounded-lg border px-2.5 py-1 text-[11px] font-semibold transition disabled:opacity-50 ${
                  selected
                    ? "border-primary bg-primary/15 text-foreground"
                    : "border-border bg-background/40 text-muted-foreground hover:bg-accent"
                }`}
              >
                {option.label}
              </button>
            );
          })}
        </div>
      </div>
      {mode === "money" ? (
        <MoneyInput
          label={title}
          currency={currency}
          min={STOP_MONEY_MIN}
          step="any"
          value={moneyValue}
          disabled={disabled}
          helperText={`Mínimo ${formatBullExBalance(STOP_MONEY_MIN, currency)}`}
          onChange={onMoneyChange}
        />
      ) : (
        <NumberField
          label={kind === "win" ? "Quantidade de WINs" : "Quantidade de LOSSes"}
          type="number"
          min={STOP_OPERATIONS_MIN}
          max={STOP_OPERATIONS_MAX}
          step={1}
          value={operationsValue}
          disabled={disabled}
          onChange={onOperationsChange}
        />
      )}
      {mode === "operations" ? <p className="text-xs text-muted-foreground">{opsHelper}</p> : null}
    </div>
  );
}

function clampNumber(raw: string, min: number, max: number, fallback: number): number {
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? Math.min(max, Math.max(min, parsed)) : fallback;
}
