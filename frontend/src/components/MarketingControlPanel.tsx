import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Loader2, Pencil, Plus, Radio, Sparkles, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import {
  ApiError,
  bullexApi,
  marketingDeleteTrade,
  marketingGenerateHistory,
  marketingSimulateTrade,
  marketingSimulationHistory,
  marketingUpdateTrade,
  robotLiveMode,
  type MarketingSimulationTrade,
  type MarketingSimulationTradeUpdate,
} from "@/lib/api";
import {
  MARKETING_ASSET_OPTIONS,
  formatMarketingTradeDateTime,
  localDateTimeInputToIso,
  toLocalDateTimeInputValue,
  type MarketingDemoSettings,
  type MarketingDirectionMode,
  type MarketingResultMode,
  type MarketingTimingMode,
} from "@/lib/marketingDemoSettings";
import {
  MARKETING_HISTORY_QUERY_KEY,
  MARKETING_STATS_QUERY_KEY,
  overlayScoreFromTrades,
} from "@/lib/marketingSimulation";
import {
  MARKETING_PANEL_TABS,
  tabAfterMarketingHistoryMutation,
  type MarketingPanelTab,
} from "@/lib/marketingPanelTabs";
import { applyRobotSessionScoreToCache, ROBOT_STATE_QUERY_KEY } from "@/hooks/useLiveTradingData";
import { ROBOT_HISTORY_QUERY_KEY, ROBOT_STATS_QUERY_KEY } from "@/hooks/useRobotHistory";

interface MarketingControlPanelProps {
  open: boolean;
  onClose: () => void;
  settings: MarketingDemoSettings;
  onSettingsChange: (partial: Partial<MarketingDemoSettings>) => void;
  targetWinRate?: number | null;
  userId?: string | null;
}

type EditForm = {
  result: "WIN" | "LOSS";
  profit: string;
  amount: string;
  asset: string;
  direction: "CALL" | "PUT";
  payout: string;
};

type ScoreDraft = {
  wins: number;
  losses: number;
  amount: number;
  asset: string;
  period: "M1" | "M5" | "M15";
};

/**
 * Painel flutuante oculto da conta marketing (Shift+O).
 * Não usa backdrop bloqueante para a operação continuar em paralelo.
 * Abas Manual / Placar / Histórico: a seta ↓ na aba Histórico abre a
 * lista com lixeira sem precisar rolar os formulários.
 */
