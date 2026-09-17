import { useEffect, useState, type FormEvent } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowLeft, Bot, KeyRound, Loader2, Mail, MailCheck, ShieldCheck } from "lucide-react";
import { requestPasswordRecovery } from "@/lib/api";

export const Route = createFileRoute("/forgot-password")({
  ssr: false,
  component: ForgotPasswordPage,
});

/** Segundos de espera entre envios: o Supabase tem rate limit proprio. */
const RESEND_COOLDOWN_SECONDS = 30;

function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setTimeout(() => setCooldown((current) => current - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [cooldown]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (cooldown > 0) return;
    const normalized = email.trim().toLowerCase();
    if (!normalized) {
      setError("Informe o email da sua conta.");
      return;
    }
    setSubmitting(true);
    setError(null);
    setMessage(null);
    const response = await requestPasswordRecovery(normalized);
    if (response.ok) {
      setMessage(response.data.message);
      setCooldown(RESEND_COOLDOWN_SECONDS);
    } else {
      setError(response.error);
    }
    setSubmitting(false);
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
            Recupere o acesso ao seu painel
          </h1>
          <p className="mx-auto mt-4 max-w-md text-pretty text-sm leading-relaxed text-muted-foreground sm:text-base lg:mx-0">
            Informe o email cadastrado e enviamos um link para você criar uma nova senha. O link vale
            por 1 hora e só pode ser usado uma vez.
          </p>
          <ul className="mt-8 hidden gap-4 text-left sm:grid sm:grid-cols-2 lg:max-w-lg">
            <li className="login-signal">
              <MailCheck className="h-4 w-4 text-primary" />
              <span>Link enviado por email</span>
            </li>
            <li className="login-signal">
              <ShieldCheck className="h-4 w-4 text-primary" />
              <span>Uso único e expirável</span>
            </li>
          </ul>
        </section>
        <section className="login-form-panel w-full max-w-md shrink-0 self-center">
          <div className="login-form-shell">
            <div className="mb-6">
              <p className="text-xs font-semibold uppercase tracking-[0.22em] text-primary/90">Recuperação</p>
              <h2 className="mt-2 text-2xl font-bold tracking-tight">Esqueci minha senha</h2>
              <p className="mt-1.5 text-sm text-muted-foreground">
                Enviamos as instruções para o email da sua conta.
              </p>
            </div>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label htmlFor="recovery-email" className="mb-1.5 block text-sm font-medium">
                  Email da conta
                </label>
                <div className="login-field">
                  <Mail className="login-field-icon" aria-hidden="true" />
                  <input
                    id="recovery-email"
                    type="email"
                    required
                    autoComplete="email"
                    autoFocus
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    placeholder="voce@email.com"
                    className="login-input"
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
              {message ? (
                <div
                  role="status"
                  className="rounded-xl border border-emerald-400/30 bg-emerald-400/10 px-3.5 py-3 text-sm text-emerald-200"
                >
                  {message}
                </div>
              ) : null}
              <button
                type="submit"
                disabled={submitting || cooldown > 0}
                className="login-submit"
              >
                {submitting ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Enviando...
                  </>
                ) : cooldown > 0 ? (
                  <>Reenviar em {cooldown}s</>
                ) : (
                  <>
                    <KeyRound className="h-4 w-4" aria-hidden="true" />
                    {message ? "Enviar novamente" : "Enviar link de recuperação"}
                  </>
                )}
              </button>
            </form>
            <p className="mt-5 text-center text-sm text-muted-foreground">
              <Link
                to="/login"
                className="inline-flex items-center gap-1.5 font-semibold text-primary hover:underline"
              >
                <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
                Voltar para o login
              </Link>
            </p>
            <p className="mt-3 text-center text-xs text-muted-foreground">
              Não recebeu? Confira a caixa de spam antes de reenviar.
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}
