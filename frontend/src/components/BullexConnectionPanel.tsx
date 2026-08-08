import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Loader2, Power, ShieldCheck, Wallet } from "lucide-react";
import { toast } from "sonner";
import { BullexLogo } from "./BullexLogo";
import { ApiError, bullexApi, type BullExAccount } from "@/lib/api";
import {
  BULLEX_STATUS_QUERY_KEY,
  ROBOT_STATE_QUERY_KEY,
  useLiveTradingData,
} from "@/hooks/useLiveTradingData";
import { BULLEX_ACCOUNT_QUERY_KEY } from "@/hooks/useBullExAccount";
import { getStoppedRobotState } from "@/lib/robotState";
import { markManualBullexDisconnect } from "@/lib/manualBullexDisconnect";
import {
  formatBullExBalance,
  isBullExConnected,
  isBullExStatusBackoff,
} from "@/lib/bullexConnection";

/**
 * Painel de conexão Bullex nas Configurações.
 *
 * Após o primeiro login bem-sucedido, email/senha ficam salvos criptografados
 * no backend para auto-reconexão (robô com tela fechada). A senha nunca volta
 * ao frontend.
 */
export function BullexConnectionPanel() {
  const { account, accountStatus, robotState, userId } = useLiveTradingData();
  const queryClient = useQueryClient();
  const [credentialsOpen, setCredentialsOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cachedGrace = robotState.data?.connection_status_source === "cached_grace";
  const statusBackoff = isBullExStatusBackoff(accountStatus.data);
  // Conta "fantasma": pill Conectado sem email/saldo (cache/grace pós-deploy).
  const metricsMissing =
    !account.data?.email?.trim() &&
    (account.data?.balance == null || !Number.isFinite(account.data.balance));
  const connected = isBullExConnected({
    account: account.data,
    accountStatus: accountStatus.data,
    cachedGrace,
  });
  // Só "sincronizando" quando ainda esperamos métricas de uma sessão viva.
  // BACKOFF / fetch sem conexão NÃO trava o formulário de login.
  const syncing =
    connected &&
    metricsMissing &&
    (account.isLoading || account.isFetching || statusBackoff);
  const email = account.data?.email?.trim() || "—";
  const balance = formatBullExBalance(account.data?.balance, account.data?.currency);
  const mode =
    account.data?.mode === "REAL" || account.data?.mode === "PRACTICE"
      ? account.data.mode
      : connected
        ? "REAL"
        : "—";
  const statusLabel = syncing && !connected ? "Sincronizando..." : connected ? "Conectado" : "Desconectado";
  const statusPillClass = connected
    ? "config-status-pill-ok"
    : syncing
      ? "config-status-pill-idle"
      : "config-status-pill-idle";

  const savedCredentials = useQuery({
    queryKey: ["bullex", "credentials"],
    queryFn: async () => {
      const response = await bullexApi.credentialsStatus();
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    staleTime: 15_000,
  });

  const credentialsSaved = Boolean(
    savedCredentials.data?.credentials_saved || account.data?.credentials_saved,
  );
  const savedEmail = savedCredentials.data?.email?.trim() || "";

  async function connect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    const response = await bullexApi.connect({
      email: String(form.get("email") ?? ""),
      password: String(form.get("password") ?? ""),
    });
    setPending(false);
    if (!response.ok) {
      setError(new ApiError(response.error, response.code, response.status).message);
      return;
    }
    setCredentialsOpen(false);
    toast.success("Conta Bullex conectada. Login salvo de forma segura para o robô.");
    await Promise.all([
      account.refetch(),
      accountStatus.refetch(),
      robotState.refetch(),
      savedCredentials.refetch(),
    ]);
  }

  async function reconnectSaved() {
    setPending(true);
    setError(null);
    const response = await bullexApi.reconnect();
    setPending(false);
    if (!response.ok) {
      setError(response.error);
      toast.error(response.error || "Não foi possível reconectar. Informe a senha novamente.");
      setCredentialsOpen(true);
      return;
    }
    toast.success("Sessão Bullex restaurada com as credenciais salvas.");
    await Promise.all([
      account.refetch(),
      accountStatus.refetch(),
      robotState.refetch(),
      savedCredentials.refetch(),
    ]);
  }

  async function disconnect() {
    setPending(true);
    setError(null);
    try {
      const response = await bullexApi.disconnect();
      if (!response.ok) {
        setError(response.error);
        toast.error(response.error || "Não foi possível desconectar a Bullex.");
        return;
      }
      // Impede auto-reconnect + preferStable de desfazer o clique.
      markManualBullexDisconnect();
      const disconnectedAccount: BullExAccount = {
        connected: false,
        balance: null,
        currency: null,
        mode: null,
        email: null,
        requires_2fa: false,
        status: "disconnected",
        credentials_saved: Boolean(
          (response.data as { credentials_saved?: boolean } | undefined)?.credentials_saved,
        ),
      };
      if (userId) {
        queryClient.setQueryData([...BULLEX_ACCOUNT_QUERY_KEY, userId], disconnectedAccount);
        queryClient.setQueryData([...BULLEX_STATUS_QUERY_KEY, userId], {
          status: "DISCONNECTED",
        });
        queryClient.setQueryData(
          [...ROBOT_STATE_QUERY_KEY, userId],
          getStoppedRobotState(true),
        );
      }
      toast.success("Conta Bullex desconectada.");
      void Promise.all([
        savedCredentials.refetch(),
        account.refetch(),
        accountStatus.refetch(),
        robotState.refetch(),
      ]).catch(() => {
        /* refetch em background — UI já está desconectada */
      });
    } finally {
      setPending(false);
    }
  }

  async function forgetCredentials() {
    setPending(true);
    setError(null);
    const response = await bullexApi.forgetCredentials();
    setPending(false);
    if (!response.ok) {
      setError(response.error);
      toast.error(response.error);
      return;
    }
    toast.success("Credenciais Bullex removidas.");
    await savedCredentials.refetch();
    queryClient.invalidateQueries({ queryKey: ["bullex"] });
  }

  return (
    <section className="config-panel-stack space-y-5">
      <header className="config-panel-head">
        <div className="config-brand-lockup">
          <div className="config-brand-plate" aria-hidden={false}>
            <BullexLogo className="config-brand-logo" />
          </div>
          <div className="min-w-0">
            <p className="config-eyebrow">Corretora parceira</p>
            <h2 className="config-brand-title">Conta Bullex</h2>
            <p className="config-brand-sub">
              Vincule a sessão uma vez — o login fica salvo criptografado para o robô operar
              mesmo com a tela fechada.
            </p>
          </div>
        </div>
        <span className={`config-status-pill ${statusPillClass}`}>
          <span className="config-status-dot" aria-hidden="true" />
          {statusLabel}
        </span>
      </header>

      <div className="config-surface space-y-5">
        <div className="config-metrics">
          <div className="config-metric">
            <p className="config-metric-label">Email</p>
            <p className="config-metric-value">{email !== "—" ? email : savedEmail || "—"}</p>
          </div>
          <div className="config-metric">
            <p className="config-metric-label">Saldo</p>
            <p className="config-metric-value flex items-center gap-2">
              <Wallet className="h-3.5 w-3.5 text-[#7ef0f3]" aria-hidden="true" />
              {balance}
            </p>
          </div>
          <div className="config-metric">
            <p className="config-metric-label">Modo</p>
            <p className="config-metric-value">{mode}</p>
          </div>
          <div className="config-metric">
            <p className="config-metric-label">Sessão</p>
            <p className="config-metric-value flex items-center gap-2">
              <ShieldCheck className="h-3.5 w-3.5 text-[#7ef0f3]" aria-hidden="true" />
              {connected ? "Pronta para o robô" : syncing ? "Sincronizando sessão" : "Aguardando login"}
            </p>
          </div>
        </div>

        <div
          className={`rounded-xl border px-4 py-3 text-sm ${
            credentialsSaved
              ? "border-[#7ef0f3]/35 bg-[#7ef0f3]/8 text-[#c9f7f8]"
              : "border-border bg-background/40 text-muted-foreground"
          }`}
        >
          <p className="flex items-center gap-2 font-semibold">
            <KeyRound className="h-4 w-4 shrink-0" />
            {credentialsSaved ? "Login salvo no servidor" : "Nenhum login salvo ainda"}
          </p>
          <p className="mt-1 text-xs opacity-90">
            {credentialsSaved
              ? "Email e senha ficam criptografados (AES-256-GCM). Ao entrar no painel ou se a sessão cair, o sistema reconecta sozinho."
              : "Ao conectar, o sistema grava as credenciais de forma segura para não pedir login toda hora."}
          </p>
        </div>

        {connected && metricsMissing ? (
          <p className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
            Sessão incompleta (sem email/saldo da corretora). Clique em{" "}
            <strong>Desconectar Bullex</strong> e conecte de novo com email e senha para
            restaurar as métricas.
          </p>
        ) : null}

        {connected ? (
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="config-btn-danger"
              disabled={pending}
              onClick={() => void disconnect()}
            >
              {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Power className="h-4 w-4" />}
              Desconectar Bullex
            </button>
            {credentialsSaved ? (
              <button
                type="button"
                className="config-btn-ghost"
                disabled={pending}
                onClick={() => void forgetCredentials()}
              >
                Esquecer credenciais salvas
              </button>
            ) : null}
          </div>
        ) : syncing ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Atualizando email e saldo da corretora…
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            {credentialsSaved ? (
              <button
                type="button"
                className="config-cta"
                disabled={pending}
                onClick={() => void reconnectSaved()}
              >
                {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                Reconectar com login salvo
              </button>
            ) : null}
            <button
              type="button"
              className={credentialsSaved ? "config-btn-ghost" : "config-cta"}
              onClick={() => setCredentialsOpen(true)}
            >
              {credentialsSaved ? "Entrar com outra conta" : "Entrar na Bullex"}
            </button>
            {credentialsSaved ? (
              <button
                type="button"
                className="config-btn-ghost"
                disabled={pending}
                onClick={() => void forgetCredentials()}
              >
                Esquecer credenciais
              </button>
            ) : null}
          </div>
        )}

        {error ? <p className="text-sm text-destructive">{error}</p> : null}
      </div>

      {credentialsOpen ? (
        <form onSubmit={connect} className="config-surface config-callout-accent space-y-3">
          <p className="config-eyebrow">Credenciais Bullex</p>
          <p className="text-xs text-muted-foreground">
            Após conectar, o login fica salvo criptografado no backend — não é preciso digitar de
            novo a cada sessão.
          </p>
          <label className="block text-xs font-semibold text-[#8fb0b8]">
            Email
            <input
              name="email"
              type="email"
              required
              autoComplete="username"
              defaultValue={savedEmail || (email !== "—" ? email : "")}
              placeholder="seu@email.com"
              className="config-field mt-1.5"
            />
          </label>
          <label className="block text-xs font-semibold text-[#8fb0b8]">
            Senha
            <input
              name="password"
              type="password"
              required
              autoComplete="current-password"
              placeholder="Senha da corretora"
              className="config-field mt-1.5"
            />
          </label>
          <div className="flex flex-wrap gap-2 pt-1">
            <button type="submit" className="config-cta" disabled={pending}>
              {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Conectar e salvar
            </button>
            <button
              type="button"
              className="config-btn-ghost"
              onClick={() => {
                setCredentialsOpen(false);
                setError(null);
              }}
            >
              Cancelar
            </button>
          </div>
        </form>
      ) : null}
    </section>
  );
}
