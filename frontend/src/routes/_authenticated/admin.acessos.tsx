import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  KeyRound,
  Loader2,
  Pencil,
  Plus,
  ShieldCheck,
  Trash2,
  UserRound,
  UsersRound,
} from "lucide-react";
import { useMemo, useState } from "react";
import {
  ApiError,
  adminCreateAccess,
  adminDeleteAccess,
  adminListAccesses,
  adminUpdateAccess,
  type AdminAccessPayload,
  type AdminAccessRecord,
  type AdminPermission,
} from "@/lib/api";
import { adminDialogContent, validateAdminPassword } from "@/lib/adminPresentation";
import { meAccessQueryOptions } from "@/lib/meAccessQuery";
import { AdminAccessTabs } from "@/components/AdminAccessTabs";
import {
  ADMIN_FIELD_CLASS,
  AdminFormActions,
  AdminFormDialog,
  AdminFormSection,
} from "@/components/AdminFormDialog";

export const Route = createFileRoute("/_authenticated/admin/acessos")({
  head: () => ({ meta: [{ title: "Acessos — Administração ElCapo" }] }),
  component: AdminAccessPage,
});

const PERMISSION_OPTIONS: readonly { value: AdminPermission; label: string }[] = [
  { value: "clients.create", label: "Criar Cliente" },
  { value: "clients.update", label: "Editar Cliente" },
  { value: "clients.delete", label: "Excluir Cliente" },
  { value: "clients.history.read", label: "Ver Histórico" },
  { value: "clients.impersonate", label: "Acessar conta do cliente" },
  { value: "admins.create", label: "Criar Administrador" },
  { value: "admins.update", label: "Editar Administrador" },
  { value: "admins.delete", label: "Excluir Administrador" },
  { value: "finance.view", label: "Ver Financeiro" },
  { value: "finance.plans.manage", label: "Gerenciar Planos" },
  { value: "finance.settings.manage", label: "Gerenciar Configurações Financeiras" },
  { value: "finance.reconcile", label: "Executar Reconciliação" },
  { value: "webhooks.view", label: "Ver Webhooks" },
  { value: "webhooks.manage", label: "Gerenciar Destinos" },
  { value: "webhooks.replay", label: "Reenviar Entregas" },
  { value: "emails.view", label: "Ver Emails" },
  { value: "emails.manage", label: "Editar Templates de Email" },
];

const PERMISSION_GROUPS = [
  {
    title: "Clientes",
    description: "Cadastro, histórico e suporte",
    options: PERMISSION_OPTIONS.filter((option) => option.value.startsWith("clients.")),
  },
  {
    title: "Administração",
    description: "Equipe e níveis de acesso",
    options: PERMISSION_OPTIONS.filter((option) => option.value.startsWith("admins.")),
  },
  {
    title: "Financeiro",
    description: "Planos, métricas e integração",
    options: PERMISSION_OPTIONS.filter((option) => option.value.startsWith("finance.")),
  },
  {
    title: "Webhooks e API",
    description: "Destinos, contratos e reenvios",
    options: PERMISSION_OPTIONS.filter((option) => option.value.startsWith("webhooks.")),
  },
  {
    title: "Emails",
    description: "Layouts HTML e envios aos clientes",
    options: PERMISSION_OPTIONS.filter((option) => option.value.startsWith("emails.")),
  },
] as const;

