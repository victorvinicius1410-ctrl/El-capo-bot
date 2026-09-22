import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode, type TouchEvent as ReactTouchEvent, type TouchList } from "react";
import { Eye, EyeOff, RotateCcw, Settings, Volume2, VolumeX, X } from "lucide-react";
import { toast } from "sonner";
import { MoneyInput } from "./MoneyInput";
import { RobotAvatarVideo } from "./RobotAvatarVideo";
import type { BullExAccount } from "@/lib/api";
import { formatBullExBalance, formatProfitAmount, profitTone } from "@/lib/bullexConnection";
import {
  ROBOT_OVERLAY_SCALE_DEFAULT,
  loadRobotOverlayScale,
  persistRobotOverlayScale,
  scaleFromPinch,
  scaleFromResizeDrag,
  scaleFromWheel,
} from "@/lib/robotOverlayScale";
import {
  formatCountdownFooter,
  getRobotStatusPresentation,
  type RobotPresentation,
} from "@/lib/robotPresentation";
import { maskMoney, privacyToggleLabel, togglePrivacyMode, usePrivacyMode } from "@/lib/privacyMode";
import { isStudyActive } from "@/lib/studyMode";
import {
  DEFAULT_ROBOT_SETTINGS,
  ENTRY_VALUE_STEP,
  STOP_MONEY_MIN,
  entryLimitsForCurrency,
  entryValueHelperText,
  parseEntryValueInput,
  parseStopMoneyInput,
  type RobotSettings,
} from "@/lib/robotSettings";
import { liveDisplayCountdownSeconds, type RobotState } from "@/lib/robotState";

const VIEWPORT_MARGIN = 12;

export interface RobotOverlayAdminControls {
  onAddWin: () => void;
  onResetScore: () => void;
}

export interface RobotOverlayProps {
  robotState?: RobotState | null;
  account?: BullExAccount | null;
  narratorEnabled?: boolean;
  narratorSpeaking?: boolean;
  narratorMuted?: boolean;
  onSilenceNarrator?: () => void;
  settings?: RobotSettings;
  onSettingsChange?: (settings: RobotSettings) => void | Promise<void>;
  onClose?: () => void;
  showConfig?: boolean;
  onStartOperation?: () => void;
  onStopOperation?: () => void;
  onResetScore?: () => void;
  /** Quando true, o overlay ignora pointer (ex.: diálogo Iniciar aberto). */
  interactionLocked?: boolean;
  startOperationDisabled?: boolean;
  stopOperationDisabled?: boolean;
  resetScoreDisabled?: boolean;
  startOperationLabel?: string;
  stopOperationLabel?: string;
  resetScoreLabel?: string;
  operationRunning?: boolean;
  adminModelControls?: RobotOverlayAdminControls;
  layerClassName?: string;
}

interface OverlayPosition {
  x: number;
  y: number;
}

/**
 * Robô flutuante arrastável com placar, resultado financeiro, narração e menu
 * de configurações rápidas. Espelha o overlay do bundle publicado.
 */
