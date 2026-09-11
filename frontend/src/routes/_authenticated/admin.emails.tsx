import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  CircleAlert,
  Loader2,
  Mail,
  RadioTower,
  Save,
  Send,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  ApiError,
  adminEmailDeliveries,
  adminEmailSettings,
  adminListEmailTemplates,
  adminPreviewEmailTemplate,
  adminSendTestEmail,
  adminUpdateEmailTemplate,
  type EmailDelivery,
  type EmailEventType,
  type EmailTemplate,
} from "@/lib/api";
import {
  EMAIL_EVENT_LABELS,
  EMAIL_TEMPLATE_VARIABLES,
  emailDeliveryErrorLabel,
  emailDeliveryStatusLabel,
  emailEventLabel,
  renderEmailPreview,
  sampleEmailVariables,
  validateEmailTemplateDraft,
} from "@/lib/emailPresentation";
import { formatBrasiliaDateTime } from "@/lib/brasiliaTime";
import { meAccessQueryOptions } from "@/lib/meAccessQuery";

const ALL_EMAIL_EVENTS = Object.keys(EMAIL_EVENT_LABELS) as EmailEventType[];

type EmailSection = "templates" | "entregas";

type EmailsSearch = {
  secao: EmailSection;
};

export const Route = createFileRoute("/_authenticated/admin/emails")({
  validateSearch: (search: Record<string, unknown>): EmailsSearch => ({
    secao: search.secao === "entregas" ? "entregas" : "templates",
  }),
  head: ({ match }) => ({
    meta: [
      {
        title: `${
          match.search.secao === "entregas" ? "Entregas de email" : "Emails"
        } — Administração ElCapo`,
      },
    ],
  }),
  component: EmailsAdminPage,
});

const SECTIONS = [
  { id: "templates" as const, label: "Templates HTML", Icon: Mail },
  { id: "entregas" as const, label: "Entregas", Icon: RadioTower },
];

