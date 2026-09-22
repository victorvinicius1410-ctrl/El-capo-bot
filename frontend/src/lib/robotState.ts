/**
 * Normalização do estado do robô retornado pelo backend.
 *
 * O payload pode vir com chaves em snake_case ou camelCase e aninhado em
 * `data`/`state`; aqui tudo é convertido para o formato canônico usado pelo
 * painel, overlay e narrador.
 */

export type RobotDirection = "CALL" | "PUT" | "WAIT";

export interface RobotSignal {
  symbol: string;
  direction: RobotDirection;
  confidence: number | null;
  strategy_score: number | null;
  strategy_name: string | null;
  strategy_key: string | null;
  strategy_summary: string | null;
  analysis_detail: string | null;
  speech_preview: string | null;
  used_strategies: string[];
  strategy_reason: string | null;
  payout: number | null;
  reason: string | null;
  created_at: string | null;
  ai_approved: boolean | null;
  ai_confidence: number | null;
  ai_risk: string | null;
  ai_candle_reading: string | null;
  ai_entry_reason: string | null;
  ai_voice_text: string | null;
  ai_block_reason: string | null;
  ai_error: string | null;
}

export interface RobotTrade {
  active: string;
  direction: RobotDirection;
  amount: number | null;
  order_id: string | null;
  confidence: number | null;
  payout: number | null;
  strategy_score: number | null;
  strategy_name: string | null;
  /** Campos da fala: o Modo Estudo narra o win a partir do `last_trade`. */
  strategy_key: string | null;
  strategy_summary: string | null;
  analysis_detail: string | null;
  speech_preview: string | null;
  used_strategies: string[];
  strategy_reason: string | null;
  entry_reason: string | null;
  result: string;
  expires_at: string | null;
  sent_at: string | null;
  finished_at: string | null;
  profit: number | null;
  gale_step: number | null;
  is_gale: boolean;
  account_mode: "REAL";
}

/**
 * Último resultado publicado pelo backend só para a narração do placar.
 *
 * Vive ~25s (`RESULT_VOICE_TTL_SECONDS` no backend) e **não** altera o
 * `status` do robô, então o narrador anuncia o WIN/LOSS mesmo quando o ciclo
 * já seguiu para a próxima análise.
 */
export interface RobotResultVoice {
  order_id: string | null;
  result: string;
  cycle_result: string | null;
  gale_step: number;
  wins: number;
  losses: number;
  profit: number;
  at: string | null;
}

export interface RobotGaleInfo {
  active: string;
  direction: RobotDirection;
  amount: number | null;
  step: number;
}

export interface RobotState {
  enabled: boolean;
  /**
   * O robô estava ligado e foi parado por um restart do servidor, não pelo
   * cliente. Serve só para o painel oferecer "religar" — o backend mantém a
   * trava de segurança e nada volta a operar sem o clique.
   */
  paused_by_maintenance: boolean;
  worker_running: boolean;
  connected: boolean;
  status: string;
  status_message: string | null;
  cycle_id: string | null;
  allow_real: boolean;
  confirm_real: boolean;
  account_mode: "REAL";
  active_mode: string | null;
  connection_status_source: string | null;
  real_ready: boolean;
  real_block_reason: string | null;
  stop_reason: string | null;
  next_cycle_at: string | null;
  server_time: string | null;
  cycle_minutes: number;
  entry_value: number | null;
  stop_win: number | null;
  stop_loss: number | null;
  stop_win_mode: string | null;
  stop_loss_mode: string | null;
  stop_win_operations: number | null;
  stop_loss_operations: number | null;
  timeframe: string | null;
  market_mode: string | null;
  /**
   * Modo LIVE (cadência de demonstração, só conta de marketing). Vem do
   * servidor para o botão do Shift+O saber se já está ligado — sem isto ele
   * nascia sempre apagado e o clique seguinte remandava `true`.
   */
  live_demo?: boolean;
  /**
   * Modo Estudo (17/09/2026, só marketing). Só vale com `live_demo` ligado —
   * use `isStudyActive` em `studyMode.ts`, nunca este campo sozinho.
   */
  study_mode?: boolean;
  /**
   * Mercado aberto disponível AGORA, segundo o servidor. Ele cruza a janela
   * semanal do forex com o que a corretora responde por ativo — então pega
   * feriado, que o relógio sozinho não pega. `undefined` quando o servidor
   * ainda não respondeu; nesse caso o painel cai no relógio local.
   */
  open_market_available?: boolean;
  /** `forex_closed` (fora da janela) ou `broker_closed` (corretora fechou). */
  open_market_closed_reason?: string | null;
  ai_analysis_enabled: boolean;
  ai_confirmation_required: boolean;
  ai_min_confidence: number | null;
  seconds_until_entry: number;
  martingale_enabled: boolean;
  martingale_multiplier: number;
  martingale_steps: number;
  cycle_result: string | null;
  gale_step: number | null;
  gale_pending: boolean;
  gale_in_progress: boolean;
  gale_active: string | null;
  gale_direction: RobotDirection | null;
  gale_amount: number | null;
  seconds_until_next_cycle: number;
  seconds_until_analysis_window: number;
  seconds_until_entry_window: number;
  display_countdown_label: string | null;
  display_countdown_seconds: number | null;
  expiration_seconds: number;
  expires_at: string | null;
  entry_window_open: boolean;
  entry_target: string | null;
  operation_in_progress: boolean;
  result_waiting: boolean;
  operation_message: string | null;
  result_display_until: string | null;
  /** Resultado fechou com a tela fechada — overlay deve mostrar WIN/LOSS ao voltar. */
  unseen_result: boolean;
  /** Canal dedicado à fala do placar; independe de `status` e do ciclo. */
  result_voice: RobotResultVoice | null;
  pending_signal: RobotSignal | null;
  best_candidate: RobotSignal | null;
  last_signal: RobotSignal | null;
  last_trade: RobotTrade | null;
  wins: number;
  losses: number;
  profit: number;
  /** Timestamp ISO do último Reiniciar placar — evita snapshot Redis atrasado. */
  stop_reset_at: string | null;
  last_order_error: string | null;
  order_fallback_in_progress: boolean;
  order_fallback_attempt: number;
  order_fallback_max_attempts: number;
  rejection_reason: string | null;
  last_rejection_reason: string | null;
  rejected_at: string | null;
  disconnected: boolean;
  fetched_at: number;
}

