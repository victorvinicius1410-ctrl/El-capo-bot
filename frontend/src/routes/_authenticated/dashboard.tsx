import { createFileRoute, Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import {
  Bot,
  Coins,
  Gamepad2,
  Loader2,
  Plug,
  Unplug,
  Wallet,
} from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { BullexLogo } from "@/components/BullexLogo";
import { DashboardDateFilter } from "@/components/DashboardDateFilter";
import { useLiveTradingData } from "@/hooks/useLiveTradingData";
import {
  useRobotHistory,
  type RobotHistoryDays,
} from "@/hooks/useRobotHistory";
import { apiConfig } from "@/lib/api";
import {
  formatBullExBalance,
  isBullExConnected,
  isBullExDisconnected,
} from "@/lib/bullexConnection";
import { useBullExLoginState } from "@/lib/bullexLoginState";
import { aggregateDashboardDailyRows } from "@/lib/dashboardDailyStats";
import { useAuth } from "@/lib/useAuth";

export const Route = createFileRoute("/_authenticated/dashboard")({
  head: () => ({ meta: [{ title: "Dashboard - ElCapo AutoBot" }] }),
  component: Dashboard,
});

function Dashboard() {
  const { user } = useAuth();
  const [days, setDays] = useState<RobotHistoryDays>(7);
  const { account, accountStatus, robotState } = useLiveTradingData();
  const history = useRobotHistory(days);
  const loginFlow = useBullExLoginState(user?.id);
  const acc = account.data;
  const syncing = account.isLoading || accountStatus.isLoading || robotState.isLoading;
  const cachedGrace = robotState.data?.connection_status_source === "cached_grace";
  const connectionPending = loginFlow.isPending;
  const connected = isBullExConnected({
    account: acc,
    accountStatus: accountStatus.data,
    cachedGrace,
    pendingConnect: connectionPending,
  });
  const robotConnected = connected && (cachedGrace || robotState.data?.connected !== false);
  const accountStatusLabel = getConnectionStatusLabel({
    syncing,
    connected,
    cachedGrace,
    connectionPending,
  });
  const robotStatus = getConnectionStatusLabel({
    syncing,
    connected: robotConnected,
    cachedGrace,
    connectionPending,
  });
  const isLoading = syncing || connectionPending;
  const hasBackend = !!apiConfig.BASE_URL;
  const disconnected =
    !syncing &&
    !connectionPending &&
    isBullExDisconnected({
      account: acc,
      accountStatus: accountStatus.data,
      cachedGrace,
      pendingConnect: connectionPending,
    });
  const apiError = disconnected ? null : account.error;
  const isNoBackend = apiError instanceof Error && apiError.message.includes("VITE_API_BASE_URL");

  const currency = acc?.currency ?? "-";
  const realBalance = acc?.balance;
  const mode = acc?.mode ?? "-";

  const dailyRows = useMemo(
    () => aggregateDashboardDailyRows(history.data ?? [], currency, mode),
    [history.data, currency, mode],
  );

  const periodResult = dailyRows.reduce((sum, row) => sum + row.result, 0);
  const periodWins = dailyRows.reduce((sum, row) => sum + row.wins, 0);
  const periodLosses = dailyRows.reduce((sum, row) => sum + row.losses, 0);

  return (
    <div className="dash-page space-y-5">
      <header className="dash-header">
        <div className="min-w-0">
          <h1 className="page-title">Dashboard</h1>
          <p className="page-lead">
            Visão diária de resultados, wins e loss do robô.
          </p>
        </div>

        <div className="dash-status-rail" aria-label="Status rápido">
          <StatusChip
            label="Conta"
            value={accountStatusLabel}
            Icon={connected ? Plug : Unplug}
            tone={connected ? "positive" : syncing || connectionPending ? "neutral" : "negative"}
          />
          <StatusChip
            label="Robô"
            value={robotStatus}
            Icon={Bot}
            tone={
              robotConnected ? "positive" : syncing || connectionPending ? "neutral" : "negative"
            }
          />
          <StatusChip
            label="Modo"
            value={isLoading ? "-" : mode}
            Icon={Gamepad2}
            tone={mode === "REAL" ? "warning" : mode === "-" ? "neutral" : "positive"}
          />
        </div>
      </header>

      {!hasBackend && (
        <div className="rounded-xl border border-border bg-card p-4 text-sm text-foreground">
          <strong>Backend nao configurado.</strong> Defina{" "}
          <code className="rounded bg-background/40 px-1 font-mono text-xs">VITE_API_BASE_URL</code>{" "}
          e{" "}
          <code className="rounded bg-background/40 px-1 font-mono text-xs">
            VITE_PANEL_API_KEY
          </code>{" "}
          no ambiente para conectar a API Bullex.
        </div>
      )}

      {hasBackend && connectionPending && (
        <div className="rounded-xl border border-border bg-card p-4 text-sm text-foreground">
          {loginFlow.phase === "reconnecting"
            ? "Reconectando automaticamente..."
            : "Conectando a Bullex..."}
        </div>
      )}

      {hasBackend && !connectionPending && disconnected && (
        <div className="dash-alert">
          <div className="flex min-w-0 items-center gap-3">
            <BullexLogo compact />
            <span>Conta Bullex desconectada.</span>
          </div>
          <Link to="/configuracoes" search={{ secao: "conta" }} className="dash-alert-link page-cta">
            Conectar Bullex
          </Link>
        </div>
      )}

      {hasBackend && apiError && !isNoBackend && (
        <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive-foreground">
          <strong>Erro na API:</strong> {apiError.message}
        </div>
      )}

      <section className="dash-summary">
        <SummaryTile
          label="Saldo"
          value={isLoading ? "-" : formatBullExBalance(realBalance, currency)}
          Icon={Wallet}
          accent
        />
        <SummaryTile label="Moeda" value={currency} Icon={Coins} accent />
        <SummaryTile
          label="Resultado do período"
          value={formatMoney(periodResult)}
          tone={periodResult < 0 ? "negative" : "positive"}
        />
        <SummaryTile label="Wins" value={String(periodWins)} tone="positive" />
        <SummaryTile label="Loss" value={String(periodLosses)} tone="negative" />
      </section>

      <section className="dash-table-panel">
        <div className="dash-table-toolbar">
          <div>
            <h2 className="font-semibold">Resultados por dia</h2>
            <p className="text-xs text-muted-foreground">
              Filtre o período e acompanhe wins, loss e resultado.
            </p>
          </div>

          <DashboardDateFilter
            days={days}
            onChange={setDays}
            isFetching={history.isFetching && !history.isLoading}
          />
        </div>

        {history.error ? (
          <div className="border-t border-destructive/30 bg-destructive/10 px-5 py-4 text-sm text-destructive-foreground">
            <strong>Não foi possível carregar os resultados.</strong>{" "}
            {history.error instanceof Error ? history.error.message : "Erro na API."}
          </div>
        ) : null}

        {history.isLoading ? (
          <div className="flex min-h-48 items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Carregando resultados...
          </div>
        ) : dailyRows.length === 0 ? (
          <div className="flex min-h-48 items-center justify-center px-4 text-center text-sm text-muted-foreground">
            Nenhum resultado no período selecionado.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Resultado</TableHead>
                  <TableHead>Wins</TableHead>
                  <TableHead>Loss</TableHead>
                  <TableHead>Moeda</TableHead>
                  <TableHead>Modo</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {dailyRows.map((row) => (
                  <TableRow key={row.dateKey}>
                    <TableCell>
                      <div className="font-medium">{row.dateLabel}</div>
                      <div
                        className={`text-sm font-semibold ${
                          row.result < 0 ? "text-destructive" : "text-primary"
                        }`}
                      >
                        {formatMoney(row.result)}
                      </div>
                    </TableCell>
                    <TableCell className="font-semibold text-primary">{row.wins}</TableCell>
                    <TableCell className="font-semibold text-destructive">{row.losses}</TableCell>
                    <TableCell>{row.currency}</TableCell>
                    <TableCell>
                      <span
                        className={`dash-mode-pill ${
                          row.mode === "REAL" ? "dash-mode-real" : "dash-mode-demo"
                        }`}
                      >
                        {row.mode}
                      </span>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </section>
    </div>
  );
}

function StatusChip({
  label,
  value,
  Icon,
  tone,
}: {
  label: string;
  value: string;
  Icon: React.ComponentType<{ className?: string }>;
  tone: "positive" | "negative" | "warning" | "neutral";
}) {
  return (
    <div className={`dash-status-chip dash-status-${tone}`}>
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <div className="min-w-0">
        <div className="text-[10px] font-semibold uppercase tracking-[0.14em] opacity-70">
          {label}
        </div>
        <div className="truncate text-xs font-semibold">{value}</div>
      </div>
    </div>
  );
}

function SummaryTile({
  label,
  value,
  Icon,
  accent,
  tone,
}: {
  label: string;
  value: string;
  Icon?: React.ComponentType<{ className?: string }>;
  accent?: boolean;
  tone?: "positive" | "negative";
}) {
  const valueClass =
    tone === "positive"
      ? "text-success"
      : tone === "negative"
        ? "text-destructive"
        : accent
          ? "text-primary"
          : "text-foreground";

  return (
    <div className="dash-summary-tile">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
        {Icon ? <Icon className={`h-3.5 w-3.5 ${valueClass}`} /> : null}
      </div>
      <div className={`text-lg font-semibold ${valueClass}`}>{value}</div>
    </div>
  );
}

function getConnectionStatusLabel({
  syncing,
  connected,
  cachedGrace,
  connectionPending,
}: {
  syncing: boolean;
  connected: boolean;
  cachedGrace: boolean;
  connectionPending: boolean;
}) {
  if (connectionPending) return "Conectando...";
  if (syncing) return "Sincronizando...";
  if (cachedGrace) return "Reconectando...";
  return connected ? "Conectado" : "Desconectado";
}

function formatMoney(value: number) {
  return value.toLocaleString("pt-BR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: "exceptZero",
  });
}