export function RobotOverlay({
  robotState,
  account,
  narratorEnabled = false,
  narratorSpeaking = false,
  narratorMuted = false,
  onSilenceNarrator,
  settings = DEFAULT_ROBOT_SETTINGS,
  onSettingsChange,
  onClose,
  showConfig,
  onStartOperation,
  onStopOperation,
  onResetScore,
  interactionLocked = false,
  startOperationDisabled = false,
  stopOperationDisabled = false,
  resetScoreDisabled = false,
  startOperationLabel = "Iniciar Operação",
  stopOperationLabel = "Parar Operação",
  resetScoreLabel = "Reiniciar placar",
  operationRunning = false,
  adminModelControls,
  layerClassName = "z-50",
}: RobotOverlayProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const dragOffsetRef = useRef({ x: 0, y: 0 });
  const resizeStateRef = useRef<{ pointerId: number; startX: number; startY: number; startScale: number } | null>(null);
  const pinchStateRef = useRef<{ startDistance: number; startScale: number } | null>(null);
  const scaleRef = useRef(ROBOT_OVERLAY_SCALE_DEFAULT);
  const wheelCommitTimerRef = useRef<number | null>(null);
  const [dragging, setDragging] = useState(false);
  const [resizing, setResizing] = useState(false);
  const [position, setPosition] = useState<OverlayPosition | null>(null);
  const [configOpen, setConfigOpen] = useState(false);
  const [draftSettings, setDraftSettings] = useState<RobotSettings>(settings);
  const [scale, setScale] = useState(ROBOT_OVERLAY_SCALE_DEFAULT);
  const [speechDetailOpen, setSpeechDetailOpen] = useState(false);
  const lastKnownScoreRef = useRef({ wins: 0, losses: 0, profit: 0 as number | null });
  if (robotState) {
    lastKnownScoreRef.current = {
      wins: robotState.wins,
      losses: robotState.losses,
      profit: robotState.profit,
    };
  }
  const scoreWins = robotState?.wins ?? lastKnownScoreRef.current.wins;
  const scoreLosses = robotState?.losses ?? lastKnownScoreRef.current.losses;
  const scoreProfit = robotState ? robotState.profit : lastKnownScoreRef.current.profit;
  const now = useNowTicker();
  const presentation = getRobotStatusPresentation(robotState, now);
  // LIVE: sem balão de entrada e sem resultado LOSS; o contador prévio de
  // losses permanece visível e congelado.
  const study = isStudyActive(robotState);
  // Modo privacidade: o olho esconde só o saldo da conta na linha de baixo.
  // Placar, resultado financeiro e o resto do painel seguem à mostra.
  const privacyOn = usePrivacyMode();
  const display = buildOverlayDisplay(robotState, presentation, now, study);
  const locked = isRobotLocked(robotState);
  // Só pending_signal = entrada travada. best_candidate/last_signal na análise
  // mentiam o balão ("vou de CALL…") e a ordem depois não saía.
  const speechSignal = study ? null : (robotState?.pending_signal ?? null);
  const speechPreview = speechSignal?.speech_preview?.trim() || speechSignal?.strategy_summary?.trim() || null;
  const showSpeechBubble =
    Boolean(speechPreview) &&
    ["SIGNAL_FOUND", "WAITING_ENTRY", "WAITING_ENTRY_WINDOW", "WAITING_NEXT_CANDLE_ENTRY", "BUYING", "SENDING_ORDER"].includes(
      robotState?.status ?? "",
    );
  // No estudo a explicação vem do win que acabou de fechar.
  const studyWinTrade = study && presentation.result === "WIN" ? presentation.trade : null;
  const speechView = speechSignal
    ? {
        symbol: speechSignal.symbol,
        direction: speechSignal.direction,
        strategyName: speechSignal.strategy_name,
        preview: speechPreview,
        detail:
          speechSignal.analysis_detail?.trim() ||
          speechSignal.strategy_reason?.trim() ||
          speechSignal.reason?.trim() ||
          null,
      }
    : studyWinTrade
      ? {
          symbol: studyWinTrade.active,
          direction: studyWinTrade.direction,
          strategyName: studyWinTrade.strategy_name,
          preview: studyWinTrade.speech_preview?.trim() || studyWinTrade.strategy_summary?.trim() || null,
          detail:
            studyWinTrade.analysis_detail?.trim() ||
            studyWinTrade.strategy_reason?.trim() ||
            studyWinTrade.entry_reason?.trim() ||
            null,
        }
      : null;
  // A janela fecha junto com o que ela explica; senão reabria sozinha no próximo win.
  const hasSpeechView = Boolean(speechView);
  useEffect(() => {
    if (!hasSpeechView) setSpeechDetailOpen(false);
  }, [hasSpeechView]);

  useEffect(() => {
    if (configOpen) return;
    setDraftSettings(settings);
  }, [configOpen, settings]);

  useEffect(() => {
    const stored = loadRobotOverlayScale();
    scaleRef.current = stored;
    setScale(stored);
  }, []);

  function commitScale(value: number): void {
    const normalized = persistRobotOverlayScale(value);
    scaleRef.current = normalized;
    setScale(normalized);
    window.requestAnimationFrame(() => {
      setPosition((current) => clampPosition(current ?? defaultPosition(), containerRef.current));
    });
  }

  function previewScale(value: number): void {
    scaleRef.current = value;
    setScale(value);
  }

  const waitingLogRef = useRef<string | null>(null);
  const sendingLogRef = useRef<string | null>(null);
  useEffect(() => {
    if (!robotState || robotState.status !== "WAITING_NEXT_CANDLE_ENTRY") return;
    const key = [
      robotState.status,
      robotState.cycle_id ?? "-",
      robotState.pending_signal?.symbol ?? "-",
      robotState.pending_signal?.direction ?? "-",
    ].join("|");
    if (waitingLogRef.current !== key) {
      waitingLogRef.current = key;
      console.log("[WAITING_NEXT_CANDLE_ENTRY_UI]", {
        cycleId: robotState.cycle_id ?? null,
        symbol: robotState.pending_signal?.symbol ?? null,
        direction: robotState.pending_signal?.direction ?? null,
      });
    }
  }, [robotState]);
  useEffect(() => {
    if (!robotState || robotState.status !== "SENDING_ORDER") return;
    const key = [
      robotState.status,
      robotState.cycle_id ?? "-",
      robotState.pending_signal?.symbol ?? "-",
      robotState.pending_signal?.direction ?? "-",
      robotState.last_trade?.order_id ?? "-",
    ].join("|");
    if (sendingLogRef.current !== key) {
      sendingLogRef.current = key;
      console.log("[SENDING_ORDER_UI]", {
        cycleId: robotState.cycle_id ?? null,
        symbol: robotState.pending_signal?.symbol ?? null,
        direction: robotState.pending_signal?.direction ?? null,
        orderId: robotState.last_trade?.order_id ?? null,
      });
    }
  }, [robotState]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      setPosition(clampPosition(defaultPosition(), containerRef.current));
    });
    const onResize = () => {
      setPosition((current) => clampPosition(current ?? defaultPosition(), containerRef.current));
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      event.preventDefault();
      const next = scaleFromWheel(scaleRef.current, event.deltaY);
      previewScale(next);
      if (wheelCommitTimerRef.current != null) window.clearTimeout(wheelCommitTimerRef.current);
      wheelCommitTimerRef.current = window.setTimeout(() => {
        commitScale(scaleRef.current);
        wheelCommitTimerRef.current = null;
      }, 180);
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      element.removeEventListener("wheel", onWheel);
      if (wheelCommitTimerRef.current != null) window.clearTimeout(wheelCommitTimerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [position]);

  function touchDistance(touches: TouchList): number {
    if (touches.length < 2) return 0;
    const first = touches[0];
    const second = touches[1];
    if (!first || !second) return 0;
    return Math.hypot(second.clientX - first.clientX, second.clientY - first.clientY);
  }

  function onTouchStart(event: ReactTouchEvent<HTMLDivElement>): void {
    if (event.touches.length !== 2) return;
    const distance = touchDistance(event.touches);
    if (distance > 0) {
      pinchStateRef.current = { startDistance: distance, startScale: scaleRef.current };
      setDragging(false);
    }
  }

  function onTouchMove(event: ReactTouchEvent<HTMLDivElement>): void {
    const pinch = pinchStateRef.current;
    if (!pinch || event.touches.length !== 2) return;
    event.preventDefault();
    const distance = touchDistance(event.touches);
    previewScale(scaleFromPinch(pinch.startScale, pinch.startDistance, distance));
  }

  function onTouchEnd(event: ReactTouchEvent<HTMLDivElement>): void {
    if (event.touches.length >= 2) return;
    if (pinchStateRef.current) {
      pinchStateRef.current = null;
      commitScale(scaleRef.current);
    }
  }

  function onResizePointerDown(event: ReactPointerEvent<HTMLButtonElement>): void {
    if (event.button !== 0) return;
    event.stopPropagation();
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    resizeStateRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      startScale: scaleRef.current,
    };
    setResizing(true);
    setDragging(false);
  }

  function onResizePointerMove(event: ReactPointerEvent<HTMLButtonElement>): void {
    const state = resizeStateRef.current;
    if (!state || state.pointerId !== event.pointerId) return;
    event.stopPropagation();
    previewScale(scaleFromResizeDrag(state.startScale, event.clientX - state.startX, event.clientY - state.startY));
  }

  function onResizePointerUp(event: ReactPointerEvent<HTMLButtonElement>): void {
    const state = resizeStateRef.current;
    if (!state || state.pointerId !== event.pointerId) return;
    event.stopPropagation();
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    resizeStateRef.current = null;
    setResizing(false);
    commitScale(scaleRef.current);
  }

  function onResizeDoubleClick(event: React.MouseEvent<HTMLButtonElement>): void {
    event.stopPropagation();
    commitScale(ROBOT_OVERLAY_SCALE_DEFAULT);
  }

  function onDragPointerDown(event: ReactPointerEvent<HTMLDivElement>): void {
    if (event.button !== 0 || !position || resizing) return;
    if ((event.target as HTMLElement).closest("button")) return;
    if (pinchStateRef.current) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragOffsetRef.current = { x: event.clientX - position.x, y: event.clientY - position.y };
    setDragging(true);
  }

  function onDragPointerMove(event: ReactPointerEvent<HTMLDivElement>): void {
    if (!dragging || resizing) return;
    setPosition(
      clampPosition(
        { x: event.clientX - dragOffsetRef.current.x, y: event.clientY - dragOffsetRef.current.y },
        containerRef.current,
      ),
    );
  }

  function onDragPointerUp(event: ReactPointerEvent<HTMLDivElement>): void {
    if (!dragging) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setDragging(false);
  }

  return (
    <div
      ref={containerRef}
      style={position ? { left: position.x, top: position.y } : { visibility: "hidden" }}
      className={`fixed ${layerClassName} flex max-w-[calc(100vw-24px)] touch-none select-none flex-col items-center pb-1 ${interactionLocked ? "pointer-events-none" : ""} ${resizing ? "cursor-nwse-resize" : dragging ? "cursor-grabbing" : "cursor-grab"}`}
      onPointerDown={onDragPointerDown}
      onPointerMove={onDragPointerMove}
      onPointerUp={onDragPointerUp}
      onPointerCancel={onDragPointerUp}
      onTouchStart={onTouchStart}
      onTouchMove={onTouchMove}
      onTouchEnd={onTouchEnd}
      onTouchCancel={onTouchEnd}
      aria-label="Robô flutuante. Arraste para mover. Puxe o canto para mudar o tamanho."
    >
      <div className="relative flex flex-col items-center origin-top pt-7" style={{ transform: `scale(${scale})` }}>
        <div
          className="absolute right-0 top-0 z-20 flex items-center gap-1.5"
          onPointerDown={(event) => event.stopPropagation()}
        >
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              togglePrivacyMode();
            }}
            className="flex h-6 w-6 cursor-pointer items-center justify-center rounded-full border border-border bg-card text-foreground transition hover:bg-accent"
            title={
              privacyOn
                ? "Saldo escondido — clique para mostrar"
                : "Clique para esconder o saldo da conta"
            }
            aria-label={privacyToggleLabel(privacyOn)}
            aria-pressed={privacyOn}
          >
            {privacyOn ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
          </button>
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onSilenceNarrator?.();
            }}
            disabled={!narratorEnabled || !onSilenceNarrator}
            className="flex h-6 w-6 cursor-pointer items-center justify-center rounded-full border border-border bg-card text-foreground transition hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
            title={
              narratorEnabled
                ? narratorMuted
                  ? "Áudio silenciado — clique para reativar"
                  : "Narrador ligado"
                : "Narrador desligado"
            }
            aria-label={narratorMuted ? "Reativar narrador" : "Silenciar narrador"}
          >
            {narratorEnabled && !narratorMuted ? (
              <Volume2 className="h-3.5 w-3.5" />
            ) : (
              <VolumeX className="h-3.5 w-3.5" />
            )}
          </button>
          {narratorEnabled && onSilenceNarrator ? (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onSilenceNarrator();
              }}
              className={`cursor-pointer rounded-full border border-border px-2 py-1 text-[10px] font-semibold transition hover:bg-accent ${narratorMuted || narratorSpeaking ? "bg-card text-foreground" : "bg-card/80 text-muted-foreground"}`}
            >
              {narratorMuted ? "Ativar áudio" : "Silenciar"}
            </button>
          ) : null}
          {showConfig ? (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                setDraftSettings(settings);
                setConfigOpen((open) => !open);
              }}
              className="flex cursor-pointer items-center gap-1 rounded-full border border-border bg-primary px-2 py-1 text-[10px] font-semibold text-primary-foreground transition hover:opacity-90"
              aria-label="Abrir configurações do robô"
            >
              <Settings className="h-3 w-3" />
              Config
            </button>
          ) : null}
          {onClose ? (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onClose();
              }}
              className="flex h-6 w-6 cursor-pointer items-center justify-center rounded-full border border-border bg-primary text-primary-foreground transition hover:opacity-90"
              aria-label="Esconder overlay do robô"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          ) : null}
        </div>
        {configOpen ? (
          <RobotConfigMenu
            settings={draftSettings}
            locked={locked}
            accountCurrency={account?.currency}
            onChange={setDraftSettings}
            onClose={() => setConfigOpen(false)}
            onSave={async () => {
              if (locked) {
                toast.error("Robo ativo no momento.");
                return;
              }
              try {
                await Promise.resolve(onSettingsChange?.(draftSettings));
                toast.success("Configurações salvas");
                setConfigOpen(false);
              } catch (error) {
                const message =
                  error instanceof Error ? error.message : "Não foi possível salvar as configurações.";
                toast.error(message);
                console.error("[ROBOT CONFIG SAVE ERROR]", error);
                throw error;
              }
            }}
          />
        ) : null}
        <div className="relative grid grid-cols-[72px_110px_72px] items-center gap-2 sm:grid-cols-[92px_170px_92px] sm:gap-4">
          <ScoreBadge label="WIN" value={scoreWins} tone="win" />
          <div className="relative flex justify-center">
            {showSpeechBubble && speechPreview ? (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  setSpeechDetailOpen(true);
                }}
                onPointerDown={(event) => event.stopPropagation()}
                className="pointer-events-auto absolute -top-2 left-1/2 z-20 w-[170px] -translate-x-1/2 -translate-y-full cursor-pointer rounded-2xl border border-border/80 bg-card/95 px-3 py-2 text-left shadow-[0_8px_20px_rgba(0,0,0,0.35)] transition hover:bg-card sm:w-[210px]"
                aria-label="Abrir explicação da operação do El Capo"
                title="Clique para ver a explicação completa"
              >
                <p className="text-[10px] font-bold uppercase tracking-wide text-primary">El Capo</p>
                <p className="mt-0.5 line-clamp-3 text-[11px] font-medium leading-snug text-foreground sm:text-xs">
                  {speechPreview}
                </p>
                {speechSignal?.strategy_name ? (
                  <p className="mt-1 line-clamp-1 text-[10px] text-muted-foreground">
                    {speechSignal.strategy_name}
                  </p>
                ) : null}
                <span
                  aria-hidden="true"
                  className="absolute bottom-[-6px] left-1/2 h-3 w-3 -translate-x-1/2 rotate-45 border-b border-r border-border/80 bg-card/95"
                />
              </button>
            ) : null}
            <RobotAvatarVideo className="h-auto w-[110px] object-contain sm:w-[170px]" />
          </div>
          <ScoreBadge label="LOSS" value={scoreLosses} tone="loss" />
        </div>
        {speechDetailOpen && speechView ? (
          <div
            className="fixed inset-0 z-[90] flex items-center justify-center bg-black/55 p-4"
            role="dialog"
            aria-modal="true"
            aria-label="Explicação da operação"
            onClick={(event) => {
              event.stopPropagation();
              setSpeechDetailOpen(false);
            }}
            onPointerDown={(event) => event.stopPropagation()}
          >
            <div
              className="max-h-[80vh] w-full max-w-md overflow-y-auto rounded-2xl border border-border bg-card p-5 text-left shadow-xl"
              onClick={(event) => event.stopPropagation()}
            >
              <div className="mb-3 flex items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-primary">El Capo</p>
                  <h3 className="text-base font-semibold text-foreground">
                    {speechView.symbol} · {speechView.direction}
                  </h3>
                </div>
                <button
                  type="button"
                  onClick={() => setSpeechDetailOpen(false)}
                  className="rounded-full border border-border p-1.5 hover:bg-accent"
                  aria-label="Fechar"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
              <p className="text-sm font-semibold text-foreground">
                {speechView.strategyName || "Estratégia selecionada"}
              </p>
              {speechView.preview ? (
                <p className="mt-2 rounded-xl border border-border bg-muted/40 px-3 py-2 text-sm italic text-foreground">
                  “{speechView.preview}”
                </p>
              ) : null}
              <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                {speechView.detail || "Sem detalhe técnico disponível para esta entrada."}
              </p>
            </div>
          </div>
        ) : null}
        {study ? null : <ProfitBadge profit={scoreProfit} currency={account?.currency} />}
        {!adminModelControls && (onStartOperation || onStopOperation || onResetScore) ? (
          <div
            className="z-10 mt-1 flex flex-col items-center gap-1.5"
            onPointerDown={(event) => event.stopPropagation()}
          >
            {operationRunning && onStopOperation ? (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onStopOperation();
                }}
                disabled={stopOperationDisabled}
                className="pointer-events-auto cursor-pointer rounded-full border border-destructive/50 bg-destructive px-4 py-1.5 text-[11px] font-bold tracking-wide text-destructive-foreground shadow-[0_6px_16px_rgba(239,68,68,0.35)] transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 sm:text-xs"
              >
                {stopOperationLabel}
              </button>
            ) : null}
            {!operationRunning && onStartOperation ? (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onStartOperation();
                }}
                disabled={startOperationDisabled}
                className="pointer-events-auto cursor-pointer rounded-full border border-primary/50 bg-success px-4 py-1.5 text-[11px] font-bold tracking-wide text-success-foreground shadow-[0_6px_16px_rgba(34,197,94,0.35)] transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 sm:text-xs"
              >
                {startOperationLabel}
              </button>
            ) : null}
            {onResetScore ? (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onResetScore();
                }}
                disabled={resetScoreDisabled}
                className="pointer-events-auto inline-flex cursor-pointer items-center gap-1 rounded-full border border-border/60 bg-transparent px-2.5 py-0.5 text-[10px] font-medium tracking-wide text-muted-foreground/90 transition hover:border-border hover:bg-card/50 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40 sm:text-[11px]"
                aria-label="Reiniciar placar do robô"
                title="Zerar wins, loss e resultado financeiro"
              >
                <RotateCcw className="h-2.5 w-2.5 opacity-80" />
                {resetScoreLabel}
              </button>
            ) : null}
          </div>
        ) : null}
        <div className="-mt-1 w-[290px] max-w-[92vw] px-3 py-2 text-center text-foreground [text-shadow:0_2px_5px_rgba(0,0,0,0.85)] sm:-mt-2 sm:w-[410px]">
          {account?.connected ? (
            <p className="mb-1 text-[11px] font-semibold sm:text-xs">
              {account.mode ?? "-"}
              {account.balance != null
                ? ` | ${maskMoney(formatBullExBalance(account.balance, account.currency), privacyOn)}`
                : ""}
            </p>
          ) : null}
          <p className={`whitespace-normal break-words text-[13px] font-bold leading-snug sm:text-base ${display.tone}`}>
            {display.title}
          </p>
          {display.details ? (
            <div className="mt-1 text-[11px] font-semibold leading-tight text-primary sm:text-xs">
              {display.details}
            </div>
          ) : null}
          {display.footer && studyWinTrade ? (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                setSpeechDetailOpen(true);
              }}
              onPointerDown={(event) => event.stopPropagation()}
              className="pointer-events-auto mt-1 line-clamp-2 cursor-pointer text-[11px] font-semibold leading-tight underline-offset-2 hover:underline sm:text-xs"
              title="Clique para ver a análise completa"
            >
              {display.footer}
            </button>
          ) : display.footer ? (
            <p className="mt-1 whitespace-nowrap text-[11px] font-semibold leading-tight sm:text-xs">
              {display.footer}
            </p>
          ) : null}
          {adminModelControls ? (
            <div className="mt-3 flex flex-wrap items-center justify-center gap-2">
              <button
                type="button"
                onClick={adminModelControls.onAddWin}
                className="inline-flex items-center gap-1 rounded-full border border-primary/40 bg-primary/20 px-3 py-1.5 text-[10px] font-bold text-primary transition hover:bg-primary/30 sm:text-xs"
              >
                <TrophyIcon />
                WIN +1
              </button>
              <button
                type="button"
                onClick={adminModelControls.onResetScore}
                className="inline-flex items-center gap-1 rounded-full border border-border bg-card/80 px-3 py-1.5 text-[10px] font-bold text-foreground transition hover:bg-accent sm:text-xs"
              >
                <RotateCcw className="h-3 w-3" />
                Resetar placar
              </button>
            </div>
          ) : null}
        </div>
        <button
          type="button"
          className="robot-overlay-resize-handle"
          aria-label="Ajustar tamanho do robô. Arraste para aumentar ou diminuir. Clique duas vezes para tamanho normal."
          title="Arraste para ajustar o tamanho · duplo clique = tamanho normal"
          onPointerDown={onResizePointerDown}
          onPointerMove={onResizePointerMove}
          onPointerUp={onResizePointerUp}
          onPointerCancel={onResizePointerUp}
          onDoubleClick={onResizeDoubleClick}
        >
          <span aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}