type Raw = Record<string, unknown>;

function asObject(value: unknown): Raw {
  return value && typeof value === "object" ? (value as Raw) : {};
}

function unwrapPayload(value: unknown): Raw {
  let current = asObject(value);
  for (let depth = 0; depth < 3; depth += 1) {
    if (
      "enabled" in current ||
      "status" in current ||
      "operation_in_progress" in current ||
      "result_waiting" in current
    ) {
      return current;
    }
    const inner = asObject(
      current.data ?? current.state ?? current.robot_state ?? current.robotState ?? current.robot,
    );
    if (Object.keys(inner).length === 0) break;
    current = inner;
  }
  return current;
}

function toText(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function toNullableText(value: unknown): string | null {
  return toText(value) || null;
}

function toMultilineText(value: unknown): string | null {
  if (Array.isArray(value)) {
    const parts = value.map((item) => toText(item)).filter(Boolean);
    return parts.length > 0 ? parts.join("\n") : null;
  }
  return toNullableText(value);
}

function toStringList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((item) => toText(item)).filter(Boolean);
  const text = toText(value);
  return text ? text.split(/[,;|]/).map((item) => item.trim()).filter(Boolean) : [];
}

function toNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value !== "string") return null;
  const parsed = Number(value.replace(",", "."));
  return Number.isFinite(parsed) ? parsed : null;
}

function toPercent(value: unknown): number | null {
  const parsed = toNumber(value);
  if (parsed == null) return null;
  return parsed <= 1 ? parsed * 100 : parsed;
}

function toBool(value: unknown): boolean {
  if (value === true || value === 1) return true;
  if (value === false || value === 0 || typeof value !== "string") return false;
  const text = value.trim().toLowerCase();
  return text === "true" || text === "1";
}

function toTriState(value: unknown): boolean | null {
  if (value == null) return null;
  if (value === true || value === 1) return true;
  if (value === false || value === 0) return false;
  if (typeof value !== "string") return null;
  const text = value.trim().toLowerCase();
  if (["true", "1", "approved"].includes(text)) return true;
  if (["false", "0", "rejected", "blocked"].includes(text)) return false;
  return null;
}

function toConnectedFlag(value: unknown): boolean | null {
  if (value == null) return null;
  if (value === true || value === 1) return true;
  if (value === false || value === 0) return false;
  if (typeof value !== "string") return null;
  const text = value.trim().toLowerCase();
  if (["true", "1", "connected"].includes(text)) return true;
  if (["false", "0", "disconnected"].includes(text)) return false;
  return null;
}

function toDirection(value: unknown): RobotDirection | null {
  const text = toText(value).toUpperCase();
  return text === "CALL" || text === "PUT" || text === "WAIT" ? text : null;
}

function toOrderId(value: unknown): string | null {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return toNullableText(value);
}

function parseTimestamp(value: string | null): number | null {
  if (!value) return null;
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
  const parsed = Date.parse(hasZone ? value : `${value}Z`);
  return Number.isFinite(parsed) ? parsed : null;
}

function secondsBetween(next: string | null, serverTime: string | null): number | null {
  const target = parseTimestamp(next);
  const reference = parseTimestamp(serverTime);
  if (target == null || reference == null) return null;
  return Math.max(0, Math.ceil((target - reference) / 1_000));
}

/**
 * Calcula o countdown exibido no overlay de forma contínua no cliente.
 *
 * O backend só atualiza `display_countdown_seconds` a cada poll (até 5s no
 * ciclo de espera). Sem esta interpolação o timer "trava" e depois salta.
 */
export function liveDisplayCountdownSeconds(state: RobotState, now = Date.now()): number | null {
  const reported = state.display_countdown_seconds;
  if (reported == null) return null;

  const label = (state.display_countdown_label ?? "").toLowerCase();
  // Overlay contínuo: “Buscando melhor oportunidade” não mostra timer.
  if (/buscando melhor oportunidade/.test(label)) {
    return 0;
  }
  const usesNextCycleAt =
    Boolean(state.next_cycle_at) &&
    (/próxima análise|proxima analise|recuperando|novo sinal/i.test(label) ||
      state.status === "SIGNAL_EXPIRED");

  if (usesNextCycleAt) {
    const target = parseTimestamp(state.next_cycle_at);
    if (target != null) return Math.max(0, Math.ceil((target - now) / 1_000));
  }

  const elapsedSeconds = Math.max(0, Math.floor((now - state.fetched_at) / 1_000));
  return Math.max(0, Math.floor(reported) - elapsedSeconds);
}

