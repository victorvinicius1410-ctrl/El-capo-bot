import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Braces,
  Check,
  CircleAlert,
  Clipboard,
  Clock3,
  Loader2,
  Plus,
  RadioTower,
  RefreshCw,
  ShieldCheck,
  Trash2,
  Webhook,
} from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import {
  ADMIN_FIELD_CLASS,
  AdminFormActions,
  AdminFormDialog,
  AdminFormSection,
} from "@/components/AdminFormDialog";
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
  adminCreateWebhook,
  adminDeleteWebhook,
  adminListWebhooks,
  adminReplayWebhookDelivery,
  adminWebhookCatalog,
  adminWebhookDeliveries,
  type CreatedWebhookEndpoint,
  type OutgoingWebhookEvent,
  type WebhookCatalogItem,
  type WebhookEndpoint,
} from "@/lib/api";
import {
  OUTGOING_WEBHOOK_EVENTS,
  WEBHOOK_EVENT_LABELS,
  deliveryStatusLabel,
  normalizeWebhookCollections,
  validateWebhookDraft,
} from "@/lib/webhookPresentation";
import { formatBrasiliaDateTime } from "@/lib/brasiliaTime";
import { meAccessQueryOptions } from "@/lib/meAccessQuery";

type WebhookSection = "destinos" | "api" | "entregas";

type WebhooksSearch = {
  secao: WebhookSection;
};

export const Route = createFileRoute("/_authenticated/admin/webhooks-api")({
  validateSearch: (search: Record<string, unknown>): WebhooksSearch => ({
    secao: search.secao === "api" || search.secao === "entregas" ? search.secao : "destinos",
  }),
  head: ({ match }) => ({
    meta: [
      {
        title: `${
          match.search.secao === "api"
            ? "Contratos JSON"
            : match.search.secao === "entregas"
              ? "Entregas"
              : "Webhooks"
        } — Administração ElCapo`,
      },
    ],
  }),
  component: WebhooksApiPage,
});

const SECTIONS = [
  { id: "destinos" as const, label: "Destinos", Icon: Webhook },
  { id: "api" as const, label: "Eventos e API", Icon: Braces },
  { id: "entregas" as const, label: "Entregas", Icon: RadioTower },
];

