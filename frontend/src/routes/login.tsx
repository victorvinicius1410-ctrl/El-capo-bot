import { useEffect, useState, type FormEvent } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Bot, Eye, EyeOff, Loader2, Lock, Mail, ShieldCheck, Zap } from "lucide-react";
import { loginWithPassword, requestPasswordRecovery } from "@/lib/api";
import { refreshAuthSession } from "@/lib/useAuth";

export const Route = createFileRoute("/login")({
  ssr: false,
  component: LoginPage,
});

function LoginPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [recovering, setRecovering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recoveryMessage, setRecoveryMessage] = useState<string | null>(null);

  useEffect(() => {
    refreshAuthSession().then((user) => {
      if (user) navigate({ to: "/dashboard", replace: true });
    });
  }, [navigate]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const response = await loginWithPassword(email, password);
      if (!response.ok) throw new Error(response.error);
      navigate({ to: "/dashboard", replace: true });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Erro ao autenticar");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRecovery(): Promise<void> {
    if (!email.trim()) {
      setError("Informe seu email antes de solicitar a recuperação.");
      return;
    }
    setRecovering(true);
    setError(null);
    setRecoveryMessage(null);
    const response = await requestPasswordRecovery(email.trim().toLowerCase());
    if (response.ok) setRecoveryMessage(response.data.message);
    else setError(response.error);
    setRecovering(false);
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
            Operações automáticas com precisão de máquina
          </h1>
          <p className="mx-auto mt-4 max-w-md text-pretty text-sm leading-relaxed text-muted-foreground sm:text-base lg:mx-0">
            Entre no painel e liberte o robô. Sinais, execução e controle em uma interface feita para
            quem opera no ritmo do mercado.
          </p>
          <ul className="mt-8 hidden gap-4 text-left sm:grid sm:grid-cols-3 lg:max-w-lg">
            <li className="login-signal">
              <Zap className="h-4 w-4 text-primary" />
              <span>Execução em tempo real</span>
            </li>
            <li className="login-signal">
              <ShieldCheck className="h-4 w-4 text-primary" />
              <span>Sessão protegida</span>
            </li>
            <li className="login-signal">
              <Bot className="h-4 w-4 text-primary" />
              <span>Robô sempre pronto</span>
            </li>
          </ul>
        </section>
        <section className="login-form-panel w-full max-w-md shrink-0 self-center">
          <div className="login-form-shell">
            <div className="mb-6">
              <p className="text-xs font-semibold uppercase tracking-[0.22em] text-primary/90">Acesso</p>
              <h2 className="mt-2 text-2xl font-bold tracking-tight">Entrar no painel</h2>
              <p className="mt-1.5 text-sm text-muted-foreground">Use suas credenciais para continuar.</p>
            </div>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label htmlFor="login-email" className="mb-1.5 block text-sm font-medium">
                  Email
                </label>
                <div className="login-field">
                  <Mail className="login-field-icon" aria-hidden="true" />
                  <input
                    id="login-email"
                    type="email"
                    required
                    autoComplete="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    placeholder="voce@email.com"
                    className="login-input"
                  />
                </div>
              </div>
              <div>
                <label htmlFor="login-password" className="mb-1.5 block text-sm font-medium">
                  Senha
                </label>
                <div className="login-field">
                  <Lock className="login-field-icon" aria-hidden="true" />
                  <input
                    id="login-password"
                    type={showPassword ? "text" : "password"}
                    required
                    minLength={8}
                    autoComplete="current-password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="••••••••"
                    className="login-input login-input-password"
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
              <button
                type="button"
                onClick={() => {
                  void handleRecovery();
                }}
                disabled={recovering}
                className="ml-auto flex text-xs font-semibold text-primary hover:underline disabled:opacity-60"
              >
                {recovering ? "Solicitando..." : "Esqueci minha senha"}
              </button>
              {error ? (
                <div
                  role="alert"
                  className="rounded-xl border border-destructive/35 bg-destructive/10 px-3.5 py-3 text-sm text-destructive"
                >
                  {error}
                </div>
              ) : null}
              {recoveryMessage ? (
                <div
                  role="status"
                  className="rounded-xl border border-emerald-400/30 bg-emerald-400/10 px-3.5 py-3 text-sm text-emerald-200"
                >
                  {recoveryMessage}
                </div>
              ) : null}
              <button type="submit" disabled={submitting} className="login-submit">
                {submitting ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Autenticando...
                  </>
                ) : (
                  <>
                    Entrar agora
                    <span className="login-submit-arrow" aria-hidden="true">
                      →
                    </span>
                  </>
                )}
              </button>
            </form>
            <p className="mt-5 text-center text-sm text-muted-foreground">
              Ainda não tem conta?{" "}
              <Link to="/register" className="font-semibold text-primary hover:underline">
                Cadastre-se
              </Link>
            </p>
            <p className="mt-3 text-center text-xs text-muted-foreground">
              Ambiente seguro · conexão criptografada
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}
