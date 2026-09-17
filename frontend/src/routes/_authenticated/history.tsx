import { createFileRoute } from "@tanstack/react-router";
import { useState, type ComponentType } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  BarChart3,
  CircleDollarSign,
  ListChecks,
  Loader2,
  ShieldCheck,
  ShieldX,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { DashboardDateFilter } from "@/components/DashboardDateFilter";
import {
  ROBOT_HISTORY_QUERY_KEY,
  ROBOT_STATS_QUERY_KEY,
  useRobotHistory,
  type RobotHistoryDays,
  type RobotHistoryItem,
} from "@/hooks/useRobotHistory";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  ApiError,
  marketingDeleteTrade,
} from "@/lib/api";
import {
  applyRobotSessionScoreToCache,
  ROBOT_STATE_QUERY_KEY,
  useLiveTradingData,
} from "@/hooks/useLiveTradingData";
import { meAccessQueryOptions } from "@/lib/meAccessQuery";
import { useMarketingPanel } from "@/lib/marketingPanelContext";
import {
  isMarketingSimulationAccount,
  MARKETING_HISTORY_QUERY_KEY,
  MARKETING_STATS_QUERY_KEY,
  overlayScoreFromTrades,
} from "@/lib/marketingSimulation";
import { useAuth } from "@/lib/useAuth";
import { computeRobotStatsFromItems, filterHistoryByRange } from "@/lib/dashboardDailyStats";
import { formatBrasiliaDateTime } from "@/lib/brasiliaTime";
import { rangeFromPreset, type DateRangeValue } from "@/lib/dateRange";
import { filterStudyHistory, isStudyActive, studyHiddenNotice } from "@/lib/studyMode";

export const Route = createFileRoute("/_authenticated/history")({
  head: () => ({ meta: [{ title: "Histórico - ElCapo AutoBot" }] }),
  component: HistoryPage,
});