function AdminAccessPage() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<AdminAccessRecord | "create" | null>(null);
  const access = useQuery(meAccessQueryOptions());
  const admins = useQuery({
    queryKey: ["admin", "accesses"],
    queryFn: async () => {
      const response = await adminListAccesses();
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    staleTime: 30_000,
  });
  const permissions = new Set(access.data?.permissions ?? []);
  const roles = useMemo(
    () =>
      Array.from(
        new Map((admins.data ?? []).map((admin) => [admin.role_id, admin.job_title])).entries(),
      ).map(([id, name]) => ({ id, name })),
    [admins.data],
  );
  const deleteMutation = useMutation({
    mutationFn: async (userId: string) => {
      const response = await adminDeleteAccess(userId);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["admin", "accesses"] }),
  });

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="page-title">Administração</h1>
          <p className="page-lead">Acessos, cargos e permissões administrativas.</p>
        </div>
        {permissions.has("admins.create") ? (
          <button
            type="button"
            onClick={() => setEditing("create")}
            className="page-cta inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold"
          >
            <Plus className="h-4 w-4" />
            Novo administrador
          </button>
        ) : null}
      </header>

      <AdminAccessTabs />

      {admins.error ? <ErrorNotice error={admins.error} /> : null}
      {deleteMutation.error ? <ErrorNotice error={deleteMutation.error} /> : null}

      {admins.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Carregando acessos...
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {(admins.data ?? []).map((admin) => (
            <article key={admin.id} className="page-surface p-5">
              <div className="flex items-start gap-3">
                <div className="rounded-xl border border-primary/30 bg-primary/10 p-3">
                  <ShieldCheck className="h-5 w-5 text-primary" />
                </div>
                <div className="min-w-0 flex-1">
                  <h2 className="font-semibold">{admin.name}</h2>
                  <p className="break-all text-sm text-muted-foreground">{admin.email}</p>
                  <p className="mt-1 text-xs font-semibold uppercase tracking-wide text-primary">
                    {admin.job_title || "Sem cargo"}
                  </p>
                </div>
              </div>
              <div className="mt-4 flex flex-wrap gap-1.5">
                {admin.permissions.map((permission) => (
                  <span key={permission} className="rounded-md bg-muted px-2 py-1 text-xs">
                    {permissionLabel(permission)}
                  </span>
                ))}
              </div>
              <div className="mt-5 flex gap-2 border-t border-border/60 pt-4">
                {permissions.has("admins.update") ? (
                  <button
                    type="button"
                    onClick={() => setEditing(admin)}
                    className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-semibold hover:bg-accent"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                    Editar
                  </button>
                ) : null}
                {permissions.has("admins.delete") ? (
                  <button
                    type="button"
                    disabled={deleteMutation.isPending}
                    onClick={() => {
                      if (window.confirm(`Desativar o administrador ${admin.name}?`)) {
                        deleteMutation.mutate(admin.id);
                      }
                    }}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-destructive/40 px-3 py-2 text-xs font-semibold text-destructive hover:bg-destructive/10"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    Excluir
                  </button>
                ) : null}
              </div>
            </article>
          ))}
        </div>
      )}

      {editing ? (
        <AccessDialog
          admin={editing === "create" ? null : editing}
          roles={roles}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void queryClient.invalidateQueries({ queryKey: ["admin", "accesses"] });
          }}
        />
      ) : null}
    </div>
  );
}