function WebhooksApiPage() {
  const { secao } = Route.useSearch();
  const navigate = Route.useNavigate();
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [created, setCreated] = useState<CreatedWebhookEndpoint | null>(null);

  const access = useQuery(meAccessQueryOptions());
  const endpointsQuery = useQuery({
    queryKey: ["admin", "webhooks"],
    enabled: secao === "destinos" || secao === "entregas",
    queryFn: async () => {
      const response = await adminListWebhooks();
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    staleTime: 30_000,
  });
  const catalogQuery = useQuery({
    queryKey: ["admin", "webhooks", "catalog"],
    enabled: secao === "api",
    queryFn: async () => {
      const response = await adminWebhookCatalog();
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    staleTime: 60_000,
  });
  const deliveriesQuery = useQuery({
    queryKey: ["admin", "webhook-deliveries"],
    enabled: secao === "entregas",
    queryFn: async () => {
      const response = await adminWebhookDeliveries();
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    staleTime: 30_000,
  });
  const deactivate = useMutation({
    mutationFn: async (endpointId: string) => {
      const response = await adminDeleteWebhook(endpointId);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["admin", "webhooks"] }),
  });
  const replay = useMutation({
    mutationFn: async (deliveryId: string) => {
      const response = await adminReplayWebhookDelivery(deliveryId);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ["admin", "webhook-deliveries"] }),
  });
  const collections = normalizeWebhookCollections(endpointsQuery.data, deliveriesQuery.data);
  const canManage = access.data?.permissions.includes("webhooks.manage") === true;
  const canReplay = access.data?.permissions.includes("webhooks.replay") === true;
  const error =
    endpointsQuery.error ??
    catalogQuery.error ??
    deliveriesQuery.error ??
    deactivate.error ??
    replay.error;
  const sectionLoading =
    secao === "destinos"
      ? endpointsQuery.isLoading
      : secao === "api"
        ? catalogQuery.isLoading
        : endpointsQuery.isLoading || deliveriesQuery.isLoading;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="mb-2 text-xs font-bold uppercase tracking-[0.22em] text-cyan-300/80">
            Integrações
          </p>
          <h1 className="page-title flex items-center gap-2">
            <Webhook className="h-6 w-6 text-primary" />
            Webhooks e API
          </h1>
          <p className="page-lead max-w-3xl">
            Envie eventos assinados para automações de email, CRM e atendimento sem expor
            credenciais ou permitir acesso entre empresas.
          </p>
        </div>
        {canManage && secao === "destinos" ? (
          <button
            type="button"
            onClick={() => setShowCreate(true)}
            className="page-cta inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold"
          >
            <Plus className="h-4 w-4" />
            Novo destino
          </button>
        ) : null}
      </header>

      <nav className="flex flex-wrap gap-2 rounded-2xl border border-border bg-card/80 p-2">
        {SECTIONS.map(({ id, label, Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => void navigate({ search: { secao: id }, replace: true })}
            className={`inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold transition ${
              secao === id
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-accent hover:text-foreground"
            }`}
          >
            <Icon className="h-4 w-4" />
            {label}
          </button>
        ))}
      </nav>

      {error ? <ErrorNotice error={error} /> : null}
      {sectionLoading ? (
        <div className="page-surface flex items-center gap-3 p-5 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin text-primary" />
          Carregando integrações...
        </div>
      ) : secao === "destinos" ? (
        <EndpointsSection
          endpoints={collections.endpoints}
          canManage={canManage}
          busyId={deactivate.isPending ? deactivate.variables : undefined}
          onDeactivate={(id) => {
            if (window.confirm("Desativar este destino? O histórico será preservado.")) {
              deactivate.mutate(id);
            }
          }}
        />
      ) : secao === "api" ? (
        <CatalogSection catalog={catalogQuery.data ?? []} />
      ) : (
        <DeliveriesSection
          deliveries={collections.deliveries}
          canReplay={canReplay}
          busyId={replay.isPending ? replay.variables : undefined}
          onReplay={(id) => replay.mutate(id)}
        />
      )}

      {showCreate ? (
        <CreateEndpointDialog
          onClose={() => setShowCreate(false)}
          onCreated={(endpoint) => {
            setCreated(endpoint);
            setShowCreate(false);
            void queryClient.invalidateQueries({ queryKey: ["admin", "webhooks"] });
          }}
        />
      ) : null}
      {created ? <SecretDialog endpoint={created} onClose={() => setCreated(null)} /> : null}
    </div>
  );
}

function EndpointsSection({
  endpoints,
  canManage,
  busyId,
  onDeactivate,
}: {
  endpoints: WebhookEndpoint[];
  canManage: boolean;
  busyId?: string;
  onDeactivate: (id: string) => void;
}) {
  if (!endpoints.length) {
    return (
      <div className="page-surface p-10 text-center">
        <Webhook className="mx-auto h-8 w-8 text-primary" />
        <h2 className="mt-3 font-semibold">Nenhum destino configurado</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Cadastre uma plataforma HTTPS e escolha quais eventos ela receberá.
        </p>
      </div>
    );
  }
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {endpoints.map((endpoint) => (
        <article key={endpoint.id} className="page-surface p-5">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="font-semibold">{endpoint.name}</h2>
              <p className="mt-1 break-all text-xs text-muted-foreground">{endpoint.url}</p>
            </div>
            <span
              className={`rounded-md px-2 py-1 text-xs font-semibold ${
                endpoint.is_active
                  ? "bg-emerald-400/10 text-emerald-300"
                  : "bg-slate-400/10 text-slate-400"
              }`}
            >
              {endpoint.is_active ? "Ativo" : "Inativo"}
            </span>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {endpoint.subscribed_events.map((event) => (
              <span
                key={event}
                className="rounded-md border border-cyan-300/15 bg-cyan-300/[0.05] px-2 py-1 text-xs text-cyan-100/80"
              >
                {WEBHOOK_EVENT_LABELS[event]}
              </span>
            ))}
          </div>
          {canManage && endpoint.is_active ? (
            <button
              type="button"
              disabled={busyId === endpoint.id}
              onClick={() => onDeactivate(endpoint.id)}
              className="mt-5 inline-flex items-center gap-2 rounded-lg border border-destructive/30 px-3 py-2 text-xs font-semibold text-destructive hover:bg-destructive/10"
            >
              {busyId === endpoint.id ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Trash2 className="h-3.5 w-3.5" />
              )}
              Desativar
            </button>
          ) : null}
        </article>
      ))}
    </div>
  );
}