function TrophyIcon() {
  // O ícone Trophy da lucide muda entre versões; este path segue o bundle publicado.
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3 w-3" aria-hidden="true">
      <path d="M10 14.66v1.626a2 2 0 0 1-.976 1.696A5 5 0 0 0 7 21.978" />
      <path d="M14 14.66v1.626a2 2 0 0 0 .976 1.696A5 5 0 0 1 17 21.978" />
      <path d="M18 9h1.5a1 1 0 0 0 0-5H18" />
      <path d="M4 22h16" />
      <path d="M6 9a6 6 0 0 0 12 0V3a1 1 0 0 0-1-1H7a1 1 0 0 0-1 1z" />
      <path d="M6 9H4.5a1 1 0 0 1 0-5H6" />
    </svg>
  );
}

function useNowTicker(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const interval = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(interval);
  }, []);
  return now;
}

interface OverlayDisplay {
  title: string;
  tone: string;
  details: ReactNode | null;
  footer: string | null;
}

function countdownText(state?: RobotState | null, now = Date.now()): string {
  if (!state) return "-";
  const seconds = liveDisplayCountdownSeconds(state, now);
  const clock = seconds != null && seconds > 0 ? formatClock(seconds) : null;
  if (state.display_countdown_label) {
    return clock ? `${state.display_countdown_label} ${clock}` : state.display_countdown_label;
  }
  return clock ?? "-";
}

