import { useEffect, useState, type FormEvent } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { CircleCheck, Loader2, LockKeyhole } from "lucide-react";
import { createSessionFromTokens, updateAccountPassword } from "@/lib/api";

const PASSWORD_PATTERN = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$/;

export const Route = createFileRoute("/reset-password")({
  ssr: false,
  component: ResetPasswordPage,
});

function ResetPasswordPage() {
  const [sessionReady, setSessionReady] = useState(false);
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const accessToken = params.get("access_token");
    const refreshToken = params.get("refresh_token");
    if (!accessToken) {
      setSessionReady(false);
      return;
    }
    createSessionFromTokens({ access_token: accessToken, refresh_token: refreshToken }).then(
      (response) => {
        setSessionReady(response.ok);
        if (response.ok) window.history.replaceState(null, "", window.location.pathname);
      },
    );
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);
    if (!PASSWORD_PATTERN.test(password)) {
      setError("Use 8+ caracteres, uma maiúscula, uma minúscula e um número.");
      return;
    }
    if (password !== confirmPassword) {
      setError("As senhas não coincidem.");
      return;
    }
    setSaving(true);
    const response = await updateAccountPassword(password);
    setSaving(false);
    if (!response.ok) {
      setError("O link expirou ou não foi possível redefinir a senha.");
      return;
    }
    setDone(true);
  }

  return (
    <main className="login-page flex min-h-screen items-center justify-center p-4">
      <section className="login-form-shell w-full max-w-md">
        <LockKeyhole className="h-9 w-9 text-primary" />
        <h1 className="mt-4 text-2xl font-bold">Redefinir senha</h1>
        {done ? (
          <div className="mt-6">
            <CircleCheck className="h-8 w-8 text-emerald-300" />
            <p className="mt-3 text-sm text-muted-foreground">
              Senha atualizada. Você já pode entrar com a nova credencial.
            </p>
            <Link to="/login" className="login-submit mt-5">
              Voltar ao login
            </Link>
          </div>
        ) : sessionReady ? (
          <form onSubmit={handleSubmit} className="mt-6 space-y-4">
            <label className="block text-sm font-medium">
              Nova senha
              <input
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="login-input mt-1.5"
                required
                minLength={8}
              />
            </label>
            <label className="block text-sm font-medium">
              Confirmar senha
              <input
                type="password"
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                className="login-input mt-1.5"
                required
                minLength={8}
              />
            </label>
            {error ? (
              <div
                role="alert"
                className="rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive"
              >
                {error}
              </div>
            ) : null}
            <button type="submit" disabled={saving} className="login-submit">
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              {saving ? "Atualizando..." : "Definir nova senha"}
            </button>
          </form>
        ) : (
          <div className="mt-6 rounded-xl border border-border p-4 text-sm text-muted-foreground">
            Abra esta página pelo link de recuperação recebido. O link pode ter expirado ou já ter
            sido utilizado.
          </div>
        )}
      </section>
    </main>
  );
}