export function MarketingControlPanel({
  open,
  onClose,
  settings,
  onSettingsChange,
  targetWinRate,
  userId,
}: MarketingControlPanelProps) {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<MarketingPanelTab>("manual");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<EditForm | null>(null);
  const [draft, setDraft] = useState(settings);
  const [timingMode, setTimingMode] = useState<MarketingTimingMode>("now");
  const [customDateTime, setCustomDateTime] = useState(() => toLocalDateTimeInputValue());
  const [scoreDraft, setScoreDraft] = useState<ScoreDraft>(() => ({
    wins: 8,
    losses: 2,
    amount: settings.amount,
    asset: settings.asset,
    period: "M5",
  }));

  useEffect(() => {
    if (open) {
      setDraft(settings);
      setTimingMode("now");
      setCustomDateTime(toLocalDateTimeInputValue());
      setScoreDraft((current) => ({
        ...current,
        amount: settings.amount,
        asset: settings.asset,
      }));
    } else {
      setActiveTab("manual");
      setEditingId(null);
      setForm(null);
    }
  }, [open, settings]);

  const history = useQuery({
    queryKey: MARKETING_HISTORY_QUERY_KEY,
    enabled: open,
    queryFn: async () => {
      const response = await marketingSimulationHistory();
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data ?? [];
    },
    refetchInterval: open ? 15_000 : false,
  });

  const assetPayoutQuery = useQuery({
    queryKey: ["bullex", "payouts", draft.asset],
    enabled: open && Boolean(draft.asset) && draft.asset !== "ALEATORIO",
    queryFn: async () => {
      const response = await bullexApi.payouts(draft.asset);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      const items = response.data ?? [];
      const match = items.find(
        (item) => String(item.symbol || "").toUpperCase() === draft.asset.toUpperCase(),
      );
      const payout = match?.payout;
      if (payout == null || !Number.isFinite(Number(payout))) {
        throw new Error("Payout indisponível para este ativo");
      }
      return Math.round(Number(payout));
    },
    staleTime: 30_000,
    retry: 1,
  });

  const scoreAssetPayoutQuery = useQuery({
    queryKey: ["bullex", "payouts", "score", scoreDraft.asset],
    enabled: open && Boolean(scoreDraft.asset) && scoreDraft.asset !== "ALEATORIO",
    queryFn: async () => {
      const response = await bullexApi.payouts(scoreDraft.asset);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      const items = response.data ?? [];
      const match = items.find(
        (item) => String(item.symbol || "").toUpperCase() === scoreDraft.asset.toUpperCase(),
      );
      const payout = match?.payout;
      if (payout == null || !Number.isFinite(Number(payout))) {
        throw new Error("Payout indisponível para este ativo");
      }
      return Math.round(Number(payout));
    },
    staleTime: 30_000,
    retry: 1,
  });

  useEffect(() => {
    if (!open || assetPayoutQuery.data == null) return;
    setDraft((current) =>
      current.payout === assetPayoutQuery.data
        ? current
        : { ...current, payout: assetPayoutQuery.data },
    );
  }, [open, assetPayoutQuery.data]);

  const trades = useMemo(
    () => [...(history.data ?? [])].reverse(),
    [history.data],
  );

  const assetSelectOptions = useMemo(() => {
    const selected = draft.asset.trim().toUpperCase();
    if (selected && !MARKETING_ASSET_OPTIONS.includes(selected as (typeof MARKETING_ASSET_OPTIONS)[number])) {
      return [selected, ...MARKETING_ASSET_OPTIONS];
    }
    return [...MARKETING_ASSET_OPTIONS];
  }, [draft.asset]);

  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  async function invalidateAll(): Promise<void> {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: MARKETING_HISTORY_QUERY_KEY }),
      queryClient.invalidateQueries({ queryKey: MARKETING_STATS_QUERY_KEY }),
      queryClient.invalidateQueries({ queryKey: ROBOT_HISTORY_QUERY_KEY }),
      queryClient.invalidateQueries({ queryKey: ROBOT_STATS_QUERY_KEY }),
      queryClient.invalidateQueries({ queryKey: ROBOT_STATE_QUERY_KEY }),
    ]);
  }

  // Modo LIVE: cadência de demonstração. Afrouxa o portão em OTC para o robô
  // entrar com mais frequência durante a transmissão. NÃO melhora o resultado
  // — em OTC o acerto é ~50% medido, então mais entradas é perder mais rápido.
  // O aviso abaixo do botão existe para isso não ser esquecido numa live.
  const [liveOn, setLiveOn] = useState(false);
  // O botão nascia sempre apagado: `useState(false)` e nada consultava o
  // servidor. Fechar e reabrir o Shift+O (ou recarregar a página) mostrava LIVE
  // desligado com o modo ligado no servidor, e o clique seguinte remandava
  // `true` — sem desligar nada. Medido em 07/09 23:44 e 08/09 00:13: dois
  // cliques, dois `enabled=True` no log. O estado do robô é a fonte da verdade.
  useEffect(() => {
    if (!open) return;
    const estadoRobo = queryClient.getQueryData<{ live_demo?: boolean }>([
      ...ROBOT_STATE_QUERY_KEY,
      userId,
    ]);
    if (typeof estadoRobo?.live_demo === "boolean") {
      setLiveOn(estadoRobo.live_demo);
    }
  }, [open, queryClient, userId]);
  const liveMode = useMutation({
    mutationFn: (enabled: boolean) => robotLiveMode(enabled),
    onSuccess: (_data, enabled) => {
      setLiveOn(enabled);
      toast.success(
        enabled
          ? "Modo LIVE ligado — o robô vai pegar muito mais operações em OTC"
          : "Modo LIVE desligado — o robô voltou ao portão normal",
      );
    },
    onError: (error: unknown) => {
      const detalhe = error instanceof ApiError ? error.message : String(error);
      toast.error(
        detalhe.includes("SOMENTE_MARKETING")
          ? "Modo LIVE é exclusivo de conta de marketing"
          : `Não deu para mudar o modo LIVE: ${detalhe}`,
      );
    },
  });

  const createTrade = useMutation({
    mutationFn: async () => {
      let createdAt: string | undefined;
      if (timingMode === "custom") {
        const iso = localDateTimeInputToIso(customDateTime);
        if (!iso) {
          throw new Error("Informe uma data e horário válidos");
        }
        createdAt = iso;
      }
      const response = await marketingSimulateTrade({
        amount: draft.amount,
        payout: draft.payout,
        asset: draft.asset,
        direction: draft.direction === "AUTO" ? undefined : draft.direction,
        result: draft.result === "AUTO" ? undefined : draft.result,
        created_at: createdAt,
      });
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    onSuccess: async (data) => {
      if (userId) {
        applyRobotSessionScoreToCache(queryClient, userId, overlayScoreFromTrades(data ? [data] : []), {
          accumulate: true,
        });
      }
      await invalidateAll();
      const nextTab = tabAfterMarketingHistoryMutation(true);
      if (nextTab) setActiveTab(nextTab);
      toast.success("Operação adicionada");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Falha ao gerar operação");
    },
  });

  const generateHistory = useMutation({
    mutationFn: async () => {
      const response = await marketingGenerateHistory({
        wins: scoreDraft.wins,
        losses: scoreDraft.losses,
        amount: scoreDraft.amount,
        asset: scoreDraft.asset === "ALEATORIO" ? undefined : scoreDraft.asset,
        period: scoreDraft.period,
      });
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    onSuccess: async (data) => {
      if (userId) {
        applyRobotSessionScoreToCache(queryClient, userId, overlayScoreFromTrades(data));
      }
      await invalidateAll();
      const nextTab = tabAfterMarketingHistoryMutation(true);
      if (nextTab) setActiveTab(nextTab);
      toast.success(`Histórico gerado: ${data?.length ?? 0} operações`);
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Falha ao gerar histórico");
    },
  });

  const updateTrade = useMutation({
    mutationFn: async ({ tradeId, payload }: { tradeId: string; payload: MarketingSimulationTradeUpdate }) => {
      const response = await marketingUpdateTrade(tradeId, payload);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    onSuccess: async () => {
      await invalidateAll();
      setEditingId(null);
      setForm(null);
      toast.success("Resultado atualizado");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Falha ao editar operação");
    },
  });

  const deleteTrade = useMutation({
    mutationFn: async (tradeId: string) => {
      const response = await marketingDeleteTrade(tradeId);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return tradeId;
    },
    onSuccess: async (tradeId) => {
      if (userId) {
        const removed = history.data?.find((trade) => trade.id === tradeId);
        if (removed) {
          applyRobotSessionScoreToCache(
            queryClient,
            userId,
            overlayScoreFromTrades([removed]),
            { subtract: true },
          );
        }
      }
      await invalidateAll();
      toast.success("Operação excluída");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Falha ao excluir operação");
    },
  });

  if (!open) return null;

  function startEdit(trade: MarketingSimulationTrade) {
    setEditingId(trade.id);
    setForm({
      result: trade.result === "LOSS" ? "LOSS" : "WIN",
      profit: String(trade.profit ?? 0),
      amount: String(trade.amount ?? 0),
      asset: trade.asset || "EURUSD-OTC",
      direction: trade.direction === "PUT" ? "PUT" : "CALL",
      payout: String(trade.payout ?? 80),
    });
  }

  function submitEdit() {
    if (!editingId || !form) return;
    const profit = Number(form.profit);
    const amount = Number(form.amount);
    const payout = Number(form.payout);
    if (!Number.isFinite(profit) || !Number.isFinite(amount) || amount <= 0) {
      toast.error("Valor e lucro precisam ser numéricos válidos");
      return;
    }
    if (!Number.isFinite(payout) || payout < 0 || payout > 100) {
      toast.error("Payout deve ficar entre 0 e 100");
      return;
    }
    updateTrade.mutate({
      tradeId: editingId,
      payload: {
        result: form.result,
        profit,
        amount,
        asset: form.asset.trim().toUpperCase(),
        direction: form.direction,
        payout: Math.round(payout),
      },
    });
  }

  function applyDemoSettings() {
    const amount = Number(draft.amount);
    const payout = Number(draft.payout);
    if (!Number.isFinite(amount) || amount <= 0) {
      toast.error("Informe um valor de operação válido");
      return;
    }
    if (!Number.isFinite(payout) || payout < 0 || payout > 100) {
      toast.error("Payout automático indisponível — conecte a Bullex");
      return;
    }
    onSettingsChange(draft);
    toast.success("Configurações salvas — próximas operações usam estes valores");
  }

  function submitGenerateHistory() {
    const wins = Number(scoreDraft.wins);
    const losses = Number(scoreDraft.losses);
    const amount = Number(scoreDraft.amount);
    if (!Number.isInteger(wins) || !Number.isInteger(losses) || wins < 0 || losses < 0) {
      toast.error("Wins e loss devem ser números inteiros ≥ 0");
      return;
    }
    if (wins + losses <= 0) {
      toast.error("Informe ao menos uma operação no placar");
      return;
    }
    if (wins + losses > 100) {
      toast.error("Máximo de 100 operações por geração");
      return;
    }
    if (!Number.isFinite(amount) || amount <= 0) {
      toast.error("Valor de entrada deve ser maior que zero");
      return;
    }
    if (
      !window.confirm(
        `Gerar ${wins} WIN e ${losses} LOSS e atualizar o placar do El Capo? As operações entram no histórico sem apagar as antigas.`,
      )
    ) {
      return;
    }
    generateHistory.mutate();
  }

  return (
    <aside
      className="fixed bottom-3 right-3 z-[80] flex h-[min(94vh,920px)] max-h-[calc(100dvh-1.5rem)] w-[min(440px,calc(100vw-1.5rem))] flex-col overflow-hidden rounded-2xl border border-border bg-card/95 shadow-2xl backdrop-blur sm:bottom-4 sm:right-4"
      role="dialog"
      aria-label="Painel de configuração"
      onPointerDown={(event) => event.stopPropagation()}
    >
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div>
          <p className="text-sm font-semibold">Configuração rápida</p>
          <p className="text-xs text-muted-foreground">Shift+O para abrir/fechar · Esc fecha</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg border border-border p-1.5 hover:bg-accent"
          aria-label="Fechar painel"
        >
          <X className="h-4 w-4" />
        </button>
      </header>

      <nav
        className="flex shrink-0 gap-1 border-b border-border bg-muted/30 p-1.5"
        aria-label="Seções do painel marketing"
        role="tablist"
      >
        {MARKETING_PANEL_TABS.map((tab) => {
          const selected = activeTab === tab.id;
          const countLabel =
            tab.id === "history" && trades.length > 0 ? ` (${trades.length})` : "";
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              id={`marketing-panel-tab-${tab.id}`}
              aria-selected={selected}
              aria-controls={`marketing-panel-panel-${tab.id}`}
              onClick={() => setActiveTab(tab.id)}
              className={`inline-flex flex-1 items-center justify-center gap-1 rounded-lg px-2 py-2 text-xs font-semibold transition sm:text-sm ${
                selected
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:bg-background/60 hover:text-foreground"
              }`}
            >
              {tab.id === "history" ? (
                <ChevronDown className="h-3.5 w-3.5 shrink-0" aria-hidden />
              ) : null}
              {tab.label}
              {countLabel}
            </button>
          );
        })}
      </nav>

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        {activeTab === "manual" ? (
        <div
          className="space-y-3 px-4 py-3"
          role="tabpanel"
          id="marketing-panel-panel-manual"
          aria-labelledby="marketing-panel-tab-manual"
        >
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Operação manual (Shift+O)
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              Selecione o ativo, o resultado e o horário (agora ou data/hora).
              O payout é consultado automaticamente na Bullex. Com resultado AUTO,
              a taxa de acertividade define o WIN/LOSS.
            </p>
            {targetWinRate != null ? (
              <p className="mt-1 text-xs text-muted-foreground">
                Taxa de acertividade: {targetWinRate}% (definida no admin)
              </p>
            ) : null}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs">
              Valor
              <input
                type="number"
                min={0.01}
                step={0.01}
                className="mt-1 w-full rounded-md border border-border bg-input px-2 py-1.5 text-sm"
                value={draft.amount}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, amount: Number(event.target.value) }))
                }
              />
            </label>
            <label className="text-xs">
              Payout (%)
              <div className="mt-1 flex h-[38px] items-center rounded-md border border-border bg-muted/40 px-2 text-sm">
                {assetPayoutQuery.isFetching ? (
                  <span className="inline-flex items-center gap-1 text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> consultando…
                  </span>
                ) : assetPayoutQuery.data != null ? (
                  <span className="font-semibold">{assetPayoutQuery.data}%</span>
                ) : (
                  <span className="text-muted-foreground">indisponível</span>
                )}
              </div>
            </label>
            <label className="col-span-2 text-xs">
              Ativo
              <select
                className="mt-1 w-full rounded-md border border-border bg-input px-2 py-1.5 text-sm"
                value={draft.asset}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, asset: event.target.value }))
                }
                aria-label="Selecionar ativo"
              >
                {assetSelectOptions.map((asset) => (
                  <option key={asset} value={asset}>
                    {asset}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs">
              Direção
              <select
                className="mt-1 w-full rounded-md border border-border bg-input px-2 py-1.5 text-sm"
                value={draft.direction}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    direction: event.target.value as MarketingDirectionMode,
                  }))
                }
              >
                <option value="AUTO">AUTO</option>
                <option value="CALL">CALL</option>
                <option value="PUT">PUT</option>
              </select>
            </label>
            <label className="text-xs">
              Resultado
              <select
                className="mt-1 w-full rounded-md border border-border bg-input px-2 py-1.5 text-sm"
                value={draft.result}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    result: event.target.value as MarketingResultMode,
                  }))
                }
                aria-label="Selecionar resultado WIN ou LOSS"
              >
                <option value="AUTO">AUTO (taxa)</option>
                <option value="WIN">WIN</option>
                <option value="LOSS">LOSS</option>
              </select>
            </label>
            <label className="text-xs">
              Horário
              <select
                className="mt-1 w-full rounded-md border border-border bg-input px-2 py-1.5 text-sm"
                value={timingMode}
                onChange={(event) => {
                  const mode = event.target.value as MarketingTimingMode;
                  setTimingMode(mode);
                  if (mode === "custom") {
                    setCustomDateTime(toLocalDateTimeInputValue());
                  }
                }}
                aria-label="Selecionar horário da operação"
              >
                <option value="now">Agora</option>
                <option value="custom">Personalizar data e hora</option>
              </select>
            </label>
            {timingMode === "custom" ? (
              <label className="text-xs">
                Data e hora
                <input
                  type="datetime-local"
                  className="mt-1 w-full rounded-md border border-border bg-input px-2 py-1.5 text-sm"
                  value={customDateTime}
                  onChange={(event) => setCustomDateTime(event.target.value)}
                  aria-label="Definir data e hora da operação"
                />
              </label>
            ) : (
              <p className="flex items-end text-[11px] leading-snug text-muted-foreground">
                Usa o horário em que você clicar em Nova operação.
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={applyDemoSettings}
            className="w-full rounded-lg border border-border px-3 py-2 text-sm font-semibold hover:bg-accent"
          >
            Salvar valores manuais
          </button>
          <button
            type="button"
            disabled={createTrade.isPending || assetPayoutQuery.data == null}
            onClick={() => createTrade.mutate()}
            className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50"
          >
            {createTrade.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            Nova operação
          </button>
          <button
            type="button"
            disabled={liveMode.isPending}
            onClick={() => liveMode.mutate(!liveOn)}
            aria-pressed={liveOn}
            className={
              "inline-flex w-full items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold disabled:opacity-50 " +
              (liveOn
                ? "bg-red-600 text-white hover:bg-red-700"
                : "border border-border text-muted-foreground hover:bg-accent hover:text-foreground")
            }
          >
            {liveMode.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Radio className={"h-4 w-4" + (liveOn ? " animate-pulse" : "")} />
            )}
            {liveOn ? "LIVE ligado — desligar" : "LIVE — mais operações em OTC"}
          </button>
          {liveOn ? (
            <p className="text-[11px] leading-snug text-muted-foreground">
              O robô vai entrar muito mais vezes em OTC. O acerto continua o
              mesmo (~50%), então o placar da live anda mais rápido para os
              dois lados.
            </p>
          ) : null}
          <button
            type="button"
            onClick={() => setActiveTab("history")}
            className="inline-flex w-full items-center justify-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-semibold text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <ChevronDown className="h-4 w-4" />
            Ir para histórico — editar / excluir
          </button>
        </div>
        ) : null}

        {activeTab === "score" ? (
        <div
          className="space-y-3 px-4 py-3"
          role="tabpanel"
          id="marketing-panel-panel-score"
          aria-labelledby="marketing-panel-tab-score"
        >
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Gerar placar automático
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              Informe o placar e o valor de entrada. O payout vem da consulta real
            do ativo na Bullex. Os horários ficam na janela do período (M1/M5/M15).
          </p>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs">
              Wins
              <input
                type="number"
                min={0}
                max={100}
                step={1}
                className="mt-1 w-full rounded-md border border-border bg-input px-2 py-1.5 text-sm"
                value={scoreDraft.wins}
                onChange={(event) =>
                  setScoreDraft((current) => ({ ...current, wins: Number(event.target.value) }))
                }
              />
            </label>
            <label className="text-xs">
              Loss
              <input
                type="number"
                min={0}
                max={100}
                step={1}
                className="mt-1 w-full rounded-md border border-border bg-input px-2 py-1.5 text-sm"
                value={scoreDraft.losses}
                onChange={(event) =>
                  setScoreDraft((current) => ({ ...current, losses: Number(event.target.value) }))
                }
              />
            </label>
            <label className="col-span-2 text-xs">
              Valor de entrada
              <input
                type="number"
                min={0.01}
                step={0.01}
                className="mt-1 w-full rounded-md border border-border bg-input px-2 py-1.5 text-sm"
                value={scoreDraft.amount}
                onChange={(event) =>
                  setScoreDraft((current) => ({
                    ...current,
                    amount: Number(event.target.value),
                  }))
                }
              />
            </label>
            <label className="text-xs">
              Período
              <select
                className="mt-1 w-full rounded-md border border-border bg-input px-2 py-1.5 text-sm"
                value={scoreDraft.period}
                onChange={(event) =>
                  setScoreDraft((current) => ({
                    ...current,
                    period: event.target.value as "M1" | "M5" | "M15",
                  }))
                }
              >
                <option value="M1">M1 (1 min)</option>
                <option value="M5">M5 (5 min)</option>
                <option value="M15">M15 (15 min)</option>
              </select>
            </label>
            <label className="text-xs">
              Payout
              <div className="mt-1 flex h-[38px] items-center rounded-md border border-border bg-muted/40 px-2 text-sm">
                {scoreDraft.asset === "ALEATORIO" ? (
                  <span className="text-muted-foreground">auto por ativo</span>
                ) : scoreAssetPayoutQuery.isFetching ? (
                  <span className="inline-flex items-center gap-1 text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> consultando…
                  </span>
                ) : scoreAssetPayoutQuery.data != null ? (
                  <span className="font-semibold">{scoreAssetPayoutQuery.data}%</span>
                ) : (
                  <span className="text-muted-foreground">indisponível</span>
                )}
              </div>
            </label>
            <label className="col-span-2 text-xs">
              Ativo
              <select
                className="mt-1 w-full rounded-md border border-border bg-input px-2 py-1.5 text-sm"
                value={scoreDraft.asset}
                onChange={(event) =>
                  setScoreDraft((current) => ({ ...current, asset: event.target.value }))
                }
                aria-label="Selecionar ativo do placar"
              >
                <option value="ALEATORIO">Aleatório</option>
                {MARKETING_ASSET_OPTIONS.map((asset) => (
                  <option key={asset} value={asset}>
                    {asset}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <button
            type="button"
            disabled={generateHistory.isPending}
            onClick={submitGenerateHistory}
            className="inline-flex w-full items-center justify-center gap-2 rounded-lg border border-primary/40 bg-primary/10 px-3 py-2 text-sm font-semibold text-primary disabled:opacity-50"
          >
            {generateHistory.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Sparkles className="h-4 w-4" />
            )}
            Gerar histórico do placar
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("history")}
            className="inline-flex w-full items-center justify-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-semibold text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <ChevronDown className="h-4 w-4" />
            Ir para histórico — editar / excluir
          </button>
        </div>
        ) : null}

        {activeTab === "history" ? (
        <div
          className="px-3 py-3"
          role="tabpanel"
          id="marketing-panel-panel-history"
          aria-labelledby="marketing-panel-tab-history"
        >
          <p className="mb-3 px-1 text-xs text-muted-foreground">
            Use a lixeira para excluir operações do histórico com tranquilidade —
            sem rolar pelos formulários de Manual ou Placar.
          </p>
          {history.isLoading ? (
            <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Carregando...
            </div>
          ) : trades.length === 0 ? (
            <p className="px-1 py-8 text-center text-sm text-muted-foreground">
              Nenhuma operação no histórico. Gere pelo placar ou adicione uma manual.
            </p>
          ) : (
            <ul className="space-y-2">
              {trades.map((trade) => {
                const editing = editingId === trade.id && form;
                return (
                  <li key={trade.id} className="rounded-xl border border-border bg-background/50 p-3">
                    {editing ? (
                      <div className="space-y-2">
                        <div className="grid grid-cols-2 gap-2">
                          <label className="text-xs">
                            Resultado
                            <select
                              className="mt-1 w-full rounded-md border border-border bg-input px-2 py-1.5 text-sm"
                              value={form.result}
                              onChange={(event) =>
                                setForm((current) =>
                                  current
                                    ? { ...current, result: event.target.value as "WIN" | "LOSS" }
                                    : current,
                                )
                              }
                            >
                              <option value="WIN">WIN</option>
                              <option value="LOSS">LOSS</option>
                            </select>
                          </label>
                          <label className="text-xs">
                            Direção
                            <select
                              className="mt-1 w-full rounded-md border border-border bg-input px-2 py-1.5 text-sm"
                              value={form.direction}
                              onChange={(event) =>
                                setForm((current) =>
                                  current
                                    ? { ...current, direction: event.target.value as "CALL" | "PUT" }
                                    : current,
                                )
                              }
                            >
                              <option value="CALL">CALL</option>
                              <option value="PUT">PUT</option>
                            </select>
                          </label>
                          <label className="text-xs">
                            Lucro
                            <input
                              className="mt-1 w-full rounded-md border border-border bg-input px-2 py-1.5 text-sm"
                              value={form.profit}
                              onChange={(event) =>
                                setForm((current) =>
                                  current ? { ...current, profit: event.target.value } : current,
                                )
                              }
                            />
                          </label>
                          <label className="text-xs">
                            Valor
                            <input
                              className="mt-1 w-full rounded-md border border-border bg-input px-2 py-1.5 text-sm"
                              value={form.amount}
                              onChange={(event) =>
                                setForm((current) =>
                                  current ? { ...current, amount: event.target.value } : current,
                                )
                              }
                            />
                          </label>
                          <label className="col-span-2 text-xs">
                            Ativo
                            <select
                              className="mt-1 w-full rounded-md border border-border bg-input px-2 py-1.5 text-sm"
                              value={form.asset}
                              onChange={(event) =>
                                setForm((current) =>
                                  current ? { ...current, asset: event.target.value } : current,
                                )
                              }
                            >
                              {!MARKETING_ASSET_OPTIONS.includes(
                                form.asset as (typeof MARKETING_ASSET_OPTIONS)[number],
                              ) ? (
                                <option value={form.asset}>{form.asset}</option>
                              ) : null}
                              {MARKETING_ASSET_OPTIONS.map((asset) => (
                                <option key={asset} value={asset}>
                                  {asset}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label className="col-span-2 text-xs">
                            Payout
                            <input
                              className="mt-1 w-full rounded-md border border-border bg-input px-2 py-1.5 text-sm"
                              value={form.payout}
                              onChange={(event) =>
                                setForm((current) =>
                                  current ? { ...current, payout: event.target.value } : current,
                                )
                              }
                            />
                          </label>
                        </div>
                        <div className="flex gap-2">
                          <button
                            type="button"
                            disabled={updateTrade.isPending}
                            onClick={submitEdit}
                            className="flex-1 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:opacity-50"
                          >
                            Salvar
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setEditingId(null);
                              setForm(null);
                            }}
                            className="rounded-lg border border-border px-3 py-1.5 text-xs font-semibold"
                          >
                            Cancelar
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold">
                            {trade.asset} · {trade.direction} ·{" "}
                            <span className={trade.result === "WIN" ? "text-primary" : "text-destructive"}>
                              {trade.result}
                            </span>
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {formatMoney(Number(trade.profit) || 0)} · valor {formatMoney(Number(trade.amount) || 0)}
                            {trade.payout != null ? ` · payout ${trade.payout}%` : ""}
                          </p>
                          {trade.created_at ? (
                            <p className="text-[11px] text-muted-foreground">
                              {formatMarketingTradeDateTime(trade.created_at)}
                            </p>
                          ) : null}
                        </div>
                        <div className="flex shrink-0 gap-1">
                          <button
                            type="button"
                            onClick={() => startEdit(trade)}
                            className="rounded-md border border-border p-1.5 hover:bg-accent"
                            aria-label="Editar resultado"
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </button>
                          <button
                            type="button"
                            disabled={deleteTrade.isPending}
                            onClick={() => {
                              if (window.confirm("Excluir esta operação do histórico?")) {
                                deleteTrade.mutate(trade.id);
                              }
                            }}
                            className="rounded-md border border-border p-1.5 hover:bg-accent"
                            aria-label="Excluir operação"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        ) : null}
      </div>
    </aside>
  );
}

function formatMoney(value: number): string {
  return value.toLocaleString("pt-BR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: "exceptZero",
  });
}
