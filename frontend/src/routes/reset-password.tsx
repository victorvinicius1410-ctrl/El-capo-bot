import { useEffect, useState, type FormEvent } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import {
  ArrowLeft,
  Bot,
  CircleCheck,
  Eye,
  EyeOff,
  Loader2,
  LockKeyhole,
  ShieldCheck,
} from "lucide-react";
import { createSessionFromTokens, updateAccountPassword } from "@/lib/api";

const PASSWORD_PATTERN = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$/;

export const Route = createFileRoute("/reset-password")({
  ssr: false,
  component: ResetPasswordPage,
});

type LinkState = "checking" | "ready" | "invalid";

/**
 * Traduz o erro que o Supabase devolve no proprio link.
 *
 * O link de recuperacao volta com `#error=...&error_code=...` quando ja foi
 * usado ou expirou; sem distinguir isso, todo problema virava a mesma frase.
 */
function describeLinkError(errorCode: string | null, rawError: string | null): string | null {
  if (errorCode === "otp_expired") {
    return "Este link expirou (vale por 1 hora) ou já foi utilizado. Peça um novo abaixo.";
  }
  if (errorCode === "access_denied" || rawError === "access_denied") {
    return "Este link não é mais válido. Peça um novo abaixo.";
  }
  if (errorCode || rawError) {
    return "Não foi possível validar este link. Peça um novo abaixo.";
  }
  return null;
}

