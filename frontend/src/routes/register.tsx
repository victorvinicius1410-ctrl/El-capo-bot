import { useEffect, useState, type FormEvent } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Bot, Eye, EyeOff, Loader2, Lock, Mail, Phone, ShieldCheck, UserRound, Zap } from "lucide-react";
import { registerWithPassword } from "@/lib/api";
import { refreshAuthSession } from "@/lib/useAuth";

export const Route = createFileRoute("/register")({
  ssr: false,
  component: RegisterPage,
});

function RegisterPage() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    refreshAuthSession().then((user) => {
      if (user) navigate({ to: "/payments", replace: true });
    });
  }, [navigate]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const response = await registerWithPassword({
        name,
        email,
        password,
        phone: phone.trim() || undefined,
      });
      if (!response.ok) throw new Error(response.error);
      navigate({ to: "/payments", replace: true });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Erro ao cadastrar");
    } finally {
      setSubmitting(false);
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
            Crie sua conta e aguarde a liberação
          </h1>
          <p className="mx-auto mt-4 max-w-md text-pretty text-sm leading-relaxed text-muted-foreground sm:text-base lg:mx-0">
            Após o cadastro, um administrador aprova seu acesso e define os dias disponíveis. Você
            também pode comprar um plano e liberar na hora.
          </p>
          <ul className="mt-8 hidden gap-4 text-left sm:grid sm:grid-cols-3 lg:max-w-lg">
            <li className="login-signal">
              <Zap className="h-4 w-4 text-primary" />
              <span>Cadastro rápido</span>
            </li>
            <li className="login-signal">
              <ShieldCheck className="h-4 w-4 text-primary" />
              <span>Aprovação segura</span>
            </li>
            <li className="login-signal">
              <Bot className="h-4 w-4 text-primary" />
              <span>Plano libera na hora</span>
            </li>
          </ul>
        </section>
        <section className="login-form-panel w-full max-w-md shrink-0 self-center">
          <div className="login-form-shell">
            <div className="mb-6">
              <p className="text-xs font-semibold uppercase tracking-[0.22em] text-primary/90">Cadastro</p>
              <h2 className="mt-2 text-2xl font-bold tracking-tight">Criar conta</h2>
              <p className="mt-1.5 text-sm text-muted-foreground">
                Já tem conta?{" "}
                <Link to="/login" className="font-semibold text-primary hover:underline">
                  Entrar
                </Link>
              </p>
            </div>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label htmlFor="register-name" className="mb-1.5 block text-sm font-medium">
                  Nome completo
                </label>
                <div className="login-field">
                  <UserRound className="login-field-icon" aria-hidden="true" />
                  <input
                    id="register-name"
                    type="text"
                    required
                    minLength={2}
                    maxLength={120}
                    autoComplete="name"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder="Seu nome"
                    className="login-input"
                  />
                </div>
              </div>
              <div>
                <label htmlFor="register-email" className="mb-1.5 block text-sm font-medium">
                  Email
                </label>
                <div className="login-field">
                  <Mail className="login-field-icon" aria-hidden="true" />
                  <input
                    id="register-email"
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
                <label htmlFor="register-phone" className="mb-1.5 block text-sm font-medium">
                  Telefone <span className="text-muted-foreground">(opcional)</span>
                </label>
                <div className="login-field">
                  <Phone className="login-field-icon" aria-hidden="true" />
                  <input
                    id="register-phone"
                    type="tel"
                    maxLength={32}
                    autoComplete="tel"
                    value={phone}
                    onChange={(event) => setPhone(event.target.value)}
                    placeholder="(11) 99999-9999"
                    className="login-input"
                  />
                </div>
              </div>
              <div>
                <label htmlFor="register-password" className="mb-1.5 block text-sm font-medium">
                  Senha
                </label>
                <div className="login-field">
                  <Lock className="login-field-icon" aria-hidden="true" />
                  <input
                    id="register-password"
                    type={showPassword ? "text" : "password"}
                    required
                    minLength={8}
                    autoComplete="new-password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="Mín. 8, maiúscula, minúscula e número"
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
              {error ? (
                <div
                  role="alert"
                  className="rounded-xl border border-destructive/35 bg-destructive/10 px-3.5 py-3 text-sm text-destructive"
                >
                  {error}
                </div>
              ) : null}
              <button type="submit" disabled={submitting} className="login-submit">
                {submitting ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Criando conta...
                  </>
                ) : (
                  <>
                    Cadastrar
                    <span className="login-submit-arrow" aria-hidden="true">
                      →
                    </span>
                  </>
                )}
              </button>
            </form>
            <p className="mt-5 text-center text-xs text-muted-foreground">
              Ao cadastrar, você poderá aguardar aprovação ou comprar um plano.
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}