function formatClock(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function buildOverlayDisplay(
  state: RobotState | null | undefined,
  presentation: RobotPresentation,
  now = Date.now(),
  study = false,
): OverlayDisplay {
  const isWin = presentation.result === "WIN";
  const isDraw = presentation.result === "DRAW";
  const tone =
    presentation.kind === "result"
      ? isWin
        ? "text-primary"
        : isDraw
          ? "text-amber-300"
          : "text-muted-foreground"
      : presentation.kind === "operation"
        ? "text-primary"
        : presentation.kind === "rejected"
          ? "text-muted-foreground"
          : "";
  const countdown = countdownText(state, now);
  const detailParts = collectDetailParts(presentation);
  // LIVE: sem contagem (ela entregaria a operação aberta); o rodapé é o
  // texto neutro ou a estratégia do WIN.
  const footer = study
    ? presentation.detail
    : presentation.footer ??
    (countdown !== "-"
      ? formatCountdownFooter(countdown, {
          operationInProgress: Boolean(state?.operation_in_progress),
          resultWaiting: Boolean(state?.result_waiting),
        })
      : null);
  return {
    title: overlayTitle(state, presentation),
    tone,
    details: detailParts.length > 0 ? <span>{detailParts.join(" | ")}</span> : null,
    footer: normalizeFooter(footer),
  };
}

function overlayTitle(state: RobotState | null | undefined, presentation: RobotPresentation): string {
  if (
    presentation.kind === "result" &&
    state?.last_trade?.is_gale &&
    (presentation.result === "LOSS" || state.last_trade.result === "LOSS")
  ) {
    return "LOSS no Gale";
  }
  return presentation.title;
}

function isRobotLocked(state?: RobotState | null): boolean {
  if (!state) return false;
  return !(state.status === "STOPPED" || (state.enabled === false && state.worker_running === false));
}

function normalizeFooter(footer: string | null): string | null {
  if (!footer) return null;
  if (/^Analisando por/i.test(footer)) return footer;
  if (/^Buscando melhor oportunidade/i.test(footer)) return footer;
  return footer.replace(/^Próxima entrada em/i, "Entrada em").replace(/^Proxima entrada em/i, "Entrada em");
}

function collectDetailParts(presentation: RobotPresentation): string[] {
  const asset =
    presentation.trade?.active ?? presentation.signal?.symbol ?? presentation.gale?.active ?? null;
  const direction =
    presentation.trade?.direction ??
    presentation.signal?.direction ??
    presentation.gale?.direction ??
    presentation.direction;
  const parts: string[] = [];
  if (asset) parts.push(asset);
  if (direction) parts.push(directionLabel(direction));
  return parts;
}

function directionLabel(direction: string): string {
  return direction === "CALL" ? "COMPRA" : direction === "PUT" ? "VENDA" : direction;
}

interface RobotConfigMenuProps {
  settings: RobotSettings;
  locked: boolean;
  accountCurrency?: string | null;
  onChange: (settings: RobotSettings) => void;
  onSave: () => Promise<void>;
  onClose: () => void;
}

function RobotConfigMenu({
  settings,
  locked,
  accountCurrency,
  onChange,
  onSave,
  onClose,
}: RobotConfigMenuProps) {
  const [saving, setSaving] = useState(false);

  function updateNumber(field: "stopWin" | "stopLoss" | "entryValue" | "martingaleSteps" | "martingaleMultiplier", raw: string): void {
    if (field === "entryValue") {
      const parsed = parseEntryValueInput(raw, settings.entryValue, accountCurrency);
      if (parsed == null) return;
      onChange({ ...settings, entryValue: parsed });
      return;
    }
    if (field === "stopWin" || field === "stopLoss") {
      const parsed = parseStopMoneyInput(raw, settings[field]);
      if (parsed == null) return;
      onChange({ ...settings, [field]: parsed });
      return;
    }
    const parsed = Number(raw);
    const value =
      field === "martingaleSteps"
        ? Number.isFinite(parsed)
          ? Math.max(1, Math.round(parsed))
          : settings.martingaleSteps
        : Number.isFinite(parsed)
          ? parsed
          : settings[field];
    onChange({ ...settings, [field]: value });
  }

  return (
    <div
      className="absolute right-0 top-2 z-10 w-56 rounded-xl border border-border bg-card p-3 text-foreground"
      onPointerDown={(event) => event.stopPropagation()}
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Config robo</p>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-border px-2 py-1 text-[10px] font-semibold hover:bg-accent"
        >
          Fechar
        </button>
      </div>
      <div className="space-y-2">
        <MoneyInput
          label="Stop Win"
          currency={accountCurrency}
          value={settings.stopWin}
          min={STOP_MONEY_MIN}
          disabled={locked}
          size="compact"
          helperText={`Mínimo ${formatBullExBalance(STOP_MONEY_MIN, accountCurrency)}`}
          onChange={(value) => updateNumber("stopWin", value)}
        />
        <MoneyInput
          label="Stop Loss"
          currency={accountCurrency}
          value={settings.stopLoss}
          min={STOP_MONEY_MIN}
          disabled={locked}
          size="compact"
          helperText={`Mínimo ${formatBullExBalance(STOP_MONEY_MIN, accountCurrency)}`}
          onChange={(value) => updateNumber("stopLoss", value)}
        />
        <MoneyInput
          label="Valor por entrada"
          currency={accountCurrency}
          value={settings.entryValue}
          min={entryLimitsForCurrency(accountCurrency).min}
          step={ENTRY_VALUE_STEP}
          helperText={entryValueHelperText(accountCurrency)}
          disabled={locked}
          size="compact"
          onChange={(value) => updateNumber("entryValue", value)}
        />
        <label className="flex cursor-pointer items-center justify-between gap-3 rounded-lg border border-border bg-background/40 px-3 py-2 text-xs font-semibold">
          <span>Gale ativado</span>
          <input
            type="checkbox"
            checked={settings.martingaleEnabled}
            disabled={locked}
            onChange={(event) => onChange({ ...settings, martingaleEnabled: event.target.checked })}
            className="h-4 w-4 accent-primary disabled:cursor-not-allowed disabled:opacity-50"
          />
        </label>
        <CompactNumberField
          label="Quantidade de Gales"
          value={settings.martingaleSteps}
          step="1"
          disabled={locked}
          onChange={(value) => updateNumber("martingaleSteps", value)}
        />
        <CompactNumberField
          label="Multiplicador do Gale"
          value={settings.martingaleMultiplier}
          step="0.1"
          disabled={locked}
          onChange={(value) => updateNumber("martingaleMultiplier", value)}
        />
      </div>
      <button
        type="button"
        onClick={async () => {
          if (saving || locked) return;
          setSaving(true);
          try {
            await onSave();
          } finally {
            setSaving(false);
          }
        }}
        disabled={saving || locked}
        className="mt-3 w-full rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {saving ? "Salvando..." : "Salvar configurações"}
      </button>
    </div>
  );
}

interface CompactNumberFieldProps {
  label: string;
  value: number;
  step?: number | string;
  min?: number;
  max?: number;
  helperText?: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}

function CompactNumberField({
  label,
  value,
  step = 1,
  min = 0,
  max,
  helperText,
  disabled = false,
  onChange,
}: CompactNumberFieldProps) {
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
        className="w-full rounded-lg border border-border bg-input px-3 py-2 text-xs font-semibold outline-none focus:ring-2 focus:ring-ring/20 disabled:cursor-not-allowed disabled:opacity-50"
      />
      {helperText ? (
        <span className="mt-1 block whitespace-pre-line text-[11px] text-muted-foreground">{helperText}</span>
      ) : null}
    </label>
  );
}

