import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Loader2, LockKeyhole, Mail } from "lucide-react";
import { useAuth } from "@/lib/useAuth";
import {
  apiConfig,
  logoutSession,
  updateAccountEmail,
  updateAccountPassword,
} from "@/lib/api";
import { resetBullExAccountState } from "@/hooks/useBullExAccount";

export const Route = createFileRoute("/_authenticated/settings")({
  head: () => ({ meta: [{ title: "Conta - ElCapo AutoBot" }] }),
  component: SettingsPage,
});

type Feedback = { type: "success" | "error"; text: string } | null;

function SettingsPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [emailPending, setEmailPending] = useState(false);
  const [passwordPending, setPasswordPending] = useState(false);
  const [emailMessage, setEmailMessage] = useState<Feedback>(null);
  const [passwordMessage, setPasswordMessage] = useState<Feedback>(null);

  useEffect(() => {
    setEmail(user?.email ?? "");
  }, [user?.email]);

  async function logout() {
    await logoutSession();
    resetBullExAccountState(user?.id);
    queryClient.clear();
    navigate({ to: "/login", replace: true });
  }

  async function updateEmail(event: React.FormEvent) {
    event.preventDefault();
    const nextEmail = email.trim();
    setEmailMessage(null);

    if (!nextEmail || nextEmail === user?.email) {
      setEmailMessage({ type: "error", text: "Informe um novo email." });
      return;
    }

    setEmailPending(true);
    const result = await updateAccountEmail(nextEmail);
    setEmailPending(false);
    setEmailMessage(
      result.ok
        ? {
            type: "success",
            text: "Solicitação enviada. Confira o novo email para confirmar a alteração.",
          }
        : { type: "error", text: result.error },
    );
  }

  async function updatePassword(event: React.FormEvent) {
    event.preventDefault();
    setPasswordMessage(null);

    if (newPassword.length < 8) {
      setPasswordMessage({ type: "error", text: "A senha deve ter pelo menos 8 caracteres." });
      return;
    }

    if (newPassword !== confirmPassword) {
      setPasswordMessage({ type: "error", text: "As senhas não coincidem." });
      return;
    }

    setPasswordPending(true);
    const result = await updateAccountPassword(newPassword);
    setPasswordPending(false);

    if (!result.ok) {
      setPasswordMessage({ type: "error", text: result.error });
      return;
    }

    setNewPassword("");
    setConfirmPassword("");
    setPasswordMessage({ type: "success", text: "Senha alterada com sucesso." });
  }

  return (
    <div className="max-w-2xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Conta</h1>
        <p className="text-sm text-muted-foreground">Sua conta e ambiente.</p>
      </header>

      <div className="space-y-5 rounded-2xl border border-border bg-card p-6">
        <h2 className="font-semibold">Conta</h2>
        <Field label="Email atual" value={user?.email ?? "-"} />
        <Field label="ID do usuário" value={user?.id ?? "-"} mono />

        <form onSubmit={updateEmail} className="space-y-3 border-t border-border pt-5">
          <div className="flex items-center gap-2 font-medium">
            <Mail className="h-4 w-4 text-primary" /> Alterar email
          </div>
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium">Novo email</span>
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
              required
              className="w-full rounded-lg border border-border bg-input px-3 py-2 outline-none focus:ring-2 focus:ring-ring/30"
            />
          </label>
          <FeedbackMessage feedback={emailMessage} />
          <SubmitButton pending={emailPending}>Alterar email</SubmitButton>
        </form>

        <form onSubmit={updatePassword} className="space-y-3 border-t border-border pt-5">
          <div className="flex items-center gap-2 font-medium">
            <LockKeyhole className="h-4 w-4 text-primary" /> Alterar senha
          </div>
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium">Nova senha</span>
            <input
              type="password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              autoComplete="new-password"
              minLength={6}
              required
              className="w-full rounded-lg border border-border bg-input px-3 py-2 outline-none focus:ring-2 focus:ring-ring/30"
            />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium">Confirmar nova senha</span>
            <input
              type="password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              autoComplete="new-password"
              minLength={6}
              required
              className="w-full rounded-lg border border-border bg-input px-3 py-2 outline-none focus:ring-2 focus:ring-ring/30"
            />
          </label>
          <FeedbackMessage feedback={passwordMessage} />
          <SubmitButton pending={passwordPending}>Alterar senha</SubmitButton>
        </form>

        <button
          type="button"
          onClick={logout}
          className="rounded-lg border border-border px-4 py-2 text-sm font-medium transition hover:bg-accent"
        >
          Sair
        </button>
      </div>

      <div className="space-y-4 rounded-2xl border border-border bg-card p-6">
        <h2 className="font-semibold">Backend</h2>
        <Field label="VITE_API_BASE_URL" value={apiConfig.BASE_URL || "não configurada"} mono />
        <Field
          label="VITE_PANEL_API_KEY"
          value={apiConfig.hasKey ? "configurada" : "não configurada"}
        />
        <p className="text-xs text-muted-foreground">
          Defina essas variáveis no ambiente para conectar o painel ao backend Bullex.
        </p>
      </div>
    </div>
  );
}

function FeedbackMessage({ feedback }: { feedback: Feedback }) {
  if (!feedback) return null;
  return (
    <p className={`text-sm ${feedback.type === "success" ? "text-success" : "text-destructive"}`}>
      {feedback.text}
    </p>
  );
}

function SubmitButton({ pending, children }: { pending: boolean; children: React.ReactNode }) {
  return (
    <button
      type="submit"
      disabled={pending}
      className="flex items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
    >
      {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
      {children}
    </button>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className={`break-all text-sm ${mono ? "font-mono" : ""}`}>{value}</div>
    </div>
  );
}