function normalizeSignal(value: unknown): RobotSignal | null {
  const raw = asObject(value);
  const symbol = toText(raw.symbol ?? raw.active ?? raw.asset);
  const direction = toDirection(raw.signal ?? raw.direction ?? raw.type);
  if (!symbol || !direction) return null;
  return {
    symbol,
    direction,
    confidence: toPercent(raw.confidence ?? raw.probability),
    strategy_score: toNumber(raw.strategy_score ?? raw.strategyScore ?? raw.score),
    strategy_name: toNullableText(raw.strategy_name ?? raw.strategyName ?? raw.strategy),
    strategy_key: toNullableText(raw.strategy_key ?? raw.strategyKey),
    strategy_summary: toMultilineText(raw.strategy_summary ?? raw.strategySummary),
    analysis_detail: toMultilineText(
      raw.analysis_detail ?? raw.analysisDetail ?? raw.entry_reason ?? raw.entryReason,
    ),
    speech_preview: toNullableText(
      raw.speech_preview ?? raw.speechPreview ?? raw.narrator_text ?? raw.narratorText,
    ),
    used_strategies: toStringList(
      raw.used_strategies ?? raw.usedStrategies ?? raw.strategies_used ?? raw.strategiesUsed ??
        raw.strategies ?? raw.strategy_name ?? raw.strategyName ?? raw.strategy,
    ),
    strategy_reason: toMultilineText(raw.strategy_reason ?? raw.strategyReason ?? raw.reason ?? raw.reasons),
    payout: toPercent(raw.payout),
    reason: toMultilineText(raw.reason ?? raw.reasons ?? raw.motive ?? raw.explanation),
    created_at: toNullableText(raw.created_at ?? raw.createdAt ?? raw.timestamp),
    ai_approved: toTriState(
      raw.ai_approved ?? raw.aiApproved ?? raw.openai_approved ?? raw.openaiApproved ??
        raw.ai_confirmation_passed ?? raw.aiConfirmationPassed,
    ),
    ai_confidence: toPercent(
      raw.ai_confidence ?? raw.aiConfidence ?? raw.openai_confidence ?? raw.openaiConfidence,
    ),
    ai_risk: toMultilineText(raw.ai_risk ?? raw.aiRisk ?? raw.openai_risk ?? raw.openaiRisk),
    ai_candle_reading: toMultilineText(
      raw.ai_candle_reading ?? raw.aiCandleReading ?? raw.ai_reading ?? raw.aiReading ??
        raw.openai_candle_reading ?? raw.openaiCandleReading,
    ),
    ai_entry_reason: toMultilineText(
      raw.ai_entry_reason ?? raw.aiEntryReason ?? raw.ai_reason ?? raw.aiReason ??
        raw.openai_entry_reason ?? raw.openaiEntryReason,
    ),
    ai_voice_text: toNullableText(raw.ai_voice_text ?? raw.aiVoiceText ?? raw.openai_voice_text ?? raw.openaiVoiceText),
    ai_block_reason: toMultilineText(
      raw.ai_block_reason ?? raw.aiBlockReason ?? raw.openai_block_reason ?? raw.openaiBlockReason,
    ),
    ai_error: toMultilineText(
      raw.ai_error ?? raw.aiError ?? raw.ai_failure_reason ?? raw.aiFailureReason ??
        raw.openai_error ?? raw.openaiError,
    ),
  };
}

function normalizeTrade(value: unknown): RobotTrade | null {
  const raw = asObject(value);
  const active = toText(raw.active ?? raw.symbol ?? raw.asset);
  const direction = toDirection(raw.direction ?? raw.signal ?? raw.type);
  if (!active || !direction) return null;
  const galeStep = toNumber(raw.gale_step ?? raw.galeStep);
  return {
    active,
    direction,
    amount: toNumber(raw.amount ?? raw.entry_value ?? raw.entryValue),
    order_id: toOrderId(raw.order_id ?? raw.orderId),
    confidence: toPercent(raw.confidence),
    payout: toPercent(raw.payout),
    strategy_score: toNumber(raw.strategy_score ?? raw.strategyScore ?? raw.score),
    strategy_name: toNullableText(raw.strategy_name ?? raw.strategyName ?? raw.strategy),
    strategy_key: toNullableText(raw.strategy_key ?? raw.strategyKey),
    strategy_summary: toMultilineText(raw.strategy_summary ?? raw.strategySummary),
    analysis_detail: toMultilineText(raw.analysis_detail ?? raw.analysisDetail),
    speech_preview: toNullableText(
      raw.speech_preview ?? raw.speechPreview ?? raw.narrator_text ?? raw.narratorText,
    ),
    used_strategies: toStringList(
      raw.used_strategies ?? raw.usedStrategies ?? raw.strategies_used ?? raw.strategiesUsed ??
        raw.strategies ?? raw.strategy_name ?? raw.strategyName ?? raw.strategy,
    ),
    strategy_reason: toMultilineText(raw.strategy_reason ?? raw.strategyReason),
    entry_reason: toMultilineText(raw.entry_reason ?? raw.entryReason ?? raw.reason ?? raw.reasons),
    result: toText(raw.result, "PENDING_RESULT").toUpperCase(),
    expires_at: toNullableText(raw.expires_at ?? raw.expiresAt ?? raw.expiration_at ?? raw.expirationAt),
    sent_at: toNullableText(raw.sent_at ?? raw.sentAt),
    finished_at: toNullableText(raw.finished_at ?? raw.finishedAt),
    profit: toNumber(raw.profit ?? raw.pnl ?? raw.result_amount ?? raw.resultAmount),
    gale_step: galeStep,
    is_gale: toBool(raw.is_gale ?? raw.isGale ?? raw.gale ?? raw.martingale ?? ((galeStep ?? 0) > 0 ? 1 : 0)),
    account_mode: "REAL",
  };
}