function ScoreBadge({ label, value, tone }: { label: string; value: number; tone: "win" | "loss" }) {
  const toneClass =
    tone === "win"
      ? "border-primary/35 bg-[#041218]/90 text-primary [text-shadow:0_0_8px_#25dbe0,0_2px_4px_#03070a]"
      : "border-border/70 bg-[#0a1216]/90 text-muted-foreground [text-shadow:0_0_8px_#12343b,0_2px_4px_#03070a]";
  return (
    <div
      className={`rounded-xl border px-2 py-1.5 text-center font-black shadow-[0_4px_14px_rgba(0,0,0,0.35)] sm:px-3 sm:py-2 ${toneClass}`}
      aria-label={`${label}: ${value}`}
    >
      <div className="text-[10px] tracking-[0.22em] sm:text-xs">{label}</div>
      <div className="text-3xl leading-none tabular-nums sm:text-5xl">{value}</div>
    </div>
  );
}

function ProfitBadge({ profit, currency }: { profit?: number | null; currency?: string | null }) {
  const tone = profitTone(profit);
  const toneClass =
    tone === "positive"
      ? "text-emerald-400 [text-shadow:0_0_10px_rgba(52,211,153,0.55),0_2px_4px_#03070a]"
      : tone === "negative"
        ? "text-rose-400 [text-shadow:0_0_10px_rgba(251,113,133,0.45),0_2px_4px_#03070a]"
        : "text-foreground/90 [text-shadow:0_2px_4px_#03070a]";
  const formatted = formatProfitAmount(profit, currency);
  return (
    <div
      className="mt-0.5 flex flex-col items-center rounded-xl border border-border/60 bg-[#0a1216]/85 px-3 py-1.5 shadow-[0_4px_14px_rgba(0,0,0,0.3)]"
      aria-label={`Resultado financeiro: ${formatted}`}
    >
      <span className="text-[9px] font-semibold uppercase tracking-[0.28em] text-muted-foreground/90 sm:text-[10px]">
        Resultado
      </span>
      <span className={`text-base font-black tabular-nums leading-none sm:text-xl ${toneClass}`}>{formatted}</span>
    </div>
  );
}

