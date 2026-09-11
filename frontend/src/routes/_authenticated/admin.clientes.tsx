import { createFileRoute } from "@tanstack/react-router";
import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Clock3,
  CreditCard,
  Edit3,
  History,
  IdCard,
  KeyRound,
  Loader2,
  Plus,
  Search,
  ShieldCheck,
  Trash2,
  UserRound,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  adminApproveClient,
  adminClientHistory,
  adminCreateClient,
  adminDeleteClient,
  adminListClients,
  adminStartImpersonation,
  adminUpdateClient,
  listBillingPlans,
  type AccountType,
  type AdminClient,
  type AdminClientPayload,
  type BillingPlan,
  type ClientSegment,
  type PaymentStatus,
} from "@/lib/api";
import {
  ADMIN_CLIENTS_STALE_TIME_MS,
  ADMIN_PENDING_STALE_TIME_MS,
  adminClientsQueryKey,
  prefetchAdminClientsSegment,
} from "@/lib/adminClientsQuery";
import {
  accountTypeOptions,
  adminDialogContent,
  validateAdminPassword,
} from "@/lib/adminPresentation";
import { normalizeListPayload } from "@/lib/financePresentation";
import { formatBrasiliaDate, formatBrasiliaDateTime } from "@/lib/brasiliaTime";
import {
  remainingDaysFromExpiresAt,
  trialDaysFromExpiresAt,
} from "@/lib/trial";
import { meAccessQueryOptions } from "@/lib/meAccessQuery";
import { AdminAccessTabs } from "@/components/AdminAccessTabs";
import {
  ADMIN_FIELD_CLASS,
  AdminFormActions,
  AdminFormDialog,
  AdminFormSection,
} from "@/components/AdminFormDialog";

export const Route = createFileRoute("/_authenticated/admin/clientes")({
  head: () => ({ meta: [{ title: "Acessos — Administração ElCapo" }] }),
  // Aquenta a fila de Pedidos antes da pintura (hover na sidebar também prefetcha).
  loader: async ({ context }) => {
    await prefetchAdminClientsSegment(context.queryClient, "pending");
  },
  component: AdminClientsPage,
});

const SEGMENTS: readonly { id: ClientSegment; label: string }[] = [
  { id: "pending", label: "Pedidos" },
  { id: "active", label: "Ativos" },
  { id: "trial", label: "Teste grátis" },
  { id: "marketing", label: "Marketing" },
  { id: "inactive", label: "Inativos" },
];

/** Aguarda o usuário parar de digitar antes de propagar o termo de busca. */
const SEARCH_DEBOUNCE_MS = 350;

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

