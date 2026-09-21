import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BadgeDollarSign,
  Check,
  Clipboard,
  Link2,
  ListChecks,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trash2,
  Wallet,
} from "lucide-react";
import { useEffect, useState } from "react";
import { DashboardDateFilter } from "@/components/DashboardDateFilter";
import { formatBrasiliaDate, formatBrasiliaDateTime } from "@/lib/brasiliaTime";
import { rangeFromPreset, type DateRangeValue } from "@/lib/dateRange";
import {
  ADMIN_FIELD_CLASS,
  AdminFormActions,
  AdminFormDialog,
  AdminFormSection,
} from "@/components/AdminFormDialog";
import { adminDialogContent } from "@/lib/adminPresentation";
import {
  ApiError,
  adminCreateFinancePlan,
  adminDeleteFinancePlan,
  adminFinanceMetrics,
  adminFinanceSettings,
  adminListFinancePlans,
  adminReconcileFinance,
  adminUpdateFinancePlan,
  type AdminFinanceMetrics,
  type BillingPlan,
  type BillingPlanPayload,
} from "@/lib/api";
import {
  BILLING_CYCLE_OPTIONS,
  CAKTO_ESSENTIAL_EVENTS,
  CURRENCY_OPTIONS,
  billingCycleLabel,
  currencySymbol,
  formatPriceInput,
  normalizeFinanceCollections,
  normalizeListPayload,
  parsePriceInput,
  planCopyForCycle,
  slugifyPlanName,
  summarizeCaktoProduct,
  uniquePlanSlug,
  validatePlanDraft,
  type PlanDraft,
} from "@/lib/financePresentation";
import { meAccessQueryOptions } from "@/lib/meAccessQuery";

type FinanceTab = "dashboard" | "planos" | "configuracoes";

export const Route = createFileRoute("/_authenticated/admin/financeiro")({
  head: () => ({ meta: [{ title: "Financeiro — Administração ElCapo" }] }),
  component: AdminFinancePage,
});

function initialTab(): FinanceTab {
  if (typeof window === "undefined") return "dashboard";
  const tab = new URLSearchParams(window.location.search).get("tab");
  return tab === "planos" || tab === "configuracoes" ? tab : "dashboard";
}

function AdminFinancePage() {
  const [tab, setTab] = useState<FinanceTab>(initialTab);
  const access = useQuery(meAccessQueryOptions());
  const permissions = new Set(access.data?.permissions ?? []);
  const canViewSettings = permissions.has("finance.settings.manage");
  const visibleTab = tab === "configuracoes" && !canViewSettings ? "dashboard" : tab;

  function selectTab(next: FinanceTab) {
    setTab(next);
    const url = new URL(window.location.href);
    if (next === "dashboard") url.searchParams.delete("tab");
    else url.searchParams.set("tab", next);
    window.history.replaceState(null, "", `${url.pathname}${url.search}`);
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="page-title">Financeiro administrativo</h1>
        <p className="page-lead">
          Receita, assinaturas, catálogo de ofertas e integração segura com a Cakto.
        </p>
      </header>

      <nav className="flex flex-wrap gap-2" aria-label="Seções do financeiro">
        {(
          [
            ["dashboard", "Dashboard"],
            ["planos", "Ofertas"],
            ...(canViewSettings ? ([["configuracoes", "Configurações"]] as const) : []),
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            onClick={() => selectTab(value)}
            className={`rounded-lg border px-4 py-2 text-sm font-semibold transition ${
              visibleTab === value
                ? "border-primary/50 bg-primary/15 text-primary"
                : "border-border bg-background/40 hover:bg-accent"
            }`}
            aria-current={visibleTab === value ? "page" : undefined}
          >
            {label}
          </button>
        ))}
      </nav>

      {visibleTab === "dashboard" ? <FinanceDashboard /> : null}
      {visibleTab === "planos" ? (
        <FinancePlans canManage={permissions.has("finance.plans.manage")} />
      ) : null}
      {visibleTab === "configuracoes" ? (
        <FinanceSettings canReconcile={permissions.has("finance.reconcile")} />
      ) : null}
    </div>
  );
}