function EmailsAdminPage() {
  const { secao } = Route.useSearch();
  const navigate = Route.useNavigate();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<EmailEventType>("purchase.completed");
  const [subject, setSubject] = useState("");
  const [htmlBody, setHtmlBody] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [testRecipient, setTestRecipient] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const access = useQuery(meAccessQueryOptions());

  const settings = useQuery({
    queryKey: ["admin", "emails", "settings"],
    queryFn: async () => {
      const response = await adminEmailSettings();
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    staleTime: 60_000,
  });

  const templates = useQuery({
    queryKey: ["admin", "emails", "templates"],
    enabled: secao === "templates",
    queryFn: async () => {
      const response = await adminListEmailTemplates();
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    staleTime: 30_000,
  });

  const deliveries = useQuery({
    queryKey: ["admin", "emails", "deliveries"],
    enabled: secao === "entregas",
    queryFn: async () => {
      const response = await adminEmailDeliveries();
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    staleTime: 30_000,
  });

  const current = useMemo(() => {
    const fromApi = templates.data?.find((item) => item.event_type === selected);
    if (fromApi) return fromApi;
    return {
      id: selected,
      event_type: selected,
      subject: "",
      html_body: "",
      is_enabled: false,
    } satisfies EmailTemplate;
  }, [templates.data, selected]);

  const eventList = useMemo(() => {
    const byType = new Map((templates.data ?? []).map((item) => [item.event_type, item]));
    const catalog =
      settings.data?.events && settings.data.events.length > 0
        ? settings.data.events
        : ALL_EMAIL_EVENTS;
    return catalog.map(
      (eventType) =>
        byType.get(eventType) ??
        ({
          id: eventType,
          event_type: eventType,
          subject: "",
          html_body: "",
          is_enabled: false,
        } satisfies EmailTemplate),
    );
  }, [templates.data, settings.data?.events]);

  useEffect(() => {
    if (!current) return;
    setSubject(current.subject);
    setHtmlBody(current.html_body);
    setEnabled(current.is_enabled);
    setFeedback(null);
    setError(null);
  }, [current?.id, current?.updated_at, current?.event_type, current?.subject, current?.html_body, current?.is_enabled]);

  const permissions = new Set(access.data?.permissions ?? []);
  const isAdmin = Boolean(access.data?.is_admin);
  const canView = permissions.has("emails.view") || isAdmin;
  const canManage = permissions.has("emails.manage") || isAdmin;

  const previewHtml = useMemo(
    () => renderEmailPreview(htmlBody, sampleEmailVariables()),
    [htmlBody],
  );
  const previewSubject = useMemo(
    () => renderEmailPreview(subject, sampleEmailVariables()),
    [subject],
  );

  const saveMutation = useMutation({
    mutationFn: async () => {
      const validation = validateEmailTemplateDraft({ subject, html_body: htmlBody });
      if (validation) throw new ApiError(validation, "VALIDATION_ERROR", 400);
      const response = await adminUpdateEmailTemplate(selected, {
        subject: subject.trim(),
        html_body: htmlBody.trim(),
        is_enabled: enabled,
      });
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    onSuccess: () => {
      setFeedback("Template salvo.");
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["admin", "emails", "templates"] });
    },
    onError: (err) => {
      setFeedback(null);
      setError(err instanceof ApiError ? err.message : "Falha ao salvar");
    },
  });

  const testMutation = useMutation({
    mutationFn: async () => {
      const recipient = testRecipient.trim();
      if (!recipient || !recipient.includes("@")) {
        throw new ApiError("Informe um e-mail válido para o teste.", "VALIDATION_ERROR", 400);
      }
      const response = await adminSendTestEmail(selected, recipient);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data as { status?: string } | undefined;
    },
    onSuccess: (data) => {
      const status = data?.status ?? "delivered";
      setFeedback(
        status === "delivered" || status === "sent"
          ? `Email de teste enviado para ${testRecipient.trim()}.`
          : `Email de teste processado (${status}) para ${testRecipient.trim()}.`,
      );
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["admin", "emails", "deliveries"] });
    },
    onError: (err) => {
      setFeedback(null);
      setError(err instanceof ApiError ? err.message : "Falha no envio de teste");
    },
  });

  const serverPreview = useMutation({
    mutationFn: async () => {
      const response = await adminPreviewEmailTemplate(selected, {
        subject,
        html_body: htmlBody,
      });
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
  });

  if (access.isLoading) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Carregando…
      </div>
    );
  }

  if (!canView) {
    return (
      <div className="rounded-2xl border border-border bg-card p-6 text-sm text-muted-foreground">
        Sem permissão para visualizar emails.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-primary/80">
          Administração
        </p>
        <h1 className="page-title text-3xl font-bold tracking-tight">Emails</h1>
        <p className="max-w-2xl text-sm text-muted-foreground">
          O servidor envia os e-mails necessários (compra, trial, cobrança, recuperação de
          senha). Aqui você define o HTML, o assunto e se cada tipo deve ser enviado. Use{" "}
          <code className="text-primary">{"{{variavel}}"}</code> para personalizar.
        </p>
        {settings.data && (
          <p className="text-xs text-muted-foreground">
            Provedor: {settings.data.provider}
            {settings.data.enabled ? " · envio ativo no servidor" : " · envio desativado no servidor"}
            {settings.data.from_configured
              ? " · remetente configurado"
              : " · configure PROD_EMAIL_FROM"}
            {settings.data.provider === "smtp"
              ? settings.data.smtp_configured
                ? " · SMTP Hostinger pronto"
                : " · configure SMTP no .env"
              : null}
          </p>
        )}
      </header>

      <nav className="flex flex-wrap gap-2 rounded-2xl border border-border bg-card p-2">
        {SECTIONS.map(({ id, label, Icon }) => {
          const active = secao === id;
          return (
            <button
              key={id}
              type="button"
              onClick={() => void navigate({ search: { secao: id } })}
              className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition ${
                active
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground"
              }`}
            >
              <Icon className="h-4 w-4" />
              {label}
            </button>
          );
        })}
      </nav>

      {secao === "entregas" ? (
        <DeliveriesPanel
          loading={deliveries.isLoading}
          items={deliveries.data ?? []}
          error={deliveries.error instanceof Error ? deliveries.error.message : null}
        />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[240px_minmax(0,1fr)]">
          <aside className="space-y-2 rounded-2xl border border-border bg-card p-3">
            {eventList.map((item) => (
              <TemplateNavButton
                key={item.event_type}
                template={item}
                active={selected === item.event_type}
                onClick={() => setSelected(item.event_type)}
              />
            ))}
            {templates.isLoading && (
              <div className="flex items-center gap-2 p-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" /> Carregando…
              </div>
            )}
            {templates.error ? (
              <p className="rounded-lg border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">
                {templates.error instanceof Error
                  ? templates.error.message
                  : "Falha ao carregar templates"}
              </p>
            ) : null}
          </aside>

          <section className="space-y-4 rounded-2xl border border-border bg-card p-5">
            {!canManage ? (
              <p className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-200">
                Seu usuário não tem permissão para editar e-mails. Peça{" "}
                <code>emails.manage</code> ou entre com a conta admin.
              </p>
            ) : null}
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold">{emailEventLabel(selected)}</h2>
                <p className="text-xs text-muted-foreground">{selected}</p>
              </div>
              <label className="inline-flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={enabled}
                  disabled={!canManage}
                  onChange={(event) => setEnabled(event.target.checked)}
                  className="size-4 accent-primary"
                />
                Enviar este email
              </label>
            </div>
            <label className="block space-y-1.5">
              <span className="text-sm font-medium">Assunto</span>
              <input
                value={subject}
                disabled={!canManage}
                onChange={(event) => setSubject(event.target.value)}
                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none ring-primary/30 focus:ring-2"
              />
            </label>

            <label className="block space-y-1.5">
              <span className="text-sm font-medium">HTML</span>
              <textarea
                value={htmlBody}
                disabled={!canManage}
                onChange={(event) => setHtmlBody(event.target.value)}
                rows={16}
                spellCheck={false}
                className="w-full rounded-xl border border-border bg-background px-3 py-2 font-mono text-xs leading-relaxed outline-none ring-primary/30 focus:ring-2"
              />
            </label>

            <div className="rounded-xl border border-border/80 bg-background/50 p-3">
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Variáveis disponíveis
              </p>
              <p className="mb-3 text-xs text-muted-foreground">
                Clique para copiar. O servidor substitui automaticamente no envio.
              </p>
              <div className="grid gap-2 sm:grid-cols-2">
                {(settings.data?.available_variables?.length
                  ? settings.data.available_variables.map((name) => ({
                      name,
                      description:
                        settings.data?.variable_descriptions?.[name] ??
                        EMAIL_TEMPLATE_VARIABLES.find((item) => item.name === name)
                          ?.description ??
                        name,
                    }))
                  : EMAIL_TEMPLATE_VARIABLES
                ).map((item) => (
                  <button
                    key={item.name}
                    type="button"
                    title="Copiar variável"
                    onClick={async () => {
                      await navigator.clipboard.writeText(`{{${item.name}}}`);
                      setFeedback(`Variável {{${item.name}}} copiada.`);
                    }}
                    className="rounded-lg border border-border px-3 py-2 text-left transition hover:border-primary/40 hover:bg-primary/5"
                  >
                    <code className="text-[11px] text-primary">{`{{${item.name}}}`}</code>
                    <p className="mt-1 text-[11px] text-muted-foreground">{item.description}</p>
                  </button>
                ))}
              </div>
            </div>

            {(feedback || error) && (
              <div
                className={`inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${
                  error
                    ? "border border-destructive/40 bg-destructive/10 text-destructive"
                    : "border border-primary/30 bg-primary/10 text-primary"
                }`}
              >
                {error ? <CircleAlert className="h-4 w-4" /> : <Check className="h-4 w-4" />}
                {error ?? feedback}
              </div>
            )}

            {canManage && (
              <div className="space-y-3">
                <label className="block max-w-md space-y-1.5">
                  <span className="text-sm font-medium">E-mail para teste</span>
                  <input
                    type="email"
                    value={testRecipient}
                    onChange={(event) => setTestRecipient(event.target.value)}
                    placeholder="ex.: voce@empresa.com"
                    autoComplete="email"
                    className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none ring-primary/30 focus:ring-2"
                  />
                </label>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => saveMutation.mutate()}
                    disabled={saveMutation.isPending}
                    className="page-cta inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold"
                  >
                    {saveMutation.isPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Save className="h-4 w-4" />
                    )}
                    Salvar
                  </button>
                  <button
                    type="button"
                    onClick={() => testMutation.mutate()}
                    disabled={
                      testMutation.isPending ||
                      !settings.data?.enabled ||
                      !testRecipient.trim()
                    }
                    className="inline-flex items-center gap-2 rounded-xl border border-border px-4 py-2 text-sm font-semibold hover:bg-accent disabled:opacity-50"
                  >
                    {testMutation.isPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Send className="h-4 w-4" />
                    )}
                    Enviar teste
                  </button>
                  <button
                    type="button"
                    onClick={() => serverPreview.mutate()}
                    disabled={serverPreview.isPending}
                    className="inline-flex items-center gap-2 rounded-xl border border-border px-4 py-2 text-sm font-semibold hover:bg-accent"
                  >
                    Preview no servidor
                  </button>
                </div>
              </div>
            )}

            <div className="space-y-2">
              <p className="text-sm font-medium">Preview</p>
              <p className="text-xs text-muted-foreground">
                Assunto: {serverPreview.data?.subject ?? previewSubject}
              </p>
              <iframe
                title="Preview do email"
                sandbox=""
                srcDoc={serverPreview.data?.html_body ?? previewHtml}
                className="h-[420px] w-full rounded-xl border border-border bg-white"
              />
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

function TemplateNavButton({
  template,
  active,
  onClick,
}: {
  template: EmailTemplate;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full flex-col rounded-xl px-3 py-2 text-left transition ${
        active ? "bg-primary text-primary-foreground" : "hover:bg-accent"
      }`}
    >
      <span className="text-sm font-semibold">
        {emailEventLabel(template.event_type)}
      </span>
      <span className={`text-[11px] ${active ? "opacity-90" : "text-muted-foreground"}`}>
        {template.is_enabled ? "Ativo" : "Desativado"}
      </span>
    </button>
  );
}

function DeliveriesPanel({
  loading,
  items,
  error,
}: {
  loading: boolean;
  items: EmailDelivery[];
  error: string | null;
}) {
  if (loading) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Carregando entregas…
      </div>
    );
  }
  if (error) {
    return <p className="text-sm text-destructive">{error}</p>;
  }
  if (items.length === 0) {
    return (
      <div className="rounded-2xl border border-border bg-card p-6 text-sm text-muted-foreground">
        Nenhuma entrega registrada ainda.
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-card">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="px-4 py-3">Evento</th>
            <th className="px-4 py-3">Assunto</th>
            <th className="px-4 py-3">Status</th>
            <th className="px-4 py-3">Quando</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id} className="border-b border-border/60 last:border-0">
              <td className="px-4 py-3">{emailEventLabel(item.event_type)}</td>
              <td className="px-4 py-3">{item.subject}</td>
              <td className="px-4 py-3">
                <div className="flex flex-col gap-0.5">
                  <span>{emailDeliveryStatusLabel(item.status)}</span>
                  {emailDeliveryErrorLabel(item.last_error_code ?? item.error) ? (
                    <span className="text-[11px] text-muted-foreground">
                      {emailDeliveryErrorLabel(item.last_error_code ?? item.error)}
                    </span>
                  ) : null}
                </div>
              </td>
              <td className="px-4 py-3 text-muted-foreground">
                {formatBrasiliaDateTime(item.created_at) || item.created_at}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