function AdminClientsPage() {
  const queryClient = useQueryClient();
  const [segment, setSegment] = useState<ClientSegment>("pending");
  const [searchInput, setSearchInput] = useState("");
  const search = useDebouncedValue(searchInput, SEARCH_DEBOUNCE_MS);
  const [editing, setEditing] = useState<AdminClient | "create" | null>(null);
  const [approving, setApproving] = useState<AdminClient | null>(null);
  const [historyClient, setHistoryClient] = useState<AdminClient | null>(null);

  const access = useQuery({
    ...meAccessQueryOptions(),
  });
  const clients = useInfiniteQuery({
    queryKey: adminClientsQueryKey(segment, search),
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const response = await adminListClients(segment, pageParam, 10, search);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    getNextPageParam: (lastPage) => (lastPage.has_more ? lastPage.next_offset : undefined),
    staleTime: segment === "pending" ? ADMIN_PENDING_STALE_TIME_MS : ADMIN_CLIENTS_STALE_TIME_MS,
    placeholderData: keepPreviousData,
  });
  const items = clients.data?.pages.flatMap((page) => page.items) ?? [];
  const permissions = new Set(access.data?.permissions ?? []);

  const prefetchSegment = (next: ClientSegment) => {
    void prefetchAdminClientsSegment(queryClient, next);
  };

  const deleteMutation = useMutation({
    mutationFn: async (userId: string) => {
      const response = await adminDeleteClient(userId);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["admin", "clients"] }),
  });
  const impersonateMutation = useMutation({
    mutationFn: async ({ userId, reason }: { userId: string; reason: string }) => {
      const response = await adminStartImpersonation(userId, reason);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    onSuccess: (session) => {
      sessionStorage.setItem("elcapo_impersonation", JSON.stringify(session));
      window.location.assign("/dashboard");
    },
  });

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="page-title">Acessos</h1>
          <p className="page-lead">
            Em <strong>Pedidos</strong>, aprove cadastros e defina os dias de acesso. Nas demais
            abas, gerencie ativos, teste grátis, marketing e inativos.
          </p>
        </div>
        {permissions.has("clients.create") ? (
          <button
            type="button"
            onClick={() => setEditing("create")}
            className="page-cta inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold"
          >
            <Plus className="h-4 w-4" />
            Novo usuário
          </button>
        ) : null}
      </header>

      <AdminAccessTabs />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <nav className="flex flex-wrap gap-2 rounded-2xl border border-border bg-card p-2">
          {SEGMENTS.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setSegment(item.id)}
              onMouseEnter={() => prefetchSegment(item.id)}
              onFocus={() => prefetchSegment(item.id)}
              className={`rounded-lg px-4 py-2 text-sm font-semibold transition ${
                segment === item.id
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground"
              }`}
            >
              {item.label}
            </button>
          ))}
        </nav>

        <div className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <input
            type="search"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="Buscar por nome, email ou ID Trader"
            aria-label="Buscar leads por nome, email ou ID Trader"
            className="w-full rounded-2xl border border-border bg-card py-2.5 pl-9 pr-9 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
          />
          {searchInput ? (
            <button
              type="button"
              onClick={() => setSearchInput("")}
              aria-label="Limpar busca"
              className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded-md p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          ) : null}
        </div>
      </div>

      {clients.error ? <ErrorNotice error={clients.error} /> : null}
      {deleteMutation.error ? <ErrorNotice error={deleteMutation.error} /> : null}
      {impersonateMutation.error ? <ErrorNotice error={impersonateMutation.error} /> : null}

      {clients.isLoading && !clients.isPlaceholderData ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Carregando clientes...
        </div>
      ) : items.length === 0 ? (
        <div className="page-surface p-8 text-center text-sm text-muted-foreground">
          {search
            ? `Nenhum lead encontrado para "${search}" nesta aba.`
            : segment === "pending"
              ? "Nenhum pedido de cadastro aguardando aprovação."
              : "Nenhum cliente nesta categoria."}
        </div>
      ) : (
        <div
          className={`grid gap-4 lg:grid-cols-2 ${clients.isFetching && clients.isPlaceholderData ? "opacity-70" : ""}`}
        >
          {items.map((client) => (
            <ClientCard
              key={client.id}
              client={client}
              permissions={permissions}
              busy={deleteMutation.isPending || impersonateMutation.isPending}
              showApprove={segment === "pending" || client.approval_status === "pending"}
              onEdit={() => setEditing(client)}
              onApprove={() => setApproving(client)}
              onHistory={() => setHistoryClient(client)}
              onImpersonate={() => {
                const reason = window.prompt(
                  "Informe o motivo do acesso temporário (mínimo 10 caracteres):",
                );
                if (reason?.trim()) {
                  impersonateMutation.mutate({ userId: client.id, reason: reason.trim() });
                }
              }}
              onDelete={() => {
                if (window.confirm(`Desativar o acesso de ${client.name}?`)) {
                  deleteMutation.mutate(client.id);
                }
              }}
            />
          ))}
        </div>
      )}

      {clients.hasNextPage ? (
        <button
          type="button"
          disabled={clients.isFetchingNextPage}
          onClick={() => void clients.fetchNextPage()}
          className="mx-auto flex items-center gap-2 rounded-lg border border-border px-4 py-2 text-sm font-semibold"
        >
          {clients.isFetchingNextPage ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {clients.isFetchingNextPage ? "Carregando..." : "Ver mais 10"}
        </button>
      ) : null}

      {editing ? (
        <ClientDialog
          client={editing === "create" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void queryClient.invalidateQueries({ queryKey: ["admin", "clients"] });
          }}
        />
      ) : null}
      {approving ? (
        <ApproveLeadDialog
          client={approving}
          onClose={() => setApproving(null)}
          onSaved={() => {
            setApproving(null);
            void queryClient.invalidateQueries({ queryKey: ["admin", "clients"] });
          }}
        />
      ) : null}
      {historyClient ? (
        <HistoryDialog client={historyClient} onClose={() => setHistoryClient(null)} />
      ) : null}
    </div>
  );
}