/**
 * Normaliza o canal de voz do placar.
 *
 * Retorna `null` quando o payload não traz um resultado final — assim o
 * narrador cai no caminho legado (status/`unseen_result`).
 */
function normalizeResultVoice(value: unknown): RobotResultVoice | null {
  const raw = asObject(value);
  const result = toText(raw.result ?? raw.final_result ?? raw.finalResult).toUpperCase();
  if (!result) return null;
  return {
    order_id: toOrderId(raw.order_id ?? raw.orderId),
    result,
    cycle_result: toNullableText(raw.cycle_result ?? raw.cycleResult)?.toUpperCase() ?? null,
    gale_step: toNumber(raw.gale_step ?? raw.galeStep) ?? 0,
    wins: Math.max(0, toNumber(raw.wins) ?? 0),
    losses: Math.max(0, toNumber(raw.losses) ?? 0),
    profit: toNumber(raw.profit) ?? 0,
    at: toNullableText(raw.at ?? raw.finished_at ?? raw.finishedAt),
  };
}

/** Converte o payload bruto de /robot/state para o formato canônico. */
export function normalizeRobotState(payload: unknown): RobotState {
  const raw = unwrapPayload(payload);
  const config = asObject(raw.config ?? raw.robot_config ?? raw.robotConfig ?? raw.settings);
  const rawTrade =
    raw.current_trade ?? raw.currentTrade ?? raw.pending_trade ?? raw.pendingTrade ??
    raw.operation ?? raw.last_trade ?? raw.lastTrade;
  const trade = asObject(rawTrade);
  const status = toText(raw.status, "STOPPED").toUpperCase();
  const connectionSource = toNullableText(
    raw.connection_status_source ?? raw.connectionStatusSource ?? raw.connection_source ?? raw.connectionSource,
  );
  const cachedGrace = connectionSource === "cached_grace";
  const connectedFlag = toConnectedFlag(raw.connected ?? raw.account_connected ?? raw.accountConnected);
  const disconnected =
    !cachedGrace &&
    (connectedFlag === false ||
      status === "ACCOUNT_DISCONNECTED" ||
      toBool(raw.disconnected ?? raw.account_disconnected ?? raw.accountDisconnected));
  const nextCycleAt = toNullableText(raw.next_cycle_at ?? raw.nextCycleAt);
  const serverTime = toNullableText(raw.server_time ?? raw.serverTime);
  const cycleMinutes = Math.max(1, toNumber(raw.cycle_minutes ?? raw.cycleMinutes) ?? 5);
  const secondsUntilNextCycle = toNumber(raw.seconds_until_next_cycle ?? raw.secondsUntilNextCycle);
  const derivedSecondsUntilNextCycle = secondsBetween(nextCycleAt, serverTime);
  const expiresAt = toNullableText(
    raw.expires_at ?? raw.expiresAt ?? raw.expiration_at ?? raw.expirationAt ??
      trade.expires_at ?? trade.expiresAt ?? trade.expiration_at ?? trade.expirationAt,
  );
  const expirationSeconds =
    toNumber(
      raw.expiration_seconds ?? raw.expirationSeconds ?? raw.seconds_until_expiration ??
        raw.secondsUntilExpiration ?? raw.expires_in ?? raw.expiresIn ??
        trade.expiration_seconds ?? trade.expirationSeconds ?? trade.seconds_until_expiration ??
        trade.secondsUntilExpiration ?? trade.expires_in ?? trade.expiresIn,
    ) ?? 0;
  const fallbackAttempt = Math.max(
    0,
    toNumber(
      raw.order_fallback_attempt ?? raw.orderFallbackAttempt ?? raw.fallback_attempt ??
        raw.fallbackAttempt ?? raw.active_fallback_attempt ?? raw.activeFallbackAttempt,
    ) ?? 0,
  );
  const fallbackMaxAttempts = Math.max(
    1,
    toNumber(
      raw.order_fallback_max_attempts ?? raw.orderFallbackMaxAttempts ?? raw.fallback_max_attempts ??
        raw.fallbackMaxAttempts ?? raw.max_fallback_attempts ?? raw.maxFallbackAttempts,
    ) ?? 3,
  );
  const fallbackInProgress = toBool(
    raw.order_fallback_in_progress ?? raw.orderFallbackInProgress ?? raw.fallback_in_progress ??
      raw.fallbackInProgress ?? raw.active_fallback_in_progress ?? raw.activeFallbackInProgress,
  );
  const galePending = toBool(raw.gale_pending ?? raw.galePending) || status === "WAITING_GALE_ENTRY";
  const galeInProgress =
    toBool(
      raw.gale_in_progress ?? raw.galeInProgress ?? raw.gale_active_flag ?? raw.galeActiveFlag ??
        (typeof raw.gale_active === "boolean" ? raw.gale_active : null),
    ) ||
    status === "SENDING_GALE_ORDER" ||
    status === "PENDING_GALE_RESULT";

  return {
    enabled: toBool(raw.enabled),
    paused_by_maintenance: toBool(raw.paused_by_maintenance ?? raw.pausedByMaintenance),
    worker_running: toBool(raw.worker_running ?? raw.workerRunning ?? raw.running ?? raw.is_running),
    connected: !disconnected,
    status,
    status_message: toNullableText(raw.status_message ?? raw.statusMessage),
    cycle_id: toNullableText(raw.cycle_id ?? raw.cycleId),
    allow_real: true,
    confirm_real: true,
    account_mode: "REAL",
    active_mode: toNullableText(raw.active_mode ?? raw.activeMode) ?? "REAL",
    connection_status_source: connectionSource,
    real_ready: toBool(raw.real_ready ?? raw.realReady),
    real_block_reason: toNullableText(raw.real_block_reason ?? raw.realBlockReason),
    stop_reason: toNullableText(
      raw.stop_reason ?? raw.stopReason ?? raw.stop_status ?? raw.stopStatus ?? raw.stop_type ?? raw.stopType,
    ),
    next_cycle_at: nextCycleAt,
    server_time: serverTime,
    cycle_minutes: cycleMinutes,
    entry_value: toNumber(raw.entry_value ?? raw.entryValue ?? config.entry_value ?? config.entryValue),
    stop_win: toNumber(raw.stop_win ?? raw.stopWin ?? config.stop_win ?? config.stopWin),
    stop_loss: toNumber(raw.stop_loss ?? raw.stopLoss ?? config.stop_loss ?? config.stopLoss),
    stop_win_mode: toNullableText(
      raw.stop_win_mode ?? raw.stopWinMode ?? config.stop_win_mode ?? config.stopWinMode,
    ),
    stop_loss_mode: toNullableText(
      raw.stop_loss_mode ?? raw.stopLossMode ?? config.stop_loss_mode ?? config.stopLossMode,
    ),
    stop_win_operations: toNumber(
      raw.stop_win_operations ??
        raw.stopWinOperations ??
        config.stop_win_operations ??
        config.stopWinOperations,
    ),
    stop_loss_operations: toNumber(
      raw.stop_loss_operations ??
        raw.stopLossOperations ??
        config.stop_loss_operations ??
        config.stopLossOperations,
    ),
    timeframe: toNullableText(raw.timeframe ?? config.timeframe ?? raw.expiration ?? config.expiration),
    market_mode: toNullableText(raw.market_mode ?? raw.marketMode ?? config.market_mode ?? config.marketMode),
    live_demo: toBool(raw.live_demo ?? raw.liveDemo ?? config.live_demo ?? config.liveDemo),
    study_mode: toBool(raw.study_mode ?? raw.studyMode ?? config.study_mode ?? config.studyMode),
    open_market_available:
      raw.open_market_available === undefined && raw.openMarketAvailable === undefined
        ? undefined
        : toBool(raw.open_market_available ?? raw.openMarketAvailable),
    open_market_closed_reason: toNullableText(
      raw.open_market_closed_reason ?? raw.openMarketClosedReason,
    ),
    ai_analysis_enabled: toBool(
      raw.ai_analysis_enabled ?? raw.aiAnalysisEnabled ?? config.ai_analysis_enabled ?? config.aiAnalysisEnabled,
    ),
    ai_confirmation_required: toBool(
      raw.ai_confirmation_required ?? raw.aiConfirmationRequired ??
        config.ai_confirmation_required ?? config.aiConfirmationRequired,
    ),
    ai_min_confidence: toNumber(
      raw.ai_min_confidence ?? raw.aiMinConfidence ?? config.ai_min_confidence ?? config.aiMinConfidence,
    ),
    seconds_until_entry: Math.max(
      0,
      toNumber(
        raw.seconds_until_entry ?? raw.secondsUntilEntry ?? raw.seconds_until_entry_window ??
          raw.secondsUntilEntryWindow,
      ) ?? 0,
    ),
    martingale_enabled: toBool(
      raw.martingale_enabled ?? raw.martingaleEnabled ?? config.martingale_enabled ?? config.martingaleEnabled,
    ),
    martingale_multiplier:
      toNumber(
        raw.martingale_multiplier ?? raw.martingaleMultiplier ??
          config.martingale_multiplier ?? config.martingaleMultiplier,
      ) ?? 2,
    martingale_steps: Math.max(
      1,
      toNumber(
        raw.martingale_steps ?? raw.martingaleSteps ?? config.martingale_steps ?? config.martingaleSteps,
      ) ?? 1,
    ),
    cycle_result: toNullableText(raw.cycle_result ?? raw.cycleResult)?.toUpperCase() ?? null,
    gale_step: toNumber(raw.gale_step ?? raw.galeStep ?? trade.gale_step ?? trade.galeStep),
    gale_pending: galePending,
    gale_in_progress: galeInProgress,
    gale_active: toNullableText(
      (typeof raw.gale_active === "string" ? raw.gale_active : null) ?? raw.galeActive ??
        raw.gale_asset ?? raw.galeAsset ?? trade.gale_active ?? trade.galeActive ??
        trade.active ?? trade.symbol,
    ),
    gale_direction: toDirection(
      raw.gale_direction ?? raw.galeDirection ?? trade.gale_direction ?? trade.galeDirection ?? trade.direction,
    ),
    gale_amount: toNumber(
      raw.gale_amount ?? raw.galeAmount ?? raw.next_gale_amount ?? raw.nextGaleAmount ??
        trade.gale_amount ?? trade.galeAmount ?? trade.amount,
    ),
    seconds_until_next_cycle: Math.max(0, secondsUntilNextCycle ?? derivedSecondsUntilNextCycle ?? 0),
    seconds_until_analysis_window: Math.max(
      0,
      toNumber(raw.seconds_until_analysis_window ?? raw.secondsUntilAnalysisWindow) ?? 0,
    ),
    seconds_until_entry_window: Math.max(
      0,
      toNumber(raw.seconds_until_entry_window ?? raw.secondsUntilEntryWindow) ?? 0,
    ),
    display_countdown_label: toNullableText(raw.display_countdown_label ?? raw.displayCountdownLabel),
    display_countdown_seconds: toNumber(raw.display_countdown_seconds ?? raw.displayCountdownSeconds),
    expiration_seconds: Math.max(0, expirationSeconds),
    expires_at: expiresAt,
    entry_window_open: toBool(raw.entry_window_open ?? raw.entryWindowOpen),
    entry_target: toNullableText(raw.entry_target ?? raw.entryTarget),
    operation_in_progress: toBool(raw.operation_in_progress ?? raw.operationInProgress),
    result_waiting: toBool(raw.result_waiting ?? raw.resultWaiting),
    operation_message: toNullableText(raw.operation_message ?? raw.operationMessage),
    result_display_until: toNullableText(raw.result_display_until ?? raw.resultDisplayUntil),
    unseen_result: Boolean(raw.unseen_result ?? raw.unseenResult),
    result_voice: normalizeResultVoice(raw.result_voice ?? raw.resultVoice),
    pending_signal: normalizeSignal(raw.pending_signal ?? raw.pendingSignal),
    best_candidate: normalizeSignal(raw.best_candidate ?? raw.bestCandidate),
    last_signal: normalizeSignal(raw.last_signal ?? raw.lastSignal),
    last_trade: normalizeTrade(rawTrade),
    wins: Math.max(0, toNumber(raw.wins) ?? 0),
    losses: Math.max(0, toNumber(raw.losses) ?? 0),
    profit: toNumber(raw.profit) ?? 0,
    stop_reset_at: toNullableText(raw.stop_reset_at ?? raw.stopResetAt),
    last_order_error: toNullableText(
      raw.last_order_error ?? raw.lastOrderError ?? raw.order_error ?? raw.orderError,
    ),
    order_fallback_in_progress: fallbackInProgress || fallbackAttempt > 0,
    order_fallback_attempt: fallbackAttempt,
    order_fallback_max_attempts: fallbackMaxAttempts,
    rejection_reason: toNullableText(raw.rejection_reason ?? raw.rejectionReason),
    last_rejection_reason: toNullableText(
      raw.last_rejection_reason ?? raw.lastRejectionReason ?? raw.rejection_reason ?? raw.rejectionReason,
    ),
    rejected_at: toNullableText(raw.rejected_at ?? raw.rejectedAt),
    disconnected,
    fetched_at: Date.now(),
  };
}