function defaultPosition(): OverlayPosition {
  const width = window.innerWidth < 640 ? 290 : 410;
  const height = window.innerWidth < 640 ? 210 : 270;
  const bounds = viewportBounds();
  return {
    x: Math.max(bounds.minX, bounds.minX + (window.innerWidth - bounds.minX - width) / 2),
    y: Math.max(VIEWPORT_MARGIN, window.innerHeight - height - 32),
  };
}

function clampPosition(position: OverlayPosition, element: HTMLDivElement | null): OverlayPosition {
  const width = element?.offsetWidth ?? (window.innerWidth < 640 ? 290 : 410);
  const height = element?.offsetHeight ?? (window.innerWidth < 640 ? 210 : 270);
  const bounds = viewportBounds();
  return {
    x: Math.min(Math.max(bounds.minX, position.x), Math.max(bounds.minX, window.innerWidth - width - VIEWPORT_MARGIN)),
    y: Math.min(Math.max(bounds.minY, position.y), Math.max(bounds.minY, window.innerHeight - height - VIEWPORT_MARGIN)),
  };
}

function viewportBounds(): { minX: number; minY: number } {
  return window.innerWidth >= 768
    ? { minX: 256 + VIEWPORT_MARGIN, minY: VIEWPORT_MARGIN }
    : { minX: VIEWPORT_MARGIN, minY: 92 };
}