function ClientCard({
  client,
  permissions,
  busy,
  showApprove,
  onEdit,
  onApprove,
  onHistory,
  onImpersonate,
  onDelete,
}: {
  client: AdminClient;
  permissions: Set<string>;
  busy: boolean;
  showApprove: boolean;
  onEdit: () => void;
  onApprove: () => void;
  onHistory: () => void;
  onImpersonate: () => void;
  onDelete: () => void;
}) {
  return (
    <article className="page-surface p-5">
      <div className="flex items-start gap-4">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-primary/30 bg-primary/10">
          <UserRound className="h-5 w-5 text-primary" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h2 className="font-semibold">{client.name}</h2>
              <p className="break-all text-sm text-muted-foreground">{client.email}</p>
            </div>
            <AccountBadge client={client} />
          </div>
          <dl className="mt-4 grid gap-2 text-sm sm:grid-cols-2">
            <ClientMeta label="Telefone" value={client.phone || "Não informado"} />
            <ClientMeta label="ID Trader" value={client.trader_id || "Não informado"} />
            <ClientMeta label="Criação" value={formatDate(client.created_at)} />
            <ClientMeta label="Plano" value={client.plan_name || "A definir"} />
            {client.account_type === "trial" && client.expires_at ? (
              <>
                <ClientMeta
                  label="Dias restantes"
                  value={`${remainingDaysFromExpiresAt(client.expires_at)} dia(s)`}
                />
                <ClientMeta
                  label="Expira em"
                  value={formatBrasiliaDateTime(client.expires_at)}
                />
              </>
            ) : null}
          </dl>
        </div>
      </div>
      <div className="mt-5 flex flex-wrap gap-2 border-t border-border/60 pt-4">
        {showApprove && (permissions.has("clients.update") || permissions.has("clients.edit")) ? (
          <ActionButton Icon={ShieldCheck} label="Aprovar acesso" onClick={onApprove} disabled={busy} />
        ) : null}
        {permissions.has("clients.update") ? (
          <ActionButton Icon={Edit3} label="Editar" onClick={onEdit} disabled={busy} />
        ) : null}
        {permissions.has("clients.history.read") ? (
          <ActionButton Icon={History} label="Ver histórico" onClick={onHistory} disabled={busy} />
        ) : null}
        {permissions.has("clients.impersonate") ? (
          <ActionButton
            Icon={KeyRound}
            label="Acessar conta"
            onClick={onImpersonate}
            disabled={busy}
          />
        ) : null}
        {permissions.has("clients.delete") ? (
          <ActionButton
            Icon={Trash2}
            label="Excluir"
            onClick={onDelete}
            disabled={busy}
            destructive
          />
        ) : null}
      </div>
    </article>
  );
}