/**
 * Diz se o overlay deve mostrar a operação como ligada (botão Parar).
 *
 * Usa só ``enabled``. ``worker_running`` sozinho após o stop era snapshot Redis
 * stale (TTL 600s) e mantinha o botão em "Parar Operação" por vários segundos.
 *
 * @param state - Estado canônico do robô (ou parcial)
 * @returns True quando o cliente pediu operação ligada
 */
export function isRobotOperationRunning(
  state?: Pick<RobotState, "enabled" | "worker_running"> | null,
): boolean {
  return Boolean(state?.enabled);
}

function sessionScoreIsBlank(state: Pick<RobotState, "wins" | "losses" | "profit">): boolean {
  return state.wins === 0 && state.losses === 0 && state.profit === 0;
}

function sessionScoreHasValue(state: Pick<RobotState, "wins" | "losses" | "profit">): boolean {
  return state.wins > 0 || state.losses > 0 || state.profit !== 0;
}

function scoreResetAtMs(value: string | null | undefined): number {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function sessionScoreTotal(state: Pick<RobotState, "wins" | "losses">): number {
  return state.wins + state.losses;
}

/**
 * Janela em que o "Reiniciar placar" do painel manda sobre snapshot atrasado.
 *
 * Mesmo prazo da marca de baixa intencional do backend
 * (`robot:score_authority`, 120s): passado isso, quem tem a palavra final é o
 * servidor — o painel não pode ficar recusando placar para sempre.
 */
export const SESSION_SCORE_RESET_GUARD_MS = 120_000;

function sessionScoreResetIsRecent(value: string | null | undefined, now: number): boolean {
  const resetAt = scoreResetAtMs(value);
  if (resetAt <= 0) return false;
  // Relógio do cliente atrasado deixa `now - resetAt` negativo: ainda é recente.
  return now - resetAt <= SESSION_SCORE_RESET_GUARD_MS;
}

/**
 * Evita o overlay piscar 0-0 (ou “outro placar”) quando start/stop/refetch
 * aplica um snapshot de controle sem o placar da sessão.
 *
 * O gateway em mode=external devolve `enabled` na hora, mas o `auto_trader`
 * local muitas vezes ainda está com wins/losses/profit zerados ou atrasados
 * (ex.: 2x0 com a sessão real em 5x3). O React Query então substituía o
 * placar vivo até o WS do runtime republicar os números.
 *
 * Depois de **Reiniciar placar**, um snapshot Redis/WS atrasado (ainda com
 * 10x12) não pode desfazer o 0-0: compara `stop_reset_at`. Essa trava vale
 * só pela JANELA do reset (`SESSION_SCORE_RESET_GUARD_MS`): sem prazo ela se
 * auto-alimentava — o estado exibido guardava o `stop_reset_at` mais novo, e
 * TODO placar recebido depois (inclusive o WIN novo) era descartado até o F5.
 * É o mesmo defeito que o backend tirou do `reconcile_session_score_on_gateway`
 * em 15/09 (ver docs/PLACAR_DIAGNOSTICO_2026-09-15.md §F1); a cópia do painel
 * ficou para trás.
 *
 * A regra "nunca rebaixa" é o oposto do certo quando a queda foi PEDIDA
 * (exclusão no Shift+O / no Histórico). Quem sabe disso é
 * `resolveSessionScoreGate`; aqui só chegam as duas decisões prontas —
 * manter a função pura e testável isolada do `localStorage`.
 *
 * @param previous - Estado já exibido no painel
 * @param incoming - Snapshot novo (mutação, HTTP ou WS)
 * @param options.allowBlankOverwrite - True só no **Reiniciar placar**
 * @param options.allowScoreDecrease - True quando a queda é intencional
 *   (exclusão) e o placar menor do servidor deve valer, inclusive 0-0
 * @param options.forceScorePreserve - True quando o snapshot recebido é a
 *   réplica atrasada repetindo o placar de antes da exclusão
 * @param options.now - Relógio (injetável no teste) para medir a janela do reset
 * @returns Estado com placar da sessão preservado quando o novo vem em branco
 *   ou claramente atrasado em relação ao exibido
 */
export function preserveRobotSessionScore(
  previous: Pick<RobotState, "wins" | "losses" | "profit" | "stop_reset_at"> | null | undefined,
  incoming: RobotState,
  options?: {
    allowBlankOverwrite?: boolean;
    allowScoreDecrease?: boolean;
    forceScorePreserve?: boolean;
    now?: number;
  },
): RobotState {
  if (options?.allowBlankOverwrite || !previous) return incoming;
  if (options?.forceScorePreserve) {
    return {
      ...incoming,
      wins: previous.wins,
      losses: previous.losses,
      profit: previous.profit,
    };
  }
  if (options?.allowScoreDecrease) return incoming;
  if (sessionScoreHasValue(previous) && sessionScoreIsBlank(incoming)) {
    return {
      ...incoming,
      wins: previous.wins,
      losses: previous.losses,
      profit: previous.profit,
    };
  }
  if (
    sessionScoreIsBlank(previous) &&
    sessionScoreHasValue(incoming) &&
    scoreResetAtMs(previous.stop_reset_at) > scoreResetAtMs(incoming.stop_reset_at) &&
    sessionScoreResetIsRecent(previous.stop_reset_at, options?.now ?? Date.now())
  ) {
    return {
      ...incoming,
      wins: 0,
      losses: 0,
      profit: 0,
      stop_reset_at: previous.stop_reset_at,
    };
  }
  if (
    sessionScoreHasValue(previous) &&
    sessionScoreHasValue(incoming) &&
    sessionScoreTotal(previous) > sessionScoreTotal(incoming)
  ) {
    return {
      ...incoming,
      wins: previous.wins,
      losses: previous.losses,
      profit: previous.profit,
    };
  }
  return incoming;
}

/**
 * Grava o placar do Shift+O no estado já exibido no overlay.
 *
 * O generate/create do painel marketing não passa por POST /robot/*; sem
 * este merge o React Query continua com 0-0 até o Redis/WS alcançarem.
 *
 * @param previous - Estado já cacheado (ou indefinido)
 * @param score - Wins/loss/lucro do lote gerado, da operação avulsa ou a remover
 * @param options.accumulate - True em "Nova operação" (soma no placar atual)
 * @param options.subtract - True na exclusão (subtrai WIN/LOSS/lucro do overlay)
 * @returns Estado com o placar aplicado, demais campos preservados
 */
export function mergeRobotSessionScore(
    previous: RobotState | undefined,
    score: { wins: number; losses: number; profit: number },
    options?: { accumulate?: boolean; subtract?: boolean },
): RobotState {
    const base = previous ?? getStoppedRobotState();
    const wins = Math.max(0, Math.trunc(Number(score.wins) || 0));
    const losses = Math.max(0, Math.trunc(Number(score.losses) || 0));
    const profit = Number.isFinite(Number(score.profit)) ? Number(score.profit) : 0;
    if (options?.subtract) {
        return {
            ...base,
            wins: Math.max(0, base.wins - wins),
            losses: Math.max(0, base.losses - losses),
            profit: Math.round((base.profit - profit) * 100) / 100,
        };
    }
    if (options?.accumulate) {
        return {
            ...base,
            wins: base.wins + wins,
            losses: base.losses + losses,
            profit: Math.round((base.profit + profit) * 100) / 100,
        };
    }
    return { ...base, wins, losses, profit };
}

/** Estado padrão usado quando o robô está parado ou a sessão caiu. */
export function getStoppedRobotState(disconnected = false): RobotState {
  return {
    enabled: false,
    paused_by_maintenance: false,
    worker_running: false,
    connected: !disconnected,
    status: disconnected ? "ACCOUNT_DISCONNECTED" : "STOPPED",
    status_message: null,
    cycle_id: null,
    allow_real: true,
    confirm_real: true,
    account_mode: "REAL",
    active_mode: disconnected ? null : "REAL",
    connection_status_source: disconnected ? "disconnected" : null,
    real_ready: !disconnected,
    real_block_reason: disconnected ? "Conta Bullex desconectada" : null,
    stop_reason: null,
    next_cycle_at: null,
    server_time: null,
    cycle_minutes: 5,
    entry_value: null,
    stop_win: null,
    stop_loss: null,
    stop_win_mode: null,
    stop_loss_mode: null,
    stop_win_operations: null,
    stop_loss_operations: null,
    timeframe: null,
    market_mode: null,
    live_demo: false,
    study_mode: false,
    open_market_available: undefined,
    open_market_closed_reason: null,
    ai_analysis_enabled: false,
    ai_confirmation_required: false,
    ai_min_confidence: null,
    seconds_until_entry: 0,
    martingale_enabled: false,
    martingale_multiplier: 2,
    martingale_steps: 1,
    cycle_result: null,
    gale_step: null,
    gale_pending: false,
    gale_in_progress: false,
    gale_active: null,
    gale_direction: null,
    gale_amount: null,
    seconds_until_analysis_window: 0,
    seconds_until_next_cycle: 0,
    seconds_until_entry_window: 0,
    display_countdown_label: null,
    display_countdown_seconds: null,
    expiration_seconds: 0,
    expires_at: null,
    entry_window_open: false,
    entry_target: null,
    operation_in_progress: false,
    result_waiting: false,
    operation_message: null,
    result_display_until: null,
    unseen_result: false,
    result_voice: null,
    pending_signal: null,
    best_candidate: null,
    last_signal: null,
    last_trade: null,
    wins: 0,
    losses: 0,
    profit: 0,
    stop_reset_at: null,
    last_order_error: null,
    order_fallback_in_progress: false,
    order_fallback_attempt: 0,
    order_fallback_max_attempts: 3,
    rejection_reason: disconnected ? "Conta Bullex desconectada" : null,
    last_rejection_reason: disconnected ? "Conta Bullex desconectada" : null,
    rejected_at: null,
    disconnected,
    fetched_at: Date.now(),
  };
}

/**
 * Poll HTTP de fallback quando o WebSocket está caído.
 * Canal primário = `/ws/robot-state` (ver robotStateWs.ts).
 */
export const ROBOT_STATE_FAST_POLL_MS = 30_000;
/** Poll lento idle (mesmo patamar do fallback WS). */
export const ROBOT_STATE_SLOW_POLL_MS = 30_000;
/**
 * Reconciliação HTTP mesmo com o WS vivo.
 *
 * Pausar o poll por completo deixava o painel dependente de um canal só: se o
 * socket vira zumbi (fica OPEN e para de entregar, sem evento de close), o
 * placar congela no último valor recebido — 0-0, se o usuário entrou logo
 * depois de um reset — e só volta no F5. Um GET a cada 60s custa pouco e
 * garante que o placar do servidor sempre alcança a tela.
 */
export const ROBOT_STATE_WS_RECONCILE_POLL_MS = 60_000;
/** True quando o WS está conectado — o refetchInterval do RQ afrouxa. */
export let robotStateWsLive = false;

/** Marca se o push WS está ativo (pausa poll HTTP). */
export function setRobotStateWsLive(live: boolean): void {
  robotStateWsLive = live;
}

const FAST_POLL_MS = ROBOT_STATE_FAST_POLL_MS;
const SLOW_POLL_MS = ROBOT_STATE_SLOW_POLL_MS;
const BACKOFF_STEPS_MS = [5_000, 10_000, 20_000, 30_000];
const ACTIVE_STATUSES = new Set([
  "ANALYZING", "SIGNAL_FOUND", "WAITING_ENTRY", "BUYING", "ORDER_OPEN", "WAITING_RESULT",
  "WIN", "LOSS", "WAITING_ENTRY_WINDOW", "WAITING_NEXT_CANDLE_ENTRY", "SENDING_ORDER",
  "PENDING_RESULT", "RESULT_RECEIVED", "WAITING_GALE_ENTRY", "SENDING_GALE_ORDER",
  "PENDING_GALE_RESULT", "GALE_RESULT_RECEIVED",
]);

const backoffByUser = new Map<string, { consecutiveFailures: number; backoffUntil: number }>();

function backoffEntry(userId: string) {
  let entry = backoffByUser.get(userId);
  if (!entry) {
    entry = { consecutiveFailures: 0, backoffUntil: 0 };
    backoffByUser.set(userId, entry);
  }
  return entry;
}

/** Intervalo de atualização adequado ao estado atual (com backoff de erros). */
export function robotStateRefetchInterval(
  state: RobotState | undefined,
  userId?: string | null,
  now = Date.now(),
  wsLive = robotStateWsLive,
): number | false {
  if (userId) {
    const entry = backoffEntry(userId);
    if (entry.backoffUntil > now) return entry.backoffUntil - now;
  }
  // Com WS vivo o HTTP vira só reconciliação ocasional — nunca desligado: ver
  // ROBOT_STATE_WS_RECONCILE_POLL_MS.
  if (wsLive) return ROBOT_STATE_WS_RECONCILE_POLL_MS;
  const status = state?.status?.toUpperCase();
  const active =
    state?.operation_in_progress === true ||
    state?.result_waiting === true ||
    state?.last_trade?.result === "PENDING_RESULT" ||
    (status != null && ACTIVE_STATUSES.has(status));
  return active ? FAST_POLL_MS : SLOW_POLL_MS;
}

export function resetRobotStateBackoff(userId?: string | null): void {
  if (!userId) return;
  const entry = backoffEntry(userId);
  entry.consecutiveFailures = 0;
  entry.backoffUntil = 0;
}

export function registerRobotStateFailure(userId?: string | null, now = Date.now()): number {
  if (!userId) return BACKOFF_STEPS_MS[0];
  const entry = backoffEntry(userId);
  entry.consecutiveFailures += 1;
  const wait = BACKOFF_STEPS_MS[Math.min(entry.consecutiveFailures - 1, BACKOFF_STEPS_MS.length - 1)];
  entry.backoffUntil = now + wait;
  return wait;
}