function FinanceDashboard() {
  const [days, setDays] = useState(30);
  const [range, setRange] = useState<DateRangeValue>(() =>
    rangeFromPreset("last_30", new Date(), 365),
  );
  const metrics = useQuery({
    queryKey: ["admin", "finance", "metrics", days],
    queryFn: async () => {
      const response = await adminFinanceMetrics(days);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
  });

  return (
    <section className="space-y-5" aria-labelledby="finance-dashboard-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="finance-dashboard-title" className="text-lg font-semibold">
            Indicadores financeiros
          </h2>
          <p className="text-sm text-muted-foreground">
            Valores confirmados no período selecionado.
          </p>
        </div>
        <DashboardDateFilter
          days={days}
          value={range}
          maxDays={365}
          onChange={(nextDays, nextRange) => {
            setDays(nextDays);
            setRange(nextRange);
          }}
          isFetching={metrics.isFetching}
        />
      </div>

      {metrics.isLoading ? (
        <LoadingNotice label="Consolidando dados financeiros..." />
      ) : metrics.error ? (
        <ErrorNotice error={metrics.error} />
      ) : metrics.data ? (
        <FinanceMetricsContent metrics={metrics.data} />
      ) : (
        <EmptyNotice label="Nenhuma métrica financeira disponível." />
      )}
    </section>
  );
}

function FinanceMetricsContent({ metrics }: { metrics: AdminFinanceMetrics }) {
  const { breakdowns, dailySeries } = normalizeFinanceCollections(metrics);
  const cards = [
    ["Receita bruta", formatMoney(metrics.gross_revenue)],
    ["Receita líquida", formatMoney(metrics.net_revenue)],
    ["Reembolsos", formatMoney(metrics.refunds)],
    ["Chargebacks", formatMoney(metrics.chargebacks)],
    ["Pagamentos aprovados", formatNumber(metrics.approved_payments)],
    ["Pagamentos recusados", formatNumber(metrics.refused_payments)],
    ["Assinaturas ativas", formatNumber(metrics.active_subscriptions)],
    ["Assinaturas canceladas", formatNumber(metrics.canceled_subscriptions)],
    ["MRR", formatMoney(metrics.mrr)],
    ["ARR", formatMoney(metrics.arr)],
    ["Ticket médio", formatMoney(metrics.average_ticket)],
    ["Taxa de aprovação", formatPercent(metrics.approval_rate)],
    ["Churn", formatPercent(metrics.churn_rate)],
  ] as const;

  return (
    <>
      <p className="text-xs text-muted-foreground">
        Período: {formatDate(metrics.period_start)} a {formatDate(metrics.period_end)} ·{" "}
        {metrics.period_days} dias
      </p>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {cards.map(([label, value]) => (
          <article key={label} className="page-surface p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {label}
            </p>
            <p className="mt-2 text-2xl font-bold tabular-nums">{value}</p>
          </article>
        ))}
      </div>
      <DailySeries items={dailySeries} />
      <div className="grid gap-4 lg:grid-cols-2">
        {Object.entries(breakdowns).length ? (
          Object.entries(breakdowns).map(([name, items]) => (
            <article key={name} className="page-surface p-5">
              <h3 className="font-semibold">{humanize(name)}</h3>
              <div className="mt-3 space-y-2">
                {items.length ? (
                  items.map((item, index) => (
                    <div
                      key={`${name}-${index}`}
                      className="flex items-center justify-between gap-3 rounded-lg border border-border/70 p-3 text-sm"
                    >
                      <span>{breakdownLabel(item, index)}</span>
                      <strong>{breakdownValue(item)}</strong>
                    </div>
                  ))
                ) : (
                  <EmptyNotice label="Sem dados neste agrupamento." compact />
                )}
              </div>
            </article>
          ))
        ) : (
          <div className="page-surface p-5 lg:col-span-2">
            <EmptyNotice label="Sem detalhamentos para o período." />
          </div>
        )}
      </div>
    </>
  );
}

function DailySeries({ items }: { items: unknown[] }) {
  const points = items.filter(isRecord);
  const maximum = Math.max(0, ...points.map((point) => numberValue(point.gross_revenue)));
  return (
    <article className="page-surface p-5">
      <h3 className="font-semibold">Receita bruta diária</h3>
      {points.length ? (
        <div
          className="mt-5 flex h-48 items-end gap-1 overflow-x-auto border-b border-border pb-1"
          role="img"
          aria-label="Série diária de receita bruta"
        >
          {points.map((point, index) => {
            const value = numberValue(point.gross_revenue);
            const height = maximum > 0 ? Math.max(3, (value / maximum) * 100) : 3;
            return (
              <div
                key={`${String(point.date ?? index)}-${index}`}
                className="group relative min-w-3 flex-1 rounded-t bg-primary/70"
                style={{ height: `${height}%` }}
                title={`${formatDate(String(point.date ?? ""))}: ${formatMoney(value)}`}
              />
            );
          })}
        </div>
      ) : (
        <EmptyNotice label="Sem série diária para o período." />
      )}
    </article>
  );
}

function FinancePlans({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<BillingPlan | "create" | null>(null);
  const plans = useQuery({
    queryKey: ["admin", "finance", "plans"],
    queryFn: async () => {
      const response = await adminListFinancePlans();
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return normalizeListPayload<BillingPlan>(response.data).sort(
        (left, right) =>
          left.display_order - right.display_order || left.name.localeCompare(right.name),
      );
    },
  });
  const deletePlan = useMutation({
    mutationFn: async (plan: BillingPlan) => {
      const response = await adminDeleteFinancePlan(plan.id);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ["admin", "finance", "plans"] }),
  });
  const productSummary = summarizeCaktoProduct(plans.data ?? []);

  return (
    <section className="space-y-4" aria-labelledby="finance-plans-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="finance-plans-title" className="text-lg font-semibold">
            Ofertas
          </h2>
          <p className="text-sm text-muted-foreground">
            Variações comerciais do mesmo produto exibidas aos clientes.
          </p>
        </div>
        {canManage ? (
          <button
            type="button"
            onClick={() => setEditing("create")}
            className="page-cta inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold"
          >
            <Plus className="h-4 w-4" />
            Nova oferta
          </button>
        ) : null}
      </div>
      {!plans.isLoading && !plans.error ? (
        <article className="page-surface p-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Produto Cakto compartilhado
          </p>
          <p className="mt-2 break-all font-mono text-sm">
            {productSummary.productId ||
              "Defina PROD_CAKTO_DEFAULT_PRODUCT_ID no servidor; novas ofertas usam esse produto."}
          </p>
          <p className="mt-2 text-xs text-muted-foreground">
            Cada oferta criada no financeiro gera automaticamente a oferta correspondente na
            Cakto (quando a API estiver configurada).
          </p>
          {productSummary.hasConflict ? (
            <p className="mt-3 text-sm text-destructive" role="alert">
              Existem produtos diferentes no catálogo. Edite as ofertas para usar um único produto.
            </p>
          ) : null}
        </article>
      ) : null}
      {plans.isLoading ? <LoadingNotice label="Carregando ofertas..." /> : null}
      {plans.error ? <ErrorNotice error={plans.error} /> : null}
      {deletePlan.error ? <ErrorNotice error={deletePlan.error} /> : null}
      {!plans.isLoading && !plans.error && !(plans.data?.length ?? 0) ? (
        <EmptyNotice label="Nenhuma oferta cadastrada." />
      ) : null}
      <div className="grid gap-4 lg:grid-cols-2">
        {(plans.data ?? []).map((plan) => (
          <article key={plan.id} className="page-surface p-5">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="font-semibold">{plan.name}</h3>
                  {plan.is_featured ? <span className="plan-savings">Destaque</span> : null}
                  <span className={plan.is_active ? "text-emerald-400" : "text-muted-foreground"}>
                    {plan.is_active ? "Ativo" : "Inativo"}
                  </span>
                </div>
                <p className="mt-1 text-sm text-muted-foreground">{plan.description}</p>
              </div>
              <strong className="text-lg">{formatMoney(plan.price, plan.currency)}</strong>
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              {billingCycleLabel(plan.billing_interval_months) ||
                `A cada ${plan.billing_interval_months} meses`}
              {" · "}
              {plan.cakto_offer_id ? `oferta ${plan.cakto_offer_id}` : "sem oferta na Cakto"}
            </p>
            <ul className="mt-4 space-y-2 text-sm">
              {plan.features.map((feature) => (
                <li key={feature} className="flex items-center gap-2">
                  <Check className="h-3.5 w-3.5 text-primary" />
                  {feature}
                </li>
              ))}
            </ul>
            {canManage ? (
              <div className="mt-5 flex gap-2 border-t border-border/60 pt-4">
                <button
                  type="button"
                  onClick={() => setEditing(plan)}
                  className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-semibold hover:bg-accent"
                >
                  <Pencil className="h-3.5 w-3.5" />
                  Editar
                </button>
                <button
                  type="button"
                  disabled={deletePlan.isPending}
                  onClick={() => {
                    if (window.confirm(`Excluir ou desativar a oferta ${plan.name}?`)) {
                      deletePlan.mutate(plan);
                    }
                  }}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-destructive/40 px-3 py-2 text-xs font-semibold text-destructive"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Excluir
                </button>
              </div>
            ) : null}
          </article>
        ))}
      </div>
      {editing ? (
        <PlanDialog
          plan={editing === "create" ? null : editing}
          sharedProductId={productSummary.productId}
          takenSlugs={(plans.data ?? [])
            .filter((item) => editing === "create" || item.id !== editing.id)
            .map((item) => item.slug)}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void queryClient.invalidateQueries({ queryKey: ["admin", "finance", "plans"] });
          }}
        />
      ) : null}
    </section>
  );
}