function ApproveLeadDialog({
  client,
  onClose,
  onSaved,
}: {
  client: AdminClient;
  onClose: () => void;
  onSaved: () => void;
}) {
  const queryClient = useQueryClient();
  const [accessDays, setAccessDays] = useState("7");
  const [error, setError] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: async () => {
      const days = Number(accessDays);
      if (!Number.isInteger(days) || days < 1 || days > 365) {
        throw new Error("Informe entre 1 e 365 dias de acesso");
      }
      const response = await adminApproveClient(client.id, days);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: ["admin", "clients", "pending"] });
      queryClient.setQueriesData(
        { queryKey: ["admin", "clients", "pending"] },
        (old: { pages?: Array<{ items: AdminClient[]; has_more: boolean; next_offset: number }> } | undefined) => {
          if (!old?.pages) return old;
          return {
            ...old,
            pages: old.pages.map((page) => ({
              ...page,
              items: page.items.filter((item) => item.id !== client.id),
            })),
          };
        },
      );
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "clients"] });
      onSaved();
    },
    onError: (caught) => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "clients", "pending"] });
      setError(caught instanceof Error ? caught.message : "Falha ao aprovar lead");
    },
  });

  return (
    <AdminFormDialog
      eyebrow="Pedido de acesso"
      title={`Aprovar ${client.name || client.email}`}
      description="Defina quantos dias este pedido terá de acesso ao ElCapo após a aprovação."
      steps={["Confirme o pedido", "Informe os dias de acesso", "Libere a conta"]}
      icon={ShieldCheck}
      onClose={mutation.isPending ? () => undefined : onClose}
    >
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (mutation.isPending) return;
          setError(null);
          mutation.mutate();
        }}
      >
        <AdminFormSection
          icon={Clock3}
          step={1}
          title="Liberação"
          description="O lead recebe conta trial com a expiração informada."
        >
          <label className="block text-sm">
            <span className="mb-1.5 block font-medium text-slate-200">Dias de acesso</span>
            <input
              type="number"
              min={1}
              max={365}
              value={accessDays}
              disabled={mutation.isPending}
              onChange={(event) => setAccessDays(event.target.value)}
              className={ADMIN_FIELD_CLASS}
            />
          </label>
        </AdminFormSection>
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        <AdminFormActions>
          <button
            type="button"
            onClick={onClose}
            disabled={mutation.isPending}
            className="rounded-xl border border-slate-600/70 bg-slate-800/70 px-5 py-3 text-sm font-medium text-slate-300 disabled:opacity-60"
          >
            Cancelar
          </button>
          <button
            type="submit"
            disabled={mutation.isPending}
            className="inline-flex items-center justify-center gap-2 rounded-xl border border-cyan-200/20 bg-gradient-to-r from-cyan-400 to-cyan-500 px-6 py-3 text-sm font-bold text-slate-950 disabled:opacity-60"
          >
            {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {mutation.isPending ? "Aprovando..." : "Aprovar e liberar"}
          </button>
        </AdminFormActions>
      </form>
    </AdminFormDialog>
  );
}

