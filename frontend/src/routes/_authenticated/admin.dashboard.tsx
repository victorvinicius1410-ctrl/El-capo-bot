import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowDownRight,
  ArrowUpRight,
  Clock3,
  Loader2,
  Target,
  TrendingDown,
  TrendingUp,
  UserRoundCheck,
  UserRoundX,
} from "lucide-react";
import { useState } from "react";
import {
  ApiError,
  adminDashboard,
  type AdminDashboardAssetRanking,
  type AdminDashboardUserRanking,
} from "@/lib/api";
import { DashboardDateFilter } from "@/components/DashboardDateFilter";

export const Route = createFileRoute("/_authenticated/admin/dashboard")({
  head: () => ({ meta: [{ title: "Dashboard — Administração ElCapo" }] }),
  component: AdminDashboardPage,
});

function AdminDashboardPage() {
  const [days, setDays] = useState(30);
  const dashboard = useQuery({
    queryKey: ["admin", "dashboard", days],
    queryFn: async () => {
      const response = await adminDashboard(days);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    staleTime: 60_000,
  });

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">Dashboard administrativo</h1>
          <p className="page-lead">
            Visão consolidada dos clientes e das operações reais no período.
          </p>
        </div>
        <DashboardDateFilter days={days} onChange={setDays} isFetching={dashboard.isFetching} />
      </header>

      {dashboard.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Consolidando informações...
        </div>
      ) : dashboard.error ? (
        <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
          {dashboard.error.message}
        </div>
      ) : dashboard.data ? (
        <>
          <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard
              label="Clientes ativos"
              value={String(dashboard.data.active_clients)}
              detail={`${dashboard.data.new_clients} novos clientes no período`}
              Icon={UserRoundCheck}
              tone="positive"
            />
            <MetricCard
              label="Clientes inativos"
              value={String(dashboard.data.inactive_clients)}
              Icon={UserRoundX}
              tone="negative"
            />
            <MetricCard
              label="Teste grátis"
              value={String(dashboard.data.trial_clients)}
              Icon={Clock3}
              tone="neutral"
            />
            <MetricCard
              label="Faturamento"
              value={formatRevenue(dashboard.data.revenue, dashboard.data.revenue_by_currency)}
              detail={`${formatRevenue(
                dashboard.data.new_client_revenue,
                dashboard.data.new_client_revenue_by_currency,
              )} de clientes novos${formatCurrencyBreakdown(dashboard.data.revenue_by_currency)}`}
              Icon={TrendingUp}
              tone="positive"
            />
          </section>

          <section className="grid gap-4 xl:grid-cols-2">
            <UserRanking
              title="Maiores ganhadores"
              items={dashboard.data.top_winners}
              Icon={ArrowUpRight}
              positive
            />
            <UserRanking
              title="Quem mais perdeu"
              items={dashboard.data.top_losers}
              Icon={ArrowDownRight}
            />
            <AssetRanking
              title="Ativos mais assertivos"
              items={dashboard.data.most_accurate_assets}
              Icon={Target}
              positive
            />
            <AssetRanking
              title="Ativos menos assertivos"
              items={dashboard.data.least_accurate_assets}
              Icon={TrendingDown}
            />
          </section>
        </>
      ) : null}
    </div>
  );
}

function MetricCard({
  label,
  value,
  detail,
  Icon,
  tone,
}: {
  label: string;
  value: string;
  detail?: string;
  Icon: React.ComponentType<{ className?: string }>;
  tone: "positive" | "negative" | "neutral";
}) {
  const toneClass =
    tone === "positive"
      ? "text-emerald-400 bg-emerald-500/10 border-emerald-500/30"
      : tone === "negative"
        ? "text-rose-400 bg-rose-500/10 border-rose-500/30"
        : "text-primary bg-primary/10 border-primary/30";
  return (
    <article className="page-surface p-5">
      <div className={`inline-flex rounded-xl border p-2.5 ${toneClass}`}>
        <Icon className="h-5 w-5" />
      </div>
      <p className="mt-4 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 text-3xl font-bold">{value}</p>
      {detail ? <p className="mt-1 text-xs text-muted-foreground">{detail}</p> : null}
    </article>
  );
}

function UserRanking({
  title,
  items,
  Icon,
  positive = false,
}: {
  title: string;
  items: AdminDashboardUserRanking[];
  Icon: React.ComponentType<{ className?: string }>;
  positive?: boolean;
}) {
  return (
    <article className="page-surface p-5">
      <h2 className="flex items-center gap-2 font-semibold">
        <Icon className={`h-5 w-5 ${positive ? "text-emerald-400" : "text-rose-400"}`} />
        {title}
      </h2>
      <div className="mt-4 space-y-2">
        {items.length ? (
          items.map((item, index) => (
            <div
              key={item.user_id}
              className="flex items-center justify-between gap-3 rounded-xl border border-border/70 bg-background/40 p-3"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold">
                  {index + 1}. {item.name}
                </p>
                <p className="truncate text-xs text-muted-foreground">{item.email}</p>
              </div>
              <strong className={positive ? "text-emerald-400" : "text-rose-400"}>
                {formatMoney(item.profit)}
              </strong>
            </div>
          ))
        ) : (
          <EmptyRanking />
        )}
      </div>
    </article>
  );
}

function AssetRanking({
  title,
  items,
  Icon,
  positive = false,
}: {
  title: string;
  items: AdminDashboardAssetRanking[];
  Icon: React.ComponentType<{ className?: string }>;
  positive?: boolean;
}) {
  return (
    <article className="page-surface p-5">
      <h2 className="flex items-center gap-2 font-semibold">
        <Icon className={`h-5 w-5 ${positive ? "text-emerald-400" : "text-rose-400"}`} />
        {title}
      </h2>
      <div className="mt-4 space-y-2">
        {items.length ? (
          items.map((item, index) => (
            <div
              key={item.asset}
              className="flex items-center justify-between gap-3 rounded-xl border border-border/70 bg-background/40 p-3"
            >
              <div>
                <p className="text-sm font-semibold">
                  {index + 1}. {item.asset}
                </p>
                <p className="text-xs text-muted-foreground">
                  {item.wins} WIN · {item.losses} LOSS · {item.operations} operações
                </p>
              </div>
              <strong className={positive ? "text-emerald-400" : "text-rose-400"}>
                {formatPercent(item.accuracy)}
              </strong>
            </div>
          ))
        ) : (
          <EmptyRanking />
        )}
      </div>
    </article>
  );
}

function EmptyRanking() {
  return (
    <p className="rounded-xl border border-dashed border-border p-4 text-sm text-muted-foreground">
      Sem operações reais concluídas neste período.
    </p>
  );
}

function formatMoney(value: number, currency = "BRL") {
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency,
  }).format(value);
}

function formatPercent(value: number) {
  return (
    new Intl.NumberFormat("pt-BR", {
      maximumFractionDigits: 2,
    }).format(value) + "%"
  );
}

function formatCurrencyBreakdown(values: Record<string, number>) {
  const entries = Object.entries(values);
  if (entries.length <= 1) return "";
  return ` · ${entries
    .map(([currency, value]) =>
      new Intl.NumberFormat("pt-BR", { style: "currency", currency }).format(value),
    )
    .join(" · ")}`;
}

function formatRevenue(total: number, values: Record<string, number>) {
  const entries = Object.entries(values);
  if (entries.length === 1) return formatMoney(entries[0][1], entries[0][0]);
  if (entries.length > 1) return "Múltiplas moedas";
  return formatMoney(total);
}