function PlanDialog({
  plan,
  sharedProductId,
  takenSlugs,
  onClose,
  onSaved,
}: {
  plan: BillingPlan | null;
  sharedProductId: string;
  takenSlugs: string[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const settings = useQuery({
    queryKey: ["admin", "finance", "settings"],
    queryFn: async () => {
      const response = await adminFinanceSettings();
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
  });
  const defaultProductId =
    sharedProductId || settings.data?.default_product_id?.trim() || "";
  const settingsReady = settings.isSuccess;
  const autoProvision = Boolean(settings.data?.offers_provisioning_configured);
  const [draft, setDraft] = useState<PlanDraft>({
    name: plan?.name ?? "",
    slug: plan?.slug ?? "",
    // Oferta nova nasce com o texto sugerido do ciclo padrão, nunca em branco.
    description: plan?.description ?? planCopyForCycle(1).description,
    price: plan?.price ?? 0,
    currency: plan?.currency ?? "BRL",
    billing_interval_months: plan?.billing_interval_months ?? 1,
    features: plan?.features ?? planCopyForCycle(1).features,
    cakto_product_id: plan?.cakto_product_id ?? defaultProductId,
    cakto_offer_id: plan?.cakto_offer_id ?? "",
    checkout_url: plan?.checkout_url ?? "",
    is_featured: plan?.is_featured ?? false,
    is_active: plan?.is_active ?? true,
    display_order: plan?.display_order ?? 0,
  });
  const [featuresText, setFeaturesText] = useState(draft.features.join("\n"));
  const [errors, setErrors] = useState<Partial<Record<keyof PlanDraft, string>>>({});
  // Enquanto o texto não for tocado à mão, ele acompanha o ciclo escolhido.
  // Plano já existente nasce "tocado": o que o dono escreveu não se mexe sozinho.
  const [copyTouched, setCopyTouched] = useState(Boolean(plan));

  /** Escreve a sugestão do ciclo na descrição e nos benefícios. */
  function applyCycleCopy(months: number) {
    const copy = planCopyForCycle(months);
    setDraft((current) => ({ ...current, description: copy.description }));
    setFeaturesText(copy.features.join("\n"));
  }

  useEffect(() => {
    if (plan || !defaultProductId) return;
    setDraft((current) =>
      current.cakto_product_id.trim()
        ? current
        : { ...current, cakto_product_id: defaultProductId },
    );
  }, [defaultProductId, plan]);

  const mutation = useMutation({
    mutationFn: async () => {
      if (!settingsReady) {
        throw new Error("Aguarde a verificação da integração Cakto.");
      }
      if (!plan && !autoProvision) {
        throw new Error(
          "Criação automática indisponível. Em Configurações, ative PROD_CAKTO_ENABLED com OAuth e PROD_CAKTO_DEFAULT_PRODUCT_ID.",
        );
      }
      const normalizedDraft = {
        ...draft,
        // Slug é opcional na tela: em branco, deriva do nome sem colidir.
        slug:
          draft.slug.trim() ||
          uniquePlanSlug(slugifyPlanName(draft.name), takenSlugs),
        cakto_product_id: (draft.cakto_product_id || defaultProductId).trim(),
        features: featuresText
          .split("\n")
          .map((feature) => feature.trim())
          .filter(Boolean),
      };
      // Novas ofertas usam o produto do .env; vínculo manual só na edição/migração.
      const validation = validatePlanDraft(normalizedDraft, {
        requireManualCaktoLink: Boolean(plan),
      });
      setErrors(validation);
      if (Object.keys(validation).length) throw new Error("Revise os campos destacados.");
      const payload: BillingPlanPayload = {
        ...normalizedDraft,
        cakto_product_id: normalizedDraft.cakto_product_id.trim() || null,
        cakto_offer_id: plan
          ? normalizedDraft.cakto_offer_id.trim() || null
          : normalizedDraft.cakto_offer_id.trim() || null,
        checkout_url: plan
          ? normalizedDraft.checkout_url.trim() || null
          : normalizedDraft.checkout_url.trim() || null,
      };
      if (!plan && autoProvision) {
        payload.cakto_offer_id = null;
        payload.checkout_url = null;
        payload.cakto_product_id = defaultProductId || null;
      }
      const response = plan
        ? await adminUpdateFinancePlan(plan.id, payload)
        : await adminCreateFinancePlan(payload);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    onSuccess: onSaved,
  });

  const dialogCopy = adminDialogContent("plan", Boolean(plan));

  return (
    <AdminFormDialog {...dialogCopy} icon={Wallet} onClose={onClose}>
      <form
        className="space-y-5"
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate();
        }}
      >
        <AdminFormSection
          icon={BadgeDollarSign}
          step={1}
          title="Cobrança"
          description="Nome que o cliente vê, quanto custa e de quanto em quanto tempo cobra."
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <PlanInput
                label="Nome do plano"
                field="name"
                placeholder="Plano Mensal"
                draft={draft}
                setDraft={setDraft}
                error={errors.name}
              />
            </div>
            <PriceField
              value={draft.price}
              currency={draft.currency}
              onChange={(price) => setDraft((current) => ({ ...current, price }))}
              error={errors.price}
            />
            <SelectField
              label="Moeda"
              value={draft.currency}
              onChange={(currency) => setDraft((current) => ({ ...current, currency }))}
              options={CURRENCY_OPTIONS.map((option) => ({
                value: option.code,
                label: option.label,
              }))}
              error={errors.currency}
            />
            <div className="sm:col-span-2">
              <CycleField
                months={draft.billing_interval_months}
                onChange={(billing_interval_months) => {
                  setDraft((current) => ({ ...current, billing_interval_months }));
                  if (!copyTouched) applyCycleCopy(billing_interval_months);
                }}
                error={errors.billing_interval_months}
              />
            </div>
          </div>
        </AdminFormSection>

        <AdminFormSection
          icon={ListChecks}
          step={2}
          title="Apresentação"
          description="Sugerimos um texto conforme o tempo do plano. Edite à vontade — o que você escrever fica."
        >
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => {
                applyCycleCopy(draft.billing_interval_months);
                setCopyTouched(false);
              }}
              className="inline-flex items-center gap-2 rounded-lg border border-cyan-300/40 bg-cyan-400/10 px-3 py-2 text-xs font-semibold text-cyan-100 transition hover:bg-cyan-400/20"
            >
              <Sparkles className="h-3.5 w-3.5" />
              Usar o texto sugerido
              {billingCycleLabel(draft.billing_interval_months)
                ? ` (${billingCycleLabel(draft.billing_interval_months).toLowerCase()})`
                : ""}
            </button>
            <span className="text-xs text-slate-400">
              {copyTouched
                ? "Texto editado por você — não muda mais sozinho."
                : "Acompanhando o tempo do plano."}
            </span>
          </div>
          <div className="grid gap-4">
            <PlanTextArea
              label="Descrição"
              value={draft.description}
              onChange={(description) => {
                setCopyTouched(true);
                setDraft((current) => ({ ...current, description }));
              }}
              error={errors.description}
            />
            <PlanTextArea
              label="Benefícios (um por linha)"
              value={featuresText}
              onChange={(value) => {
                setCopyTouched(true);
                setFeaturesText(value);
              }}
              error={errors.features}
            />
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <CheckField
              label="Destacar no catálogo"
              checked={draft.is_featured}
              onChange={(is_featured) => setDraft((current) => ({ ...current, is_featured }))}
            />
            <CheckField
              label="Oferta disponível"
              checked={draft.is_active}
              onChange={(is_active) => setDraft((current) => ({ ...current, is_active }))}
            />
          </div>
        </AdminFormSection>

        <AdminFormSection
          icon={Link2}
          step={3}
          title="Oferta Cakto"
          description={
            !plan && autoProvision
              ? "Ao salvar, a oferta é criada automaticamente no produto padrão da Cakto."
              : !plan
                ? "A criação automática depende da integração Cakto no servidor."
                : "Vínculo já provisionado com a oferta e o checkout Cakto."
          }
        >
          {settings.isLoading ? (
            <p className="text-sm text-slate-400">Verificando integração Cakto...</p>
          ) : settings.error ? (
            <ErrorNotice error={settings.error} />
          ) : !plan && autoProvision ? (
            <div className="space-y-3 text-sm text-slate-300">
              <p>
                Produto padrão:{" "}
                <code className="break-all rounded bg-slate-950/60 px-2 py-1 font-mono text-xs text-cyan-200">
                  {defaultProductId || "Configure PROD_CAKTO_DEFAULT_PRODUCT_ID no servidor"}
                </code>
              </p>
              <p className="text-xs text-slate-400">
                Não é necessário informar ID de oferta nem checkout. O backend cria a oferta
                na Cakto, grava o ID e gera o checkout oficial em
                pay.cakto.com.br/&lt;id-da-oferta&gt;.
              </p>
            </div>
          ) : !plan ? (
            <div className="space-y-2 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-100">
              <p className="font-medium">Criação automática indisponível</p>
              <p className="text-xs text-amber-100/80">
                Confirme em Financeiro → Configurações se Webhook, API Cakto e Criação de
                ofertas estão verdes. No servidor:{" "}
                <code className="font-mono">PROD_CAKTO_ENABLED=true</code>, OAuth e{" "}
                <code className="font-mono">PROD_CAKTO_DEFAULT_PRODUCT_ID</code>.
              </p>
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              <PlanInput
                label="ID do produto compartilhado"
                field="cakto_product_id"
                draft={draft}
                setDraft={setDraft}
                readOnly={Boolean(defaultProductId)}
                error={errors.cakto_product_id}
              />
              <PlanInput
                label="ID da oferta"
                field="cakto_offer_id"
                draft={draft}
                setDraft={setDraft}
                error={errors.cakto_offer_id}
              />
              <div className="sm:col-span-2">
                <PlanInput
                  label="URL de checkout"
                  field="checkout_url"
                  type="url"
                  draft={draft}
                  setDraft={setDraft}
                  error={errors.checkout_url}
                />
              </div>
            </div>
          )}
        </AdminFormSection>

        <details className="rounded-2xl border border-slate-700/70 bg-slate-900/60 p-4 sm:p-5">
          <summary className="cursor-pointer select-none text-sm font-semibold text-slate-200">
            Opções avançadas
            <span className="ml-2 text-xs font-normal text-slate-400">
              identificador e posição na lista — preenchidos sozinhos
            </span>
          </summary>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <PlanInput
              label="Identificador (slug)"
              field="slug"
              placeholder={slugifyPlanName(draft.name) || "gerado a partir do nome"}
              hint="Deixe em branco para gerar a partir do nome."
              draft={draft}
              setDraft={setDraft}
              error={errors.slug}
            />
            <PlanInput
              label="Ordem de exibição"
              field="display_order"
              type="number"
              hint="Menor número aparece primeiro na página de planos."
              draft={draft}
              setDraft={setDraft}
              error={errors.display_order}
            />
          </div>
        </details>

        {mutation.error ? <ErrorNotice error={mutation.error} /> : null}
        <AdminFormActions>
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border border-slate-600/70 bg-slate-800/70 px-5 py-3 text-sm font-medium text-slate-300 transition hover:border-slate-500 hover:bg-slate-700 hover:text-white"
          >
            Cancelar
          </button>
          <button
            type="submit"
            disabled={
              mutation.isPending ||
              settings.isLoading ||
              Boolean(settings.error) ||
              (!plan && (!autoProvision || !defaultProductId))
            }
            className="inline-flex items-center justify-center gap-2 rounded-xl border border-cyan-200/20 bg-gradient-to-r from-cyan-400 to-cyan-500 px-6 py-3 text-sm font-bold text-slate-950 shadow-[0_12px_32px_rgba(34,211,238,0.2)] transition-all hover:-translate-y-0.5 hover:from-cyan-300 hover:to-cyan-400 hover:shadow-[0_16px_38px_rgba(34,211,238,0.3)] disabled:translate-y-0 disabled:opacity-60"
          >
            {mutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Plus className="h-4 w-4" />
            )}
            {plan ? "Salvar alterações" : "Criar oferta"}
          </button>
        </AdminFormActions>
      </form>
    </AdminFormDialog>
  );
}