function ResetPasswordPage() {
  const [linkState, setLinkState] = useState<LinkState>("checking");
  const [linkError, setLinkError] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function establish(): Promise<void> {
      const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
      const query = new URLSearchParams(window.location.search);
      const pick = (key: string) => hash.get(key) ?? query.get(key);

      const failure = describeLinkError(pick("error_code"), pick("error"));
      if (failure) {
        if (!cancelled) {
          setLinkError(failure);
          setLinkState("invalid");
        }
        return;
      }

      // Fluxo PKCE: o code_verifier fica no navegador que iniciou o pedido e
      // links da Auth Admin nunca criam um, entao nao ha o que trocar aqui.
      if (!pick("access_token") && query.get("code")) {
        if (!cancelled) {
          setLinkError(
            "Este formato de link não pode ser concluído aqui. Peça um novo link abaixo.",
          );
          setLinkState("invalid");
        }
        return;
      }

      const accessToken = pick("access_token");
      if (!accessToken) {
        if (!cancelled) setLinkState("invalid");
        return;
      }

      try {
        const response = await createSessionFromTokens({
          access_token: accessToken,
          refresh_token: pick("refresh_token"),
        });
        if (cancelled) return;
        if (!response.ok) {
          setLinkError(response.error);
          setLinkState("invalid");
          return;
        }
        window.history.replaceState(null, "", window.location.pathname);
        setLinkState("ready");
      } catch {
        if (!cancelled) {
          setLinkError("Não foi possível validar o link. Tente novamente.");
          setLinkState("invalid");
        }
      }
    }

    void establish();
    return () => {
      cancelled = true;
    };
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
    try {
      // Nada de revalidar a sessão aqui: createSessionFromTokens já limpou o
      // cache de identidade e revalidou. Um /auth/session que falhe de forma
      // transitória gravaria `null` no cache por 120s e travaria a troca.
      const response = await updateAccountPassword(password);
      if (!response.ok) {
        setError(
          response.code === "PASSWORD_POLICY"
            ? response.error
            : "O link expirou ou não foi possível redefinir a senha.",
        );
        return;
      }
      setDone(true);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="login-page relative min-h-screen overflow-hidden text-foreground">
      <div className="login-atmosphere" aria-hidden="true">
        <div className="login-orb login-orb-a" />
        <div className="login-orb login-orb-b" />
        <div className="login-grid" />
        <div className="login-scanline" />
      </div>
      <div className="relative z-10 mx-auto flex min-h-screen w-full max-w-6xl flex-col justify-center gap-10 px-4 py-10 sm:px-6 lg:flex-row lg:items-center lg:gap-16 lg:px-10">
        <section className="login-brand-panel flex-1 text-center lg:text-left">
          <div className="login-brand-mark mx-auto inline-flex items-center gap-3 lg:mx-0">
            <div className="login-logo-ring flex h-14 w-14 items-center justify-center">
              <Bot className="h-7 w-7 text-primary-foreground" />
            </div>
            <div className="text-left">
              <p className="login-brand-name text-3xl font-extrabold tracking-tight sm:text-4xl">ElCapo</p>
              <p className="text-xs font-medium uppercase tracking-[0.28em] text-primary/80">AutoBot</p>
            </div>
          </div>
          <h1 className="login-headline mt-8 text-balance text-3xl font-bold leading-tight sm:text-4xl lg:text-5xl">
            Defina a sua nova senha
          </h1>
          <p className="mx-auto mt-4 max-w-md text-pretty text-sm leading-relaxed text-muted-foreground sm:text-base lg:mx-0">
            Escolha uma senha com pelo menos 8 caracteres, uma maiúscula, uma minúscula e um número.
          </p>
          <ul className="mt-8 hidden gap-4 text-left sm:grid sm:grid-cols-2 lg:max-w-lg">
            <li className="login-signal">
              <ShieldCheck className="h-4 w-4 text-primary" />
              <span>Link de uso único</span>
            </li>
            <li className="login-signal">
              <LockKeyhole className="h-4 w-4 text-primary" />
              <span>Sessão protegida</span>
            </li>
          </ul>
        </section>
        <section className="login-form-panel w-full max-w-md shrink-0 self-center">
          <div className="login-form-shell">
            <div className="mb-6">
              <p className="text-xs font-semibold uppercase tracking-[0.22em] text-primary/90">Segurança</p>
              <h2 className="mt-2 text-2xl font-bold tracking-tight">Redefinir senha</h2>
              <p className="mt-1.5 text-sm text-muted-foreground">
                Você chegou aqui pelo link enviado ao seu email.
              </p>
            </div>

            {done ? (
              <div role="status">
                <CircleCheck className="h-8 w-8 text-emerald-300" />
                <p className="mt-3 text-sm text-muted-foreground">
                  Senha atualizada. Você já pode entrar com a nova credencial.
                </p>
                <Link to="/login" className="login-submit mt-5">
                  Ir para o login
                </Link>
              </div>
            ) : linkState === "checking" ? (
              <div
                role="status"
                className="flex items-center gap-2.5 rounded-xl border border-border px-3.5 py-3 text-sm text-muted-foreground"
              >
                <Loader2 className="h-4 w-4 animate-spin" />
                Validando o link de recuperação...
              </div>
            ) : linkState === "ready" ? (
              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label htmlFor="new-password" className="mb-1.5 block text-sm font-medium">
                    Nova senha
                  </label>
                  <div className="login-field">
                    <LockKeyhole className="login-field-icon" aria-hidden="true" />
                    <input
                      id="new-password"
                      type={showPassword ? "text" : "password"}
                      autoComplete="new-password"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      className="login-input login-input-password"
                      placeholder="••••••••"
                      required
                      minLength={8}
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword((current) => !current)}
                      className="login-password-toggle"
                      aria-label={showPassword ? "Ocultar senha" : "Mostrar senha"}
                    >
                      {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </div>
                <div>
                  <label htmlFor="confirm-password" className="mb-1.5 block text-sm font-medium">
                    Confirmar senha
                  </label>
                  <div className="login-field">
                    <LockKeyhole className="login-field-icon" aria-hidden="true" />
                    <input
                      id="confirm-password"
                      type={showPassword ? "text" : "password"}
                      autoComplete="new-password"
                      value={confirmPassword}
                      onChange={(event) => setConfirmPassword(event.target.value)}
                      className="login-input"
                      placeholder="••••••••"
                      required
                      minLength={8}
                    />
                  </div>
                </div>
                {error ? (
                  <div
                    role="alert"
                    className="rounded-xl border border-destructive/35 bg-destructive/10 px-3.5 py-3 text-sm text-destructive"
                  >
                    {error}
                  </div>
                ) : null}
                <button type="submit" disabled={saving} className="login-submit">
                  {saving ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Atualizando...
                    </>
                  ) : (
                    <>Definir nova senha</>
                  )}
                </button>
              </form>
            ) : (
              <div>
                <div
                  role="alert"
                  className="rounded-xl border border-destructive/35 bg-destructive/10 px-3.5 py-3 text-sm text-destructive"
                >
                  {linkError ??
                    "Abra esta página pelo link de recuperação recebido por email. O link pode ter expirado ou já ter sido utilizado."}
                </div>
                <Link to="/forgot-password" className="login-submit mt-5">
                  Pedir um novo link
                </Link>
              </div>
            )}

            <p className="mt-5 text-center text-sm text-muted-foreground">
              <Link
                to="/login"
                className="inline-flex items-center gap-1.5 font-semibold text-primary hover:underline"
              >
                <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
                Voltar para o login
              </Link>
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}
