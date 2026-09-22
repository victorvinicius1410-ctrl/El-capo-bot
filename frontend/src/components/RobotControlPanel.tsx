import { useEffect, useMemo, useState } from "react";
import { Bot, Loader2, Lock, Save } from "lucide-react";
import { toast } from "sonner";
import { MoneyInput } from "@/components/MoneyInput";
import { MarketModeLockPopover } from "@/components/MarketModeLockPopover";
import {
  OPEN_MARKET_MAINTENANCE_SHORT,
  resolveMarketModeLock,
} from "@/lib/openMarketMaintenance";
import { useLiveTradingData, applyRobotMutationToCache } from "@/hooks/useLiveTradingData";
import { useRobotSettings } from "@/hooks/useRobotSettings";
import { robotStart, robotStop } from "@/lib/api";
import { entryValueBalanceError, formatBullExBalance, isBullExConnected } from "@/lib/bullexConnection";
import { isRobotOperationRunning } from "@/lib/robotState";
import { useAuth } from "@/lib/useAuth";
import { useQueryClient } from "@tanstack/react-query";
import {
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
  parseEntryValueInput,
  parseStopMoneyInput,
  parseStopOperationsInput,
  visibleRobotMarketModeOptions,
  type RobotSettings,
  type RobotStopMode,
} from "@/lib/robotSettings";

/**
 * Painel completo de configuração do robô (aba Configurações → Robô).
 *
 * Espelha os mesmos campos usados ao iniciar operação na corretora:
 * timeframe, mercado, entrada, stop win/loss e gale.
 */