function FinanceSettings({ canReconcile }: { canReconcile: boolean }) {
  const queryClient = useQueryClient();
  const [copied, setCopied] = useState(false);
  const settings = useQuery({
    queryKey: ["admin", "finance", "settings"],
    queryFn: async () => {
      const response = await adminFinanceSettings();
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
  });
  const reconcile = useMutation({
    mutationFn: async () => {
      const response = await adminReconcileFinance();
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ["admin", "finance", "settings"] }),
  });

  if (settings.isLoading) return <LoadingNotice label="Verificando integração financeira..." />;
  if (settings.error) return <ErrorNotice error={settings.error} />;
  if (!settings.data) return <EmptyNotice label="Configurações indisponíveis." />;

  return (
    <section className="space-y-4" aria-labelledby="finance-settings-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="finance-settings-title" className="text-lg font-semibold">
            Integração Cakto
          </h2>
          <p className="text-sm text-muted-foreground">
            Tokens e segredos ficam somente no ambiente seguro do servidor e nunca são exibidos
            aqui.
          </p>
        </div>
        {canReconcile ? (
          <button
            type="button"
            disabled={reconcile.isPending}
            onClick={() => reconcile.mutate()}
            className="page-cta inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold"
          >
            <RefreshCw className={`h-4 w-4 ${reconcile.isPending ? "animate-spin" : ""}`} />
            Reconciliação
          </button>
        ) : null}
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <StatusCard label="Webhook" configured={settings.data.webhook_configured} />
        <StatusCard label="API Cakto" configured={settings.data.api_configured} />
        <StatusCard
          label="Criação de ofertas"
          configured={Boolean(settings.data.offers_provisioning_configured)}
        />
      </div>
      {settings.data.default_product_id ? (
        <article className="page-surface p-5">
          <h3 className="font-semibold">Produto padrão Cakto</h3>
          <code className="mt-3 block break-all rounded-lg border bg-background/50 p-3 text-xs">
            {settings.data.default_product_id}
          </code>
          <p className="mt-2 text-xs text-muted-foreground">
            Todas as novas ofertas do financeiro são criadas neste produto via API.
          </p>
        </article>
      ) : null}
      <article className="page-surface p-5">
        <h3 className="font-semibold">URL do webhook</h3>
        <div className="mt-3 flex flex-wrap gap-2">
          <code className="min-w-0 flex-1 break-all rounded-lg border bg-background/50 p-3 text-xs">
            {settings.data.webhook_url || "Ainda não disponibilizada"}
          </code>
          <button
            type="button"
            disabled={!settings.data.webhook_url}
            onClick={async () => {
              await navigator.clipboard.writeText(settings.data.webhook_url);
              setCopied(true);
              window.setTimeout(() => setCopied(false), 2000);
            }}
            className="inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm"
          >
            <Clipboard className="h-4 w-4" />
            Copiar
          </button>
        </div>
        <p className="mt-2 text-xs text-muted-foreground" aria-live="polite">
          {copied ? "URL copiada." : "Cadastre esta URL no painel da Cakto."}
        </p>
      </article>
      <div className="grid gap-4 lg:grid-cols-2">
        <article className="page-surface p-5">
          <h3 className="font-semibold">Eventos essenciais</h3>
          <ul className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
            {CAKTO_ESSENTIAL_EVENTS.map((event) => {
              const enabled = settings.data.enabled_events.includes(event);
              return (
                <li key={event} className="flex items-center gap-2">
                  <Check
                    className={`h-4 w-4 ${enabled ? "text-emerald-400" : "text-muted-foreground"}`}
                  />
                  {humanize(event)}
                </li>
              );
            })}
          </ul>
        </article>
        <article className="page-surface p-5">
          <h3 className="font-semibold">Sincronização</h3>
          <dl className="mt-3 space-y-3 text-sm">
            <div>
              <dt className="text-muted-foreground">Último evento</dt>
              <dd>{formatDateTime(settings.data.last_event_at)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Última reconciliação</dt>
              <dd>{formatDateTime(settings.data.last_reconciled_at)}</dd>
            </div>
          </dl>
        </article>
      </div>
      <div aria-live="polite">
        {reconcile.error ? <ErrorNotice error={reconcile.error} /> : null}
        {reconcile.isSuccess ? (
          <p className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4 text-sm text-emerald-300">
            Reconciliação solicitada com sucesso.
          </p>
        ) : null}
      </div>
    </section>
  );
}

function PlanInput({
  label,
  field,
  draft,
  setDraft,
  error,
  type = "text",
  step,
  readOnly = false,
  placeholder,
  hint,
}: {
  label: string;
  field: keyof PlanDraft;
  draft: PlanDraft;
  setDraft: React.Dispatch<React.SetStateAction<PlanDraft>>;
  error?: string;
  type?: string;
  step?: string;
  readOnly?: boolean;
  placeholder?: string;
  hint?: string;
}) {
  const numeric = type === "number";
  return (
    <label className="grid gap-1.5 text-sm">
      <span className="text-xs font-semibold uppercase tracking-[0.08em] text-slate-300">
        {label}
      </span>
      <input
        type={type}
        step={step}
        readOnly={readOnly}
        placeholder={placeholder}
        value={String(draft[field])}
        onChange={(event) =>
          setDraft((current) => ({
            ...current,
            [field]: numeric ? Number(event.target.value) : event.target.value,
          }))
        }
        aria-invalid={Boolean(error)}
        className={ADMIN_FIELD_CLASS}
      />
      {error ? (
        <span className="text-xs text-destructive">{error}</span>
      ) : hint ? (
        <span className="text-xs text-slate-400">{hint}</span>
      ) : null}
    </label>
  );
}

/** Campo de preço com o símbolo da moeda e máscara local. */
function PriceField({
  value,
  currency,
  onChange,
  error,
}: {
  value: number;
  currency: string;
  onChange: (value: number) => void;
  error?: string;
}) {
  // Texto próprio para o usuário digitar "1.147,90" sem o campo reformatar a cada tecla.
  const [text, setText] = useState(() => formatPriceInput(value, currency));
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    if (!editing) setText(formatPriceInput(value, currency));
  }, [value, currency, editing]);

  return (
    <label className="grid gap-1.5 text-sm">
      <span className="text-xs font-semibold uppercase tracking-[0.08em] text-slate-300">
        Preço
      </span>
      <div className="relative">
        <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm font-semibold text-slate-400">
          {currencySymbol(currency)}
        </span>
        <input
          type="text"
          inputMode="decimal"
          value={text}
          placeholder="0,00"
          onFocus={() => setEditing(true)}
          onChange={(event) => {
            setText(event.target.value);
            const parsed = parsePriceInput(event.target.value);
            onChange(Number.isFinite(parsed) ? parsed : 0);
          }}
          onBlur={() => {
            setEditing(false);
            setText(formatPriceInput(value, currency));
          }}
          aria-invalid={Boolean(error)}
          className={`${ADMIN_FIELD_CLASS} pl-11`}
        />
      </div>
      {error ? <span className="text-xs text-destructive">{error}</span> : null}
    </label>
  );
}