function AccessDialog({
  admin,
  roles,
  onClose,
  onSaved,
}: {
  admin: AdminAccessRecord | null;
  roles: { id: string; name: string }[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const editing = admin != null;
  const [form, setForm] = useState({
    name: admin?.name ?? "",
    email: admin?.email ?? "",
    jobTitle: admin?.job_title ?? "",
    password: "",
    permissions: new Set<AdminPermission>(admin?.permissions ?? []),
    manageAll: admin?.manageable_role_ids == null,
    manageableRoleIds: new Set(admin?.manageable_role_ids ?? []),
  });
  const mutation = useMutation({
    mutationFn: async () => {
      if (!editing || form.password) {
        const passwordError = validateAdminPassword(form.password);
        if (passwordError) throw new Error(passwordError);
      }
      const payload: AdminAccessPayload = {
        name: form.name.trim(),
        email: form.email.trim(),
        job_title: form.jobTitle.trim(),
        password: form.password,
        permissions: Array.from(form.permissions),
        manageable_role_ids: form.manageAll ? null : Array.from(form.manageableRoleIds),
      };
      const response = editing
        ? await adminUpdateAccess(admin.id, {
            ...payload,
            password: payload.password || undefined,
          })
        : await adminCreateAccess(payload);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    onSuccess: onSaved,
  });

  const dialogCopy = adminDialogContent("admin", editing);

  return (
    <AdminFormDialog {...dialogCopy} icon={KeyRound} onClose={onClose}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate();
        }}
        className="space-y-5"
      >
        <AdminFormSection
          icon={UserRound}
          step={1}
          title="Identidade e cargo"
          description="Dados de login e função exibida no controle de acessos."
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <Input
              label="Nome"
              value={form.name}
              onChange={(name) => setForm((current) => ({ ...current, name }))}
            />
            <Input
              label="Email"
              type="email"
              value={form.email}
              onChange={(email) => setForm((current) => ({ ...current, email }))}
            />
            <Input
              label="Cargo"
              value={form.jobTitle}
              onChange={(jobTitle) => setForm((current) => ({ ...current, jobTitle }))}
            />
            <Input
              label={editing ? "Nova senha (opcional)" : "Senha"}
              type="password"
              required={!editing}
              value={form.password}
              onChange={(password) => setForm((current) => ({ ...current, password }))}
            />
          </div>
        </AdminFormSection>

        <AdminFormSection
          icon={ShieldCheck}
          step={2}
          title="Permissões"
          description="Selecione somente as ações necessárias para este administrador."
        >
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {PERMISSION_GROUPS.map((group) => (
              <div
                key={group.title}
                className="rounded-2xl border border-slate-700/60 bg-slate-950/35 p-3.5"
              >
                <div className="mb-3 border-b border-white/8 px-1 pb-3">
                  <h4 className="text-sm font-semibold text-cyan-200">{group.title}</h4>
                  <p className="mt-0.5 text-[0.68rem] leading-relaxed text-slate-500">
                    {group.description}
                  </p>
                </div>
                <div className="space-y-2">
                  {group.options.map((option) => {
                    const selected = form.permissions.has(option.value);
                    return (
                      <label
                        key={option.value}
                        className={`group flex cursor-pointer items-center gap-2.5 rounded-xl border px-3 py-2.5 text-xs transition-all ${
                          selected
                            ? "border-cyan-300/35 bg-cyan-300/[0.09] text-cyan-50 shadow-[0_0_18px_rgba(37,219,224,0.06)]"
                            : "border-white/7 bg-white/[0.018] text-slate-400 hover:border-slate-600 hover:bg-white/[0.035]"
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={selected}
                          onChange={() =>
                            setForm((current) => {
                              const next = new Set(current.permissions);
                              if (next.has(option.value)) next.delete(option.value);
                              else next.add(option.value);
                              return { ...current, permissions: next };
                            })
                          }
                          className="h-3.5 w-3.5 shrink-0 rounded border-white/20 accent-cyan-300"
                        />
                        <span className="leading-snug">{option.label}</span>
                      </label>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </AdminFormSection>

        <AdminFormSection
          icon={UsersRound}
          step={3}
          title="Escopo de gestão"
          description="Controle quais cargos este administrador pode modificar."
        >
          <label
            className={`flex cursor-pointer items-center gap-3 rounded-xl border p-3.5 text-sm transition ${
              form.manageAll
                ? "border-primary/35 bg-primary/[0.08]"
                : "border-white/8 bg-slate-950/20 hover:border-white/15"
            }`}
          >
            <input
              type="checkbox"
              checked={form.manageAll}
              onChange={(event) =>
                setForm((current) => ({ ...current, manageAll: event.target.checked }))
              }
              className="h-4 w-4 rounded accent-primary"
            />
            Pode gerenciar todos os cargos
          </label>
          {!form.manageAll ? (
            <div className="mt-3 grid gap-2.5 sm:grid-cols-2">
              {roles.map((role) => {
                const selected = form.manageableRoleIds.has(role.id);
                return (
                  <label
                    key={role.id}
                    className={`flex cursor-pointer items-center gap-3 rounded-xl border p-3.5 text-sm transition ${
                      selected
                        ? "border-primary/35 bg-primary/[0.08]"
                        : "border-white/8 bg-slate-950/20 hover:border-white/15"
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={() =>
                        setForm((current) => {
                          const next = new Set(current.manageableRoleIds);
                          if (next.has(role.id)) next.delete(role.id);
                          else next.add(role.id);
                          return { ...current, manageableRoleIds: next };
                        })
                      }
                      className="h-4 w-4 rounded accent-primary"
                    />
                    {role.name}
                  </label>
                );
              })}
            </div>
          ) : null}
        </AdminFormSection>

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
            {editing ? "Salvar alterações" : "Criar administrador"}
          </button>
        </AdminFormActions>
      </form>
    </AdminFormDialog>
  );
}

function Input({
  label,
  value,
  onChange,
  type = "text",
  required = true,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  required?: boolean;
}) {
  return (
    <label className="grid gap-1.5 text-sm">
      <span className="text-xs font-semibold uppercase tracking-[0.08em] text-slate-300">
        {label}
      </span>
      <input
        type={type}
        required={required}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={ADMIN_FIELD_CLASS}
      />
    </label>
  );
}

function permissionLabel(permission: AdminPermission) {
  return PERMISSION_OPTIONS.find((option) => option.value === permission)?.label ?? permission;
}

function ErrorNotice({ error }: { error: unknown }) {
  return (
    <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
      {error instanceof Error ? error.message : "Não foi possível concluir a operação."}
    </div>
  );
}