function ClientDialog({
  client,
  onClose,
  onSaved,
}: {
  client: AdminClient | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const editing = client != null;
  const initialForm = useMemo(
    () => ({
      name: client?.name ?? "",
      email: client?.email ?? "",
      phone: client?.phone ?? "",
      traderId: client?.trader_id ?? "",
      password: "",
      accountType: client?.account_type ?? ("trial" as AccountType),
      trialDays: trialDaysFromExpiresAt(client?.expires_at),
      paymentStatus: client?.payment_status ?? ("not_required" as PaymentStatus),
      planId: client?.plan_id ?? "",
      marketingWinRate: String(client?.marketing_win_rate ?? 80),
    }),
    [client],
  );
  const [form, setForm] = useState(initialForm);
  const initialTrialDaysRef = useRef(initialForm.trialDays);

  useEffect(() => {
    setForm(initialForm);
    initialTrialDaysRef.current = initialForm.trialDays;
  }, [initialForm]);
  const plans = useQuery({
    queryKey: ["billing", "plans"],
    queryFn: async () => {
      const response = await listBillingPlans();
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return normalizeListPayload<BillingPlan>(response.data);
    },
  });
  const mutation = useMutation({
    mutationFn: async () => {
      if (!editing || form.password) {
        const passwordError = validateAdminPassword(form.password);
        if (passwordError) throw new Error(passwordError);
      }
      const trialDaysChanged = form.trialDays !== initialTrialDaysRef.current;
      const payload: AdminClientPayload = {
        name: form.name.trim(),
        email: form.email.trim(),
        phone: form.phone.trim() || null,
        trader_id: form.traderId.trim(),
        password: form.password,
        account_type: form.accountType,
        payment_status:
          form.accountType === "trial" || form.accountType === "marketing"
            ? "not_required"
            : form.paymentStatus,
        plan_id: form.accountType === "client" ? form.planId || null : null,
        marketing_mode: form.accountType === "marketing" ? "simulation" : null,
        marketing_win_rate: form.accountType === "marketing" ? Number(form.marketingWinRate) : null,
      };
      if (form.accountType === "trial") {
        if (!editing || trialDaysChanged) {
          payload.trial_days = Number(form.trialDays);
        }
      } else {
        payload.trial_days = null;
      }
      const response = editing
        ? await adminUpdateClient(client.id, {
            ...payload,
            password: payload.password || undefined,
          })
        : await adminCreateClient(payload);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    onSuccess: onSaved,
  });

  const dialogCopy = adminDialogContent("client", editing);

  return (
    <AdminFormDialog {...dialogCopy} icon={UserRound} onClose={onClose}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate();
        }}
        className="space-y-5"
      >
        <AdminFormSection
          icon={IdCard}
          step={1}
          title="Identificação"
          description="Dados usados para localizar e reconhecer este cliente."
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField
              label="Nome"
              value={form.name}
              onChange={(name) => setForm((current) => ({ ...current, name }))}
            />
            <FormField
              label="Email"
              type="email"
              value={form.email}
              onChange={(email) => setForm((current) => ({ ...current, email }))}
            />
            <FormField
              label="Telefone"
              value={form.phone}
              onChange={(phone) => setForm((current) => ({ ...current, phone }))}
            />
            <FormField
              label="ID Trader"
              value={form.traderId}
              onChange={(traderId) => setForm((current) => ({ ...current, traderId }))}
            />
          </div>
        </AdminFormSection>

        <AdminFormSection
          icon={KeyRound}
          step={2}
          title="Acesso"
          description="Defina credenciais e o tipo de experiência liberada."
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField
              label={editing ? "Nova senha (opcional)" : "Senha"}
              type="password"
              required={!editing}
              value={form.password}
              onChange={(password) => setForm((current) => ({ ...current, password }))}
              hint="8+ caracteres, maiúscula, minúscula e número"
            />
            <SelectField
              label="Tipo de conta"
              value={form.accountType}
              onChange={(accountType) =>
                setForm((current) => ({
                  ...current,
                  accountType: accountType as AccountType,
                  paymentStatus: accountType === "client" ? "pending" : "not_required",
                }))
              }
              options={[
                ...accountTypeOptions(editing).map((value) => ({
                  value,
                  label:
                    value === "trial"
                      ? "Teste grátis"
                      : value === "client"
                        ? "Cliente"
                        : "Marketing",
                })),
              ]}
            />
            {form.accountType === "trial" ? (
              <FormField
                label="Tempo de teste grátis (dias)"
                type="number"
                min={1}
                max={365}
                value={form.trialDays}
                onChange={(trialDays) => setForm((current) => ({ ...current, trialDays }))}
              />
            ) : null}
          </div>
          {form.accountType === "marketing" ? (
            <div className="mt-4 grid gap-4">
              <div className="rounded-xl border border-primary/25 bg-primary/[0.07] p-3.5 text-sm text-primary/90">
                Conta Marketing usa somente simulação identificada. Nenhuma ordem real é enviada.
              </div>
              <FormField
                label="Taxa-alvo da simulação (%)"
                type="number"
                min={0}
                max={100}
                value={form.marketingWinRate}
                onChange={(marketingWinRate) =>
                  setForm((current) => ({ ...current, marketingWinRate }))
                }
              />
            </div>
          ) : null}
        </AdminFormSection>

        {form.accountType === "client" ? (
          <AdminFormSection
            icon={CreditCard}
            step={3}
            title="Oferta e cobrança"
            description="Associe a oferta e confira a situação financeira atual."
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <SelectField
                label="Pagamento"
                value={form.paymentStatus}
                onChange={(paymentStatus) =>
                  setForm((current) => ({
                    ...current,
                    paymentStatus: paymentStatus as PaymentStatus,
                  }))
                }
                options={[
                  { value: "paid", label: "Pago" },
                  { value: "pending", label: "Ainda não pagou" },
                  { value: "overdue", label: "Atrasado" },
                  { value: "refused", label: "Recusado" },
                  { value: "canceled", label: "Cancelado" },
                  { value: "refunded", label: "Reembolsado" },
                  { value: "chargeback", label: "Chargeback" },
                ]}
              />
              <SelectField
                label="Oferta"
                value={form.planId}
                onChange={(planId) => setForm((current) => ({ ...current, planId }))}
                options={[
                  { value: "", label: plans.isLoading ? "Carregando ofertas..." : "Sem oferta" },
                  ...(plans.data ?? []).map((plan) => ({
                    value: plan.id,
                    label: `${plan.name} — ${formatPlanPrice(plan.price, plan.currency)}`,
                  })),
                ]}
              />
            </div>
          </AdminFormSection>
        ) : null}

        {plans.error ? <ErrorNotice error={plans.error} /> : null}
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
            disabled={mutation.isPending}
            className="inline-flex items-center justify-center gap-2 rounded-xl border border-cyan-200/20 bg-gradient-to-r from-cyan-400 to-cyan-500 px-6 py-3 text-sm font-bold text-slate-950 shadow-[0_12px_32px_rgba(34,211,238,0.2)] transition-all hover:-translate-y-0.5 hover:from-cyan-300 hover:to-cyan-400 hover:shadow-[0_16px_38px_rgba(34,211,238,0.3)] disabled:translate-y-0 disabled:opacity-60"
          >
            {mutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Plus className="h-4 w-4" />
            )}
            {editing ? "Salvar alterações" : "Criar cliente"}
          </button>
        </AdminFormActions>
      </form>
    </AdminFormDialog>
  );
}