/** Seletor simples reaproveitável no formulário de oferta. */
function SelectField({
  label,
  value,
  options,
  onChange,
  error,
  hint,
}: {
  label: string;
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (value: string) => void;
  error?: string;
  hint?: string;
}) {
  return (
    <label className="grid gap-1.5 text-sm">
      <span className="text-xs font-semibold uppercase tracking-[0.08em] text-slate-300">
        {label}
      </span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={Boolean(error)}
        className={ADMIN_FIELD_CLASS}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {error ? (
        <span className="text-xs text-destructive">{error}</span>
      ) : hint ? (
        <span className="text-xs text-slate-400">{hint}</span>
      ) : null}
    </label>
  );
}

/** Ciclo de cobrança por atalho, com escape para um número de meses livre. */
function CycleField({
  months,
  onChange,
  error,
}: {
  months: number;
  onChange: (months: number) => void;
  error?: string;
}) {
  const preset = billingCycleLabel(months);
  const [custom, setCustom] = useState(!preset);

  return (
    <div className="grid gap-1.5 text-sm">
      <span className="text-xs font-semibold uppercase tracking-[0.08em] text-slate-300">
        Cobrança a cada
      </span>
      <div className="flex flex-wrap gap-2">
        {BILLING_CYCLE_OPTIONS.map((option) => {
          const selected = !custom && months === option.months;
          return (
            <button
              key={option.months}
              type="button"
              aria-pressed={selected}
              onClick={() => {
                setCustom(false);
                onChange(option.months);
              }}
              className={`rounded-xl border px-4 py-2 text-sm font-medium transition ${
                selected
                  ? "border-cyan-300/60 bg-cyan-400/15 text-cyan-100"
                  : "border-slate-600/70 bg-slate-800/70 text-slate-300 hover:border-slate-500 hover:text-white"
              }`}
            >
              {option.label}
            </button>
          );
        })}
        <button
          type="button"
          aria-pressed={custom}
          onClick={() => setCustom(true)}
          className={`rounded-xl border px-4 py-2 text-sm font-medium transition ${
            custom
              ? "border-cyan-300/60 bg-cyan-400/15 text-cyan-100"
              : "border-slate-600/70 bg-slate-800/70 text-slate-300 hover:border-slate-500 hover:text-white"
          }`}
        >
          Outro
        </button>
      </div>
      {custom ? (
        <div className="mt-1 flex items-center gap-2">
          <input
            type="number"
            min={1}
            value={months}
            onChange={(event) => onChange(Number(event.target.value))}
            aria-label="Meses entre as cobranças"
            aria-invalid={Boolean(error)}
            className={`${ADMIN_FIELD_CLASS} w-28`}
          />
          <span className="text-sm text-slate-400">
            {months === 1 ? "mês" : "meses"} entre as cobranças
          </span>
        </div>
      ) : null}
      {error ? <span className="text-xs text-destructive">{error}</span> : null}
    </div>
  );
}

