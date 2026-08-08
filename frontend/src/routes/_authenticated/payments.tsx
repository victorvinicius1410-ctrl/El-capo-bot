import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, ChevronLeft, ChevronRight, ExternalLink, Loader2 } from "lucide-react";
import { useState } from "react";
import {
  ApiError,
  getBillingCheckout,
  listBillingHistory,
  listBillingPlans,
  type BillingHistoryItem,
  type BillingPlan,
} from "@/lib/api";
import {
  isOfficialCaktoCheckoutUrl,
  normalizeListPayload,
  planDiscountPercent,
} from "@/lib/financePresentation";
import { meAccessQueryOptions } from "@/lib/meAccessQuery";

export const Route = createFileRoute("/_authenticated/payments")({
  head: () => ({ meta: [{ title: "Financeiro — ElCapo AutoBot" }] }),
  component: FinanceiroPage,
});

function FinanceiroPage() {
  const [offset, setOffset] = useState(0);
  const access = useQuery(meAccessQueryOptions());
  const pendingApproval = access.data?.access_status === "pending_approval";
  const plans = useQuery({
    queryKey: ["billing", "plans"],
    queryFn: async () => {
      const response = await listBillingPlans();
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return normalizeListPayload<BillingPlan>(response.data)
        .filter((plan) => plan.is_active)
        .sort(
          (left, right) =>
            left.display_order - right.display_order || left.name.localeCompare(right.name),
        );
    },
  });
  const history = useQuery({
    queryKey: ["billing", "history", offset],
    queryFn: async () => {
      const response = await listBillingHistory(offset, 10);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      if (Array.isArray(response.data)) {
        return { items: response.data, hasMore: response.data.length === 10 };
      }
      return {
        items: normalizeListPayload<BillingHistoryItem>(response.data),
        hasMore: response.data.has_more ?? response.data.items.length === 10,
      };
    },
  });
  const checkout = useMutation({
    mutationFn: async (planId: string) => {
      const response = await getBillingCheckout(planId);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      if (!isOfficialCaktoCheckoutUrl(response.data.checkout_url)) {
        throw new Error("URL de checkout inválida.");
      }
      return response.data.checkout_url;
    },
    onSuccess: (checkoutUrl) => {
      window.open(checkoutUrl, "_blank", "noopener,noreferrer");
    },
  });
  const monthlyReference =
    plans.data?.find((plan) => plan.billing_interval_months === 1)?.price ?? 0;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="page-title">Financeiro</h1>
        <p className="page-lead">
          Escolha uma oferta do AutoBot. Ciclos mais longos reduzem o valor mensal efetivo.
        </p>
      </header>

      {pendingApproval ? (
        <section className="rounded-2xl border border-amber-500/35 bg-amber-500/10 p-4 sm:p-5">
          <h2 className="text-base font-semibold text-foreground">Aguardando aprovação</h2>
          <p className="mt-1.5 text-sm text-muted-foreground">
            Seu cadastro está em análise. Um administrador pode liberar o acesso com dias
            definidos, ou você pode comprar um plano abaixo e liberar na hora.
          </p>
        </section>
      ) : null}

      {plans.isLoading ? <LoadingNotice label="Carregando ofertas..." /> : null}
      {plans.error ? <ErrorNotice error={plans.error} /> : null}
      {checkout.error ? <ErrorNotice error={checkout.error} /> : null}
      {!plans.isLoading && !plans.error && !plans.data?.length ? (
        <EmptyNotice label="Nenhuma oferta está disponível no momento." />
      ) : null}

      <div className="grid gap-4 md:grid-cols-3">
        {(plans.data ?? []).map((plan) => {
          const discount = planDiscountPercent(
            plan.price,
            plan.billing_interval_months,
            monthlyReference,
          );
          const monthlyEffective = plan.price / plan.billing_interval_months;

          return (
            <div
              key={plan.id}
              className={`plan-card page-surface flex flex-col p-6 ${
                plan.is_featured ? "plan-card-featured" : ""
              }`}
            >
              <div className="plan-card-top">
                <div>
                  <div className="plan-name">{plan.name}</div>
                  {plan.is_featured && <span className="plan-recommended">Recomendado</span>}
                </div>
                {discount > 0 && (
                  <span className="plan-savings" aria-label={`${discount}% de redução`}>
                    −{discount}%
                  </span>
                )}
              </div>

              <div className="mt-5">
                <div className="plan-price-row">
                  <span className="plan-price">{formatCurrency(plan.price, plan.currency)}</span>
                </div>
                <div className="plan-period">
                  {formatBillingPeriod(plan.billing_interval_months)}
                </div>
              </div>

              {plan.billing_interval_months > 1 ? (
                <p className="plan-meta">
                  Equivale a {formatCurrency(monthlyEffective, plan.currency)}/mês
                  {discount > 0 ? ` · ${discount}% abaixo do mensal` : ""}
                </p>
              ) : (
                <p className="plan-meta">Cobrança recorrente mensal</p>
              )}

              <ul className="mb-6 flex-1 space-y-2.5 text-sm">
                {plan.features.map((feature) => (
                  <li key={feature} className="plan-feature">
                    <Check className="h-3.5 w-3.5 shrink-0" />
                    <span>{feature}</span>
                  </li>
                ))}
              </ul>

              <button
                type="button"
                disabled={checkout.isPending}
                onClick={() => checkout.mutate(plan.id)}
                className={`relative w-full overflow-hidden rounded-lg py-2.5 text-sm font-medium transition ${
                  plan.is_featured ? "page-cta" : "plan-cta-secondary"
                }`}
              >
                {checkout.isPending && checkout.variables === plan.id ? (
                  <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
                ) : (
                  <ExternalLink className="mr-2 inline h-4 w-4" />
                )}
                {plan.is_featured ? "Contratar oferta" : "Selecionar"}
              </button>
            </div>
          );
        })}
      </div>

      <div className="page-surface p-5">
        <h2 className="text-sm font-semibold tracking-wide text-foreground/90">Histórico</h2>
        {history.isLoading ? <LoadingNotice label="Carregando cobranças..." /> : null}
        {history.error ? <ErrorNotice error={history.error} /> : null}
        {!history.isLoading && !history.error && !history.data?.items.length ? (
          <EmptyNotice label="Nenhuma cobrança registrada até o momento." />
        ) : null}
        {history.data?.items.length ? (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="pb-3">Data</th>
                  <th className="pb-3">Oferta</th>
                  <th className="pb-3">Evento</th>
                  <th className="pb-3">Status</th>
                  <th className="pb-3 text-right">Valor</th>
                </tr>
              </thead>
              <tbody>
                {history.data.items.map((item, index) => (
                  <tr
                    key={item.id ?? `${item.occurred_at}-${index}`}
                    className="border-t border-border/60"
                  >
                    <td className="py-3">{formatDateTime(item.occurred_at)}</td>
                    <td className="py-3">{item.plan_name || "—"}</td>
                    <td className="py-3">{humanize(item.event)}</td>
                    <td className="py-3">{humanize(item.status)}</td>
                    <td className="py-3 text-right font-semibold">
                      {formatCurrency(item.amount, item.currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        <div className="mt-4 flex items-center justify-between border-t border-border/60 pt-4">
          <span className="text-xs text-muted-foreground">
            Página {Math.floor(offset / 10) + 1}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={offset === 0 || history.isFetching}
              onClick={() => setOffset((current) => Math.max(0, current - 10))}
              className="inline-flex items-center gap-1 rounded-lg border px-3 py-2 text-xs disabled:opacity-50"
            >
              <ChevronLeft className="h-4 w-4" />
              Anterior
            </button>
            <button
              type="button"
              disabled={!history.data?.hasMore || history.isFetching}
              onClick={() => setOffset((current) => current + 10)}
              className="inline-flex items-center gap-1 rounded-lg border px-3 py-2 text-xs disabled:opacity-50"
            >
              Próxima
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function LoadingNotice({ label }: { label: string }) {
  return (
    <p className="mt-3 flex items-center gap-2 text-sm text-muted-foreground" aria-live="polite">
      <Loader2 className="h-4 w-4 animate-spin" />
      {label}
    </p>
  );
}

function ErrorNotice({ error }: { error: unknown }) {
  return (
    <p
      className="rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive"
      role="alert"
    >
      {error instanceof Error ? error.message : "Não foi possível carregar os dados financeiros."}
    </p>
  );
}

function EmptyNotice({ label }: { label: string }) {
  return <p className="mt-3 text-sm text-muted-foreground">{label}</p>;
}

function formatCurrency(value: number, currency = "BRL") {
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency,
  }).format(value);
}

function formatBillingPeriod(months: number) {
  if (months === 1) return "por mês";
  if (months === 12) return "por ano";
  return `a cada ${months} meses`;
}

function formatDateTime(value: string) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("pt-BR");
}

function humanize(value: string) {
  return value
    ? value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase())
    : "—";
}