function HistoryDialog({ client, onClose }: { client: AdminClient; onClose: () => void }) {
  const history = useQuery({
    queryKey: ["admin", "clients", client.id, "history"],
    queryFn: async () => {
      const response = await adminClientHistory(client.id);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
  });
  return (
    <Modal title={`Histórico — ${client.name}`} onClose={onClose}>
      {history.isLoading ? (
        <Loader2 className="h-5 w-5 animate-spin text-primary" />
      ) : history.error ? (
        <ErrorNotice error={history.error} />
      ) : history.data?.length ? (
        <pre className="max-h-96 overflow-auto rounded-xl bg-background p-4 text-xs">
          {JSON.stringify(history.data, null, 2)}
        </pre>
      ) : (
        <p className="text-sm text-muted-foreground">Nenhuma operação registrada.</p>
      )}
    </Modal>
  );
}

function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-4">
      <section
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="max-h-[92vh] w-full max-w-3xl overflow-y-auto rounded-2xl border border-border bg-card p-5 shadow-2xl"
      >
        <header className="mb-5 flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button type="button" onClick={onClose} className="rounded-lg p-2 hover:bg-accent">
            <X className="h-4 w-4" />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}

function FormField({
  label,
  value,
  onChange,
  type = "text",
  required = true,
  hint,
  min,
  max,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  required?: boolean;
  hint?: string;
  min?: number;
  max?: number;
}) {
  return (
    <label className="grid gap-1.5 text-sm">
      <span className="text-xs font-semibold uppercase tracking-[0.08em] text-slate-300">
        {label}
      </span>
      <input
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        required={required}
        min={min}
        max={max}
        className={ADMIN_FIELD_CLASS}
      />
      {hint ? <span className="text-xs text-muted-foreground">{hint}</span> : null}
    </label>
  );
}

function SelectField({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="grid gap-1.5 text-sm">
      <span className="text-xs font-semibold uppercase tracking-[0.08em] text-slate-300">
        {label}
      </span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={ADMIN_FIELD_CLASS}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function ClientMeta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="truncate font-medium">{value}</dd>
    </div>
  );
}

function AccountBadge({ client }: { client: AdminClient }) {
  const label =
    client.approval_status === "pending" && !client.grant_access
      ? "Pedido"
      : client.deleted_at || !client.grant_access
        ? "Inativo"
        : client.account_type === "trial"
          ? "Teste grátis"
          : client.account_type === "marketing"
            ? "Marketing"
            : "Ativo";
  return (
    <span className="rounded-md bg-primary/10 px-2 py-1 text-xs font-semibold text-primary">
      {label}
    </span>
  );
}

function ActionButton({
  Icon,
  label,
  onClick,
  disabled,
  destructive = false,
}: {
  Icon: React.ComponentType<{ className?: string }>;
  label: string;
  onClick: () => void;
  disabled: boolean;
  destructive?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-semibold transition disabled:opacity-50 ${
        destructive
          ? "border-destructive/40 text-destructive hover:bg-destructive/10"
          : "border-border hover:bg-accent"
      }`}
    >
      <Icon className="h-3.5 w-3.5" />
      {label}
    </button>
  );
}

function ErrorNotice({ error }: { error: unknown }) {
  return (
    <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
      {error instanceof Error ? error.message : "Não foi possível concluir a operação."}
    </div>
  );
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return formatBrasiliaDate(date);
}

function formatPlanPrice(value: number, currency: string) {
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency,
  }).format(value);
}