function PlanTextArea({
  label,
  value,
  onChange,
  error,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
}) {
  return (
    <label className="grid gap-1.5 text-sm">
      <span className="text-xs font-semibold uppercase tracking-[0.08em] text-slate-300">
        {label}
      </span>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={3}
        aria-invalid={Boolean(error)}
        className={`${ADMIN_FIELD_CLASS} min-h-24 resize-y`}
      />
      {error ? <span className="text-xs text-destructive">{error}</span> : null}
    </label>
  );
}

function CheckField({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label
      className={`flex cursor-pointer items-center gap-3 rounded-xl border p-3.5 text-sm transition ${
        checked
          ? "border-primary/35 bg-primary/[0.08]"
          : "border-white/8 bg-slate-950/20 hover:border-white/15"
      }`}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="h-4 w-4 rounded accent-primary"
      />
      {label}
    </label>
  );
}

function StatusCard({ label, configured }: { label: string; configured: boolean }) {
  return (
    <article className="page-surface flex items-center gap-3 p-5">
      <div
        className={`rounded-xl border p-3 ${
          configured
            ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
            : "border-border bg-muted text-muted-foreground"
        }`}
      >
        {configured ? <ShieldCheck className="h-5 w-5" /> : <Wallet className="h-5 w-5" />}
      </div>
      <div>
        <h3 className="font-semibold">{label}</h3>
        <p className="text-sm text-muted-foreground">{configured ? "Configurado" : "Pendente"}</p>
      </div>
    </article>
  );
}