function HistoryPage() {
  const [days, setDays] = useState<RobotHistoryDays>(1);
  const [range, setRange] = useState<DateRangeValue>(() =>
    rangeFromPreset("today", new Date(), 90),
  );
  const [analysisItem, setAnalysisItem] = useState<RobotHistoryItem | null>(null);
  const history = useRobotHistory(days);
  const { robotState } = useLiveTradingData();
  // Modo Estudo: esconde os losses do estudo enquanto ele estiver ligado, e o
  // aviso abaixo diz quantos. Desligou, a lista volta inteira.
  const study = filterStudyHistory(
    filterHistoryByRange(history.data ?? [], range.start, range.end),
    isStudyActive(robotState.data),
  );
  const items = study.items;
  const studyNotice = studyHiddenNotice(study.hidden);
  const stats = computeRobotStatsFromItems(items);
  const error = history.error;
  const isRefreshing = history.isFetching;
  const { open: marketingPanelOpen } = useMarketingPanel();
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const access = useQuery({
    ...meAccessQueryOptions(),
    enabled: Boolean(user?.id),
  });
  const canDeleteMarketing =
    marketingPanelOpen && isMarketingSimulationAccount(access.data);

  const deleteTrade = useMutation({
    mutationFn: async (payload: { tradeId: string; result?: string; profit?: number }) => {
      const response = await marketingDeleteTrade(payload.tradeId);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return payload;
    },
    onSuccess: async (payload) => {
      if (user?.id) {
        applyRobotSessionScoreToCache(
          queryClient,
          user.id,
          overlayScoreFromTrades([{ result: payload.result, profit: payload.profit }]),
          { subtract: true },
        );
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ROBOT_HISTORY_QUERY_KEY }),
        queryClient.invalidateQueries({ queryKey: ROBOT_STATS_QUERY_KEY }),
        queryClient.invalidateQueries({ queryKey: ROBOT_STATE_QUERY_KEY }),
        queryClient.invalidateQueries({ queryKey: MARKETING_HISTORY_QUERY_KEY }),
        queryClient.invalidateQueries({ queryKey: MARKETING_STATS_QUERY_KEY }),
      ]);
      toast.success("Operação excluída");
    },
    onError: (mutationError) => {
      toast.error(mutationError instanceof Error ? mutationError.message : "Falha ao excluir");
    },
  });

  return (
    <div className="min-w-0 space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="page-title">Histórico</h1>
          <p className="page-lead">
            Operações finalizadas e desempenho do robô.
            {canDeleteMarketing
              ? " Shift+O ativo: você pode excluir operações abaixo."
              : ""}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <DashboardDateFilter
            days={days}
            value={range}
            maxDays={90}
            onChange={(nextDays, nextRange) => {
              setDays(nextDays);
              setRange(nextRange);
            }}
            isFetching={isRefreshing}
          />
        </div>
      </header>

      {error ? (
        <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive-foreground">
          <strong>Não foi possível carregar o histórico.</strong>{" "}
          {error instanceof Error ? error.message : "Erro na API."}
        </div>
      ) : null}

      {studyNotice ? (
        <div
          className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-sm font-semibold text-amber-300"
          role="status"
        >
          {studyNotice}. Os números abaixo não incluem essas operações; todas continuam gravadas e voltam
          quando o estudo for desligado.
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <StatCard label="Win Rate" value={`${formatDecimal(stats.winRate)}%`} Icon={Activity} />
        <StatCard label="Wins" value={String(stats.wins)} Icon={ShieldCheck} tone="positive" />
        <StatCard label="Loss" value={String(stats.losses)} Icon={ShieldX} tone="negative" />
        <StatCard label="Total Trades" value={String(stats.totalTrades)} Icon={ListChecks} />
        <StatCard
          label="Lucro Total"
          value={formatMoney(stats.profit)}
          Icon={CircleDollarSign}
          tone={stats.profit < 0 ? "negative" : "positive"}
        />
        <StatCard
          label="Profit Factor"
          value={stats.profitFactor == null ? "-" : formatDecimal(stats.profitFactor)}
          Icon={BarChart3}
        />
      </div>

      <section className="page-surface overflow-hidden">
        <div className="border-b border-border px-5 py-4">
          <h2 className="font-semibold">Operações</h2>
          <p className="text-xs text-muted-foreground">
            Atualização automática a cada 30 segundos. Clique em “Ver análise” para o detalhe da
            estratégia usada.
          </p>
        </div>

        {history.isLoading ? (
          <div className="flex min-h-48 items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Carregando operações...
          </div>
        ) : items.length === 0 ? (
          <div className="flex min-h-48 items-center justify-center px-4 text-center text-sm text-muted-foreground">
            Nenhuma operação registrada ainda.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <Table className={canDeleteMarketing ? "min-w-[1180px]" : "min-w-[1100px]"}>
            <TableHeader>
              <TableRow>
                <TableHead>Data</TableHead>
                <TableHead>Ativo</TableHead>
                <TableHead>Direção</TableHead>
                <TableHead>Estratégia</TableHead>
                <TableHead>Análise</TableHead>
                <TableHead>Valor</TableHead>
                <TableHead>Resultado</TableHead>
                <TableHead>Lucro/Prejuizo</TableHead>
                <TableHead>Gale</TableHead>
                <TableHead>Gale Step</TableHead>
                <TableHead>Conta</TableHead>
                {canDeleteMarketing ? <TableHead className="w-16">Ações</TableHead> : null}
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => (
                <HistoryRow
                  key={item.id}
                  item={item}
                  canDelete={canDeleteMarketing}
                  deleting={deleteTrade.isPending}
                  onOpenAnalysis={() => setAnalysisItem(item)}
                  onDelete={() => {
                    const tradeId = item.orderId || item.id;
                    if (!tradeId) {
                      toast.error("Operação sem identificador");
                      return;
                    }
                    if (window.confirm("Excluir esta operação do histórico?")) {
                      deleteTrade.mutate({
                        tradeId,
                        result: item.result,
                        profit: item.profit,
                      });
                    }
                  }}
                />
              ))}
            </TableBody>
            </Table>
          </div>
        )}
      </section>

      {analysisItem ? (
        <AnalysisDetailModal item={analysisItem} onClose={() => setAnalysisItem(null)} />
      ) : null}
    </div>
  );
}

function StatCard({
  label,
  value,
  Icon,
  tone,
}: {
  label: string;
  value: string;
  Icon: ComponentType<{ className?: string }>;
  tone?: "positive" | "negative";
}) {
  const toneClass =
    tone === "positive"
      ? "text-primary"
      : tone === "negative"
        ? "text-muted-foreground"
        : "text-foreground";

  return (
    <div className="page-surface p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
        <Icon className={`h-4 w-4 ${toneClass}`} />
      </div>
      <p className={`break-words text-xl font-semibold sm:text-2xl ${toneClass}`}>{value}</p>
    </div>
  );
}