export function RobotControlPanel() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const { account, accountStatus, robotState } = useLiveTradingData();
  const { settings, setSettings, saveSettings } = useRobotSettings(user?.id);
  const [pending, setPending] = useState<"save" | "toggle" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const running = isRobotOperationRunning(robotState.data);
  // Só faz sentido enquanto o robô está parado: assim que religa, o backend
  // limpa a flag e o aviso some sozinho.
  const pausedByMaintenance = Boolean(robotState.data?.paused_by_maintenance) && !running;
  const connected = isBullExConnected({ account: account.data, accountStatus: accountStatus.data });
  const currency = account.data?.currency ?? null;
  const balance = account.data?.balance ?? null;
  const entryLimits = entryLimitsForCurrency(currency);
  const balanceError = useMemo(
    () => entryValueBalanceError(balance, settings.entryValue),
    [balance, settings.entryValue],
  );

  useEffect(() => {
    const next = clampEntryValueForCurrency(settings.entryValue, currency);
    if (next !== settings.entryValue) {
      setSettings({ ...settings, entryValue: next });
    }
  }, [currency, settings, setSettings]);

  function patch(patch: Partial<RobotSettings>): void {
    setSettings({ ...settings, ...patch });
  }

  async function persist(): Promise<boolean> {
    const selectableMode = coerceSelectableMarketMode(settings.marketMode);
    const toSave = {
      ...(selectableMode === settings.marketMode
        ? settings
        : { ...settings, marketMode: selectableMode }),
      entryValue: clampEntryValueForCurrency(settings.entryValue, currency),
    };
    if (toSave.marketMode !== settings.marketMode) {
      setSettings(toSave);
    }
    if (balanceError) {
      setError(balanceError);
      toast.error(balanceError);
      return false;
    }
    setPending("save");
    setError(null);
    try {
      await saveSettings(toSave, {
        enabled: running,
        accountMode: "REAL",
        allowReal: true,
        confirmReal: true,
      });
      toast.success("Configurações do robô salvas.");
      return true;
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Não foi possível salvar.";
      setError(message);
      toast.error(message);
      return false;
    } finally {
      setPending(null);
    }
  }

  async function toggle(): Promise<void> {
    setPending("toggle");
    setError(null);
    try {
      if (!running) {
        // Não bloquear por `connected` do poll — mesmo motivo do overlay:
        // backoff/cache falso. O POST /robot/start valida de verdade.
        const saved = await persist();
        if (!saved) return;
        setPending("toggle");
        const start = await robotStart();
        if (!start.ok) {
          setError(start.error);
          toast.error(start.error);
          return;
        }
        if (user?.id && start.data) {
          applyRobotMutationToCache(queryClient, user.id, start.data);
        }
        toast.success("Robô iniciado.");
      } else {
        const stop = await robotStop();
        if (!stop.ok) {
          setError(stop.error);
          toast.error(stop.error);
          return;
        }
        if (user?.id && stop.data) {
          applyRobotMutationToCache(queryClient, user.id, stop.data);
        }
        toast.success("Robô parado.");
      }
      void robotState.refetch();
    } finally {
      setPending(null);
    }
  }

  const busy = pending !== null;
  const marketModeOptions = useMemo(() => visibleRobotMarketModeOptions(), []);
  const [nowTick, setNowTick] = useState(() => Date.now());
  // O relógio do navegador só conhece a janela semanal do forex — e erra em
  // FERIADO, que foi o que enganou a medição de 07/09/2026. O servidor cruza
  // essa janela com o que a corretora responde por ativo, então sabe mais.
  // Sem resposta do servidor ainda, o relógio local é o que temos: travar o
  // painel por falta de dado seria pior que o defeito que isto conserta.
  const openMarketDoServidor = robotState.data?.open_market_available;
  const openMarketAvailable =
    typeof openMarketDoServidor === "boolean"
      ? openMarketDoServidor
      : isForexOpenMarketAvailable(new Date(nowTick));
  const openMarketCountdown = formatForexOpenCountdown(new Date(nowTick));

  useEffect(() => {
    if (openMarketAvailable) return;
    const timer = window.setInterval(() => setNowTick(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, [openMarketAvailable]);

  useEffect(() => {
    const selectable = coerceSelectableMarketMode(settings.marketMode);
    if (selectable !== settings.marketMode && !running) {
      patch({ marketMode: selectable });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- só reage ao modo/sessão
  }, [settings.marketMode, running, openMarketAvailable]);

  return (
    <section className="config-panel-stack space-y-5">
      <header className="config-panel-head">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-semibold">
            <Bot className="h-5 w-5" /> Robô
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Defina aqui o que o robô usa na corretora: timeframe, mercado, valores e gale.
            Saldo conectado: {formatBullExBalance(balance, currency)}.
          </p>
        </div>
        <span>{running ? "Em operação" : "Parado"}</span>
      </header>

      <div className="config-surface space-y-5">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-[#8fb0b8]">
            Timeframe da operação
          </p>
          <div className="mt-2 grid gap-2 sm:grid-cols-3">
            {ROBOT_TIMEFRAME_OPTIONS.map((option) => {
              const selected = settings.timeframe === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  disabled={busy || running}
                  onClick={() => patch({ timeframe: option.value })}
                  className={`rounded-xl border px-3 py-3 text-left transition disabled:opacity-50 ${
                    selected
                      ? "border-[#7ef0f3]/70 bg-[#7ef0f3]/10 text-foreground"
                      : "border-border bg-background/40 text-muted-foreground hover:bg-accent"
                  }`}
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
          <p className="text-xs font-semibold uppercase tracking-wide text-[#8fb0b8]">
            Mercado analisado
          </p>
          <div className="mt-2 grid gap-2 sm:grid-cols-3">
            {marketModeOptions.map((option) => {
              const lock = resolveMarketModeLock(option.value, {
                openMarketAvailable,
                forexClosedMessage: openMarketCountdown,
              });
              const selected = settings.marketMode === option.value && !lock;
              const optionButton = (
                <button
                  key={option.value}
                  type="button"
                  // `aria-disabled` em vez de `disabled`: elemento desabilitado
                  // não dispara hover nem clique, e o cadeado precisa explicar.
                  disabled={!lock && (busy || running)}
                  aria-disabled={lock ? true : undefined}
                  onClick={() => {
                    if (lock) return;
                    patch({ marketMode: option.value });
                  }}
                  className={`rounded-xl border px-3 py-3 text-left transition disabled:opacity-50 ${
                    lock
                      ? "cursor-not-allowed border-border/60 bg-background/20 text-muted-foreground opacity-60"
                      : selected
                        ? "border-[#7ef0f3]/70 bg-[#7ef0f3]/10 text-foreground"
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
            currency={currency}
            min={entryLimits.min}
            step={ENTRY_VALUE_STEP}
            value={settings.entryValue}
            disabled={busy || running}
            helperText={entryValueHelperText(currency)}
            onChange={(value) => {
              const parsed = parseEntryValueInput(value, settings.entryValue, currency);
              if (parsed == null) return;
              patch({ entryValue: parsed });
            }}
          />
          <div className="space-y-2 sm:col-span-2">
            <PanelStopField
              kind="win"
              mode={settings.stopWinMode}
              moneyValue={settings.stopWin}
              operationsValue={settings.stopWinOperations}
              currency={currency}
              disabled={busy || running}
              onModeChange={(mode) => patch({ stopWinMode: mode })}
              onMoneyChange={(value) => {
                const parsed = parseStopMoneyInput(value, settings.stopWin);
                if (parsed == null) return;
                patch({ stopWin: parsed });
              }}
              onOperationsChange={(value) => {
                const parsed = parseStopOperationsInput(value, settings.stopWinOperations);
                if (parsed == null) return;
                patch({ stopWinOperations: parsed });
              }}
            />
            <PanelStopField
              kind="loss"
              mode={settings.stopLossMode}
              moneyValue={settings.stopLoss}
              operationsValue={settings.stopLossOperations}
              currency={currency}
              disabled={busy || running}
              onModeChange={(mode) => patch({ stopLossMode: mode })}
              onMoneyChange={(value) => {
                const parsed = parseStopMoneyInput(value, settings.stopLoss);
                if (parsed == null) return;
                patch({ stopLoss: parsed });
              }}
              onOperationsChange={(value) => {
                const parsed = parseStopOperationsInput(value, settings.stopLossOperations);
                if (parsed == null) return;
                patch({ stopLossOperations: parsed });
              }}
            />
          </div>
          <label className="flex items-center justify-between gap-3 rounded-xl border border-border bg-background/40 px-4 py-3 text-sm font-medium">
            <span>Gale ativado</span>
            <input
              type="checkbox"
              checked={settings.martingaleEnabled}
              disabled={busy || running}
              onChange={(event) => patch({ martingaleEnabled: event.target.checked })}
              className="h-4 w-4 accent-primary"
            />
          </label>
          <label className="block space-y-1.5 text-sm">
            <span className="font-medium">Quantidade de Gales</span>
            <input
              type="number"
              min={1}
              max={10}
              step={1}
              value={settings.martingaleSteps}
              disabled={busy || running || !settings.martingaleEnabled}
              onChange={(event) =>
                patch({
                  martingaleSteps: Math.max(1, Math.floor(Number(event.target.value) || 1)),
                })
              }
              className="config-field w-full"
            />
          </label>
          <label className="block space-y-1.5 text-sm">
            <span className="font-medium">Multiplicador do Gale</span>
            <input
              type="number"
              min={1}
              max={20}
              step={0.1}
              value={settings.martingaleMultiplier}
              disabled={busy || running || !settings.martingaleEnabled}
              onChange={(event) =>
                patch({
                  martingaleMultiplier: Math.max(1, Number(event.target.value) || 2),
                })
              }
              className="config-field w-full"
            />
          </label>
        </div>

        {pausedByMaintenance ? (
          <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-600 dark:text-amber-400">
            Seu robô foi pausado por uma manutenção no servidor — nenhuma operação
            foi perdida. Clique em <strong>Iniciar robô</strong> para voltar a operar.
          </p>
        ) : null}
        {balanceError ? <p className="text-sm text-destructive">{balanceError}</p> : null}
        {error && error !== balanceError ? <p className="text-sm text-destructive">{error}</p> : null}

        <div className="flex flex-wrap gap-2 pt-1">
          <button
            type="button"
            onClick={() => void persist()}
            disabled={busy || running || Boolean(balanceError)}
            className="config-btn-ghost"
          >
            {pending === "save" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Salvar configurações
          </button>
          <button
            type="button"
            onClick={() => void toggle()}
            disabled={busy || (!running && Boolean(balanceError))}
            className="config-cta"
          >
            {pending === "toggle" ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {running ? "Parar robô" : "Iniciar robô"}
          </button>
        </div>
      </div>
    </section>
  );
}

interface PanelStopFieldProps {
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
 * Stop Win/Loss do painel Configurações → Robô com modo valor ou operações.
 */
function PanelStopField({
  kind,
  mode,
  moneyValue,
  operationsValue,
  currency,
  disabled,
  onModeChange,
  onMoneyChange,
  onOperationsChange,
}: PanelStopFieldProps) {
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
                    ? "border-[#7ef0f3]/70 bg-[#7ef0f3]/10 text-foreground"
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
        <label className="block space-y-1.5 text-sm">
          <span className="font-medium">
            {kind === "win" ? "Quantidade de WINs" : "Quantidade de LOSSes"}
          </span>
          <input
            type="number"
            min={STOP_OPERATIONS_MIN}
            max={STOP_OPERATIONS_MAX}
            step={1}
            value={operationsValue}
            disabled={disabled}
            onChange={(event) => onOperationsChange(event.target.value)}
            className="config-field w-full"
          />
          <span className="block text-xs text-muted-foreground">{opsHelper}</span>
        </label>
      )}
    </div>
  );
}