function LoadingNotice({ label }: { label: string }) {
  return (
    <p className="flex items-center gap-2 text-sm text-muted-foreground" aria-live="polite">
      <Loader2 className="h-4 w-4 animate-spin" />
      {label}
    </p>
  );
}

function EmptyNotice({ label, compact = false }: { label: string; compact?: boolean }) {
  return (
    <p
      className={`${compact ? "py-2" : "rounded-xl border border-dashed border-border p-5"} text-sm text-muted-foreground`}
    >
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
      {error instanceof Error ? error.message : "Não foi possível concluir a operação."}
    </p>
  );
}

function formatMoney(value: number, currency = "BRL") {
  return new Intl.NumberFormat("pt-BR", { style: "currency", currency }).format(numberValue(value));
}

function formatNumber(value: number) {
  return new Intl.NumberFormat("pt-BR").format(numberValue(value));
}

function formatPercent(value: number) {
  return `${new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 }).format(numberValue(value))}%`;
}

function formatDate(value: string) {
  if (!value) return "—";
  return formatBrasiliaDate(value) || value;
}

function formatDateTime(value: string | null) {
  if (!value) return "Ainda não registrado";
  return formatBrasiliaDateTime(value) || value;
}

function humanize(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function breakdownLabel(value: unknown, index: number) {
  if (!isRecord(value)) return `Item ${index + 1}`;
  return String(value.label ?? value.name ?? value.status ?? value.event ?? `Item ${index + 1}`);
}

function breakdownValue(value: unknown) {
  if (!isRecord(value)) return String(value ?? "—");
  if (typeof value.amount === "number")
    return formatMoney(value.amount, String(value.currency ?? "BRL"));
  if (typeof value.value === "number") return formatNumber(value.value);
  if (typeof value.count === "number") return formatNumber(value.count);
  return "—";
}

function numberValue(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