function CatalogSection({ catalog }: { catalog: WebhookCatalogItem[] }) {
  return (
    <div className="space-y-4">
      <div className="page-surface grid gap-4 p-5 md:grid-cols-3">
        <Info icon={ShieldCheck} title="Assinatura" text="HMAC-SHA256 do timestamp + corpo bruto" />
        <Info icon={Clock3} title="Retentativas" text="Backoff exponencial, até 6 tentativas" />
        <Info icon={RadioTower} title="Idempotência" text="event_id estável em todos os reenvios" />
      </div>
      {catalog.map((item) => (
        <article key={item.event_type} className="page-surface overflow-hidden">
          <header className="border-b border-border px-5 py-4">
            <h2 className="font-mono text-sm font-semibold text-cyan-300">{item.event_type}</h2>
            <p className="mt-1 text-sm text-muted-foreground">{item.description}</p>
          </header>
          <pre className="max-h-[28rem] overflow-auto bg-black/25 p-5 text-xs leading-relaxed text-slate-300">
            {JSON.stringify(item.example, null, 2)}
          </pre>
        </article>
      ))}
    </div>
  );
}

function DeliveriesSection({
  deliveries,
  canReplay,
  busyId,
  onReplay,
}: {
  deliveries: Array<{
    id: string;
    event_id: string;
    endpoint_id: string;
    status: string;
    attempt_count: number;
    response_status: number | null;
    latency_ms: number | null;
    created_at: string;
  }>;
  canReplay: boolean;
  busyId?: string;
  onReplay: (id: string) => void;
}) {
  if (!deliveries.length) {
    return (
      <div className="page-surface p-10 text-center text-sm text-muted-foreground">
        Nenhuma entrega registrada.
      </div>
    );
  }
  return (
    <div className="page-surface overflow-hidden">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Evento</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Tentativas</TableHead>
            <TableHead>HTTP</TableHead>
            <TableHead>Latência</TableHead>
            <TableHead>Data</TableHead>
            <TableHead className="text-right">Ação</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {deliveries.map((delivery) => (
            <TableRow key={delivery.id}>
              <TableCell className="max-w-36 truncate font-mono text-xs">
                {delivery.event_id}
              </TableCell>
              <TableCell>{deliveryStatusLabel(delivery.status)}</TableCell>
              <TableCell>{delivery.attempt_count}</TableCell>
              <TableCell>{delivery.response_status ?? "—"}</TableCell>
              <TableCell>
                {delivery.latency_ms == null ? "—" : `${delivery.latency_ms} ms`}
              </TableCell>
              <TableCell>{formatBrasiliaDateTime(delivery.created_at) || delivery.created_at}</TableCell>
              <TableCell className="text-right">
                {canReplay && delivery.status !== "delivered" ? (
                  <button
                    type="button"
                    onClick={() => onReplay(delivery.id)}
                    disabled={busyId === delivery.id}
                    className="inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-semibold"
                  >
                    <RefreshCw
                      className={`h-3.5 w-3.5 ${busyId === delivery.id ? "animate-spin" : ""}`}
                    />
                    Reenviar
                  </button>
                ) : null}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function CreateEndpointDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (endpoint: CreatedWebhookEndpoint) => void;
}) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<OutgoingWebhookEvent[]>([
    "purchase.completed",
    "user.password_recovery_requested",
  ]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const mutation = useMutation({
    mutationFn: async () => {
      const validation = validateWebhookDraft({
        name,
        url,
        subscribed_events: events,
      });
      if (Object.keys(validation).length) {
        setErrors(validation);
        throw new Error("Revise os campos destacados.");
      }
      const response = await adminCreateWebhook({
        name: name.trim(),
        url: url.trim(),
        subscribed_events: events,
      });
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    onSuccess: onCreated,
  });
  return (
    <AdminFormDialog
      eyebrow="Novo destino"
      title="Configurar webhook"
      description="Cadastre uma URL HTTPS pública e selecione somente os eventos necessários."
      steps={["Identificação", "Eventos", "Segredo"]}
      icon={Webhook}
      onClose={onClose}
    >
      <div className="space-y-4 pb-20">
        <AdminFormSection
          title="Identificação"
          description="O servidor valida HTTPS, DNS e redes privadas antes de entregar."
          icon={RadioTower}
          step={1}
        >
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="Nome" error={errors.name}>
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                className={ADMIN_FIELD_CLASS}
                placeholder="Automação de email"
              />
            </Field>
            <Field label="URL HTTPS" error={errors.url}>
              <input
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                className={ADMIN_FIELD_CLASS}
                placeholder="https://hooks.exemplo.com/elcapo"
              />
            </Field>
          </div>
        </AdminFormSection>
        <AdminFormSection
          title="Eventos"
          description="Cada destino recebe somente os contratos selecionados."
          icon={Braces}
          step={2}
        >
          <div className="grid gap-2 sm:grid-cols-2">
            {OUTGOING_WEBHOOK_EVENTS.map((eventName) => {
              const checked = events.includes(eventName);
              return (
                <label
                  key={eventName}
                  className={`flex cursor-pointer items-start gap-3 rounded-xl border p-3 text-sm ${
                    checked ? "border-cyan-300/35 bg-cyan-300/[0.07]" : "border-slate-700"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() =>
                      setEvents((current) =>
                        checked
                          ? current.filter((item) => item !== eventName)
                          : [...current, eventName],
                      )
                    }
                    className="mt-0.5"
                  />
                  <span>
                    <strong className="block text-slate-100">
                      {WEBHOOK_EVENT_LABELS[eventName]}
                    </strong>
                    <code className="text-[11px] text-slate-500">{eventName}</code>
                  </span>
                </label>
              );
            })}
          </div>
          {errors.subscribed_events ? (
            <p className="mt-2 text-xs text-destructive">{errors.subscribed_events}</p>
          ) : null}
        </AdminFormSection>
        {mutation.error ? <ErrorNotice error={mutation.error} /> : null}
        <AdminFormActions>
          <button type="button" onClick={onClose} className="rounded-lg border px-4 py-2 text-sm">
            Cancelar
          </button>
          <button
            type="button"
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
            className="page-cta inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold"
          >
            {mutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Plus className="h-4 w-4" />
            )}
            Criar destino
          </button>
        </AdminFormActions>
      </div>
    </AdminFormDialog>
  );
}

function SecretDialog({
  endpoint,
  onClose,
}: {
  endpoint: CreatedWebhookEndpoint;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/80 p-4 backdrop-blur-lg">
      <section
        role="dialog"
        aria-modal="true"
        className="w-full max-w-xl rounded-2xl border border-cyan-300/25 bg-[#071116] p-6 shadow-2xl"
      >
        <ShieldCheck className="h-8 w-8 text-cyan-300" />
        <h2 className="mt-4 text-xl font-semibold">Guarde o segredo agora</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Ele não será exibido novamente. Use-o para validar o header X-Webhook-Signature.
        </p>
        <code className="mt-5 block break-all rounded-xl border border-white/10 bg-black/30 p-4 text-sm text-cyan-200">
          {endpoint.signing_secret}
        </code>
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button
            type="button"
            onClick={async () => {
              await navigator.clipboard.writeText(endpoint.signing_secret);
              setCopied(true);
            }}
            className="inline-flex items-center gap-2 rounded-lg border px-4 py-2 text-sm font-semibold"
          >
            {copied ? <Check className="h-4 w-4" /> : <Clipboard className="h-4 w-4" />}
            {copied ? "Copiado" : "Copiar"}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="page-cta rounded-lg px-4 py-2 text-sm font-semibold"
          >
            Concluído
          </button>
        </div>
      </section>
    </div>
  );
}

function Field({ label, error, children }: { label: string; error?: string; children: ReactNode }) {
  return (
    <label className="space-y-1.5 text-sm font-medium text-slate-200">
      <span>{label}</span>
      {children}
      {error ? <span className="block text-xs text-destructive">{error}</span> : null}
    </label>
  );
}

function Info({ icon: Icon, title, text }: { icon: LucideIcon; title: string; text: string }) {
  return (
    <div className="flex items-start gap-3">
      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
      <div>
        <h2 className="text-sm font-semibold">{title}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{text}</p>
      </div>
    </div>
  );
}

function ErrorNotice({ error }: { error: unknown }) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
      <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />
      <span>
        {error instanceof Error ? error.message : "Não foi possível concluir a operação."}
      </span>
    </div>
  );
}