function HistoryRow({
  item,
  canDelete,
  deleting,
  onDelete,
  onOpenAnalysis,
}: {
  item: RobotHistoryItem;
  canDelete: boolean;
  deleting: boolean;
  onDelete: () => void;
  onOpenAnalysis: () => void;
}) {
  const isWin = item.result === "WIN";
  // Empate tem rotulo proprio: pintado como os demais nao-WIN ele parecia
  // derrota, e o cliente cobrava uma perda que nao existiu.
  const isDraw = item.result === "DRAW";
  const resultClass = isWin
    ? "bg-primary/15 text-primary"
    : isDraw
      ? "bg-muted text-foreground"
      : "bg-muted text-muted-foreground";
  const directionClass = item.direction === "CALL" ? "text-primary" : "text-muted-foreground";
  const galeClass =
    item.badge === "NORMAL"
      ? "bg-muted text-muted-foreground"
    : item.badge === "GALE WIN"
        ? "bg-primary/15 text-primary"
        : item.badge === "GALE LOSS"
          ? "bg-muted text-muted-foreground"
          : "bg-primary/10 text-primary";
  const accountLabel = item.accountMode ?? "-";
  const accountClass =
    item.accountMode === "REAL"
      ? "bg-primary/15 text-primary"
      : "bg-muted text-muted-foreground";
  const strategyLabel = item.strategyName || item.strategySummary || "-";
  const hasAnalysis = Boolean(item.analysisDetail || item.strategySummary || item.speechPreview);

  return (
    <TableRow>
      <TableCell className="whitespace-nowrap">
        {formatDate(item.finishedAt ?? item.createdAt)}
      </TableCell>
      <TableCell className="font-medium">{item.active}</TableCell>
      <TableCell className={`font-semibold ${directionClass}`}>{item.direction}</TableCell>
      <TableCell className="max-w-[220px]">
        <p className="truncate text-sm font-medium" title={strategyLabel}>
          {strategyLabel}
        </p>
        {item.strategyKey ? (
          <p className="truncate text-[10px] uppercase tracking-wide text-muted-foreground">
            {item.strategyKey}
          </p>
        ) : null}
      </TableCell>
      <TableCell>
        {hasAnalysis ? (
          <button
            type="button"
            onClick={onOpenAnalysis}
            className="rounded-md border border-border px-2.5 py-1 text-xs font-semibold transition hover:bg-accent"
          >
            Ver análise
          </button>
        ) : (
          <span className="text-xs text-muted-foreground">-</span>
        )}
      </TableCell>
      <TableCell>{formatMoney(item.amount)}</TableCell>
      <TableCell>
        <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold ${resultClass}`}>
          {isDraw ? "EMPATE" : item.result}
        </span>
      </TableCell>
      <TableCell className={`font-semibold ${isWin ? "text-primary" : "text-muted-foreground"}`}>
        {formatMoney(item.profit)}
      </TableCell>
      <TableCell>
        <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold ${galeClass}`}>
          {item.isGale ? "Sim" : "Nao"}
        </span>
      </TableCell>
      <TableCell>{item.galeStep ?? "-"}</TableCell>
      <TableCell>
        <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold ${accountClass}`}>
          {accountLabel}
        </span>
      </TableCell>
      {canDelete ? (
        <TableCell>
          <button
            type="button"
            disabled={deleting}
            onClick={onDelete}
            className="rounded-md border border-border p-1.5 hover:bg-accent disabled:opacity-50"
            aria-label="Excluir operação"
            title="Excluir operação"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </TableCell>
      ) : null}
    </TableRow>
  );
}

function AnalysisDetailModal({
  item,
  onClose,
}: {
  item: RobotHistoryItem;
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/55 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Detalhe da análise"
      onClick={onClose}
    >
      <div
        className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-border bg-card p-5 shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Análise da operação
            </p>
            <h3 className="text-lg font-semibold text-foreground">
              {item.active} · {item.direction}
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-border p-1.5 hover:bg-accent"
            aria-label="Fechar"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <dl className="space-y-3 text-sm">
          <div>
            <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Estratégia
            </dt>
            <dd className="mt-1 font-medium text-foreground">
              {item.strategyName || item.strategySummary || "Não registrada"}
            </dd>
            {item.strategyKey ? (
              <dd className="mt-0.5 text-xs text-muted-foreground">{item.strategyKey}</dd>
            ) : null}
          </div>
          {item.strategySummary ? (
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Resumo
              </dt>
              <dd className="mt-1 text-foreground">{item.strategySummary}</dd>
            </div>
          ) : null}
          {item.speechPreview ? (
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Falou o El Capo
              </dt>
              <dd className="mt-1 rounded-xl border border-border bg-muted/40 px-3 py-2 italic text-foreground">
                “{item.speechPreview}”
              </dd>
            </div>
          ) : null}
          <div>
            <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Detalhe da análise
            </dt>
            <dd className="mt-1 whitespace-pre-wrap leading-relaxed text-foreground">
              {item.analysisDetail || "Sem detalhe técnico salvo nesta operação."}
            </dd>
          </div>
        </dl>
      </div>
    </div>
  );
}

function formatDate(value: string | null) {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "-";
  return formatBrasiliaDateTime(parsed);
}

function formatMoney(value: number) {
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
  }).format(value);
}

function formatDecimal(value: number) {
  return new Intl.NumberFormat("pt-BR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(value);
}
