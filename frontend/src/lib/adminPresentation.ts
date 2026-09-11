import type { AccountType } from "./api";

export type AdminDialogKind = "client" | "admin" | "plan";

/** Retorna os textos do fluxo administrativo correspondente. */
export function adminDialogContent(kind: AdminDialogKind, editing: boolean) {
  const content = {
    client: {
      eyebrow: "Acessos · Clientes",
      noun: "cliente",
      description: "Configure identidade, acesso e assinatura em um único fluxo.",
      steps: ["Identificação", "Acesso", "Oferta e cobrança"],
    },
    admin: {
      eyebrow: "Acessos · Administração",
      noun: "administrador",
      description: "Defina identidade, cargo e permissões com segurança.",
      steps: ["Identidade e cargo", "Permissões", "Escopo de gestão"],
    },
    plan: {
      eyebrow: "Financeiro · Ofertas",
      noun: "oferta",
      description:
        "Organize cobrança e benefícios; a oferta é criada automaticamente no produto padrão da Cakto.",
      steps: ["Cobrança", "Apresentação", "Oferta Cakto"],
    },
  } as const;
  const selected = content[kind];
  const action = editing ? "Editar" : kind === "plan" ? "Nova" : "Novo";
  return { ...selected, title: `${action} ${selected.noun}` };
}

/** Valida a política mínima de senha do painel administrativo. */
export function validateAdminPassword(password: string): string | null {
  if (password.length < 8) return "A senha deve ter pelo menos 8 caracteres.";
  if (!/[A-Z]/.test(password)) return "A senha deve conter uma letra maiúscula.";
  if (!/[a-z]/.test(password)) return "A senha deve conter uma letra minúscula.";
  if (!/\d/.test(password)) return "A senha deve conter pelo menos um número.";
  return null;
}

/** Lista tipos de conta permitidos conforme o modo do formulário. */
export function accountTypeOptions(editing: boolean): AccountType[] {
  return editing ? ["trial", "client", "marketing"] : ["trial", "client"];
}

/** Informa se controles operacionais podem ser usados nesta sessão. */
export function canUseRobotControls(supportSession: boolean): boolean {
  return !supportSession;
}

export interface LiveTradingShellFlags {
  impersonating: boolean;
  isAdminRoute: boolean;
  accessReady: boolean;
  hasOperationalAccess: boolean;
}

/**
 * Decide se o `LiveTradingDataProvider` deve envolver o AppShell.
 *
 * Dashboard, Configurações e Histórico chamam `useLiveTradingData()` sempre que
 * a rota autenticada renderiza o `<Outlet />`. Sem o provider a página explode
 * com “precisa ser usado dentro de LiveTradingDataProvider” — inclusive quando
 * `/me/access` falha, o lead está inativo/pendente ou o admin está em sessão de
 * suporte (overlay oculto, mas o hook do dashboard continua montado).
 *
 * Por isso, fora de `/admin/*`, o provider **sempre** monta. Overlay e controles
 * do robô continuam gated por `shouldShowRobotOverlay` / `hasOperationalAccess`.
 *
 * Em `/admin/*` o provider permanece desligado (performance da navegação).
 */
export function shouldMountLiveTradingProvider(input: LiveTradingShellFlags): boolean {
  if (input.isAdminRoute) return false;
  return true;
}

/**
 * Overlay flutuante do robô: só com acesso operacional confirmado, fora de
 * `/admin/*` e fora da sessão de suporte (controles bloqueados).
 */
export function shouldShowRobotOverlay(input: {
  impersonating: boolean;
  isAdminRoute: boolean;
  hasOperationalAccess: boolean;
}): boolean {
  return input.hasOperationalAccess && !input.impersonating && !input.isAdminRoute;
}

/**
 * Overlay “assinatura inativa / aguardando aprovação”. Sessão de suporte
 * nunca entra aqui — o admin precisa ver o dashboard do lead mesmo sem
 * `grant_access`.
 */
export function shouldTreatSessionAsInactive(input: {
  impersonating: boolean;
  accessReady: boolean;
  hasOperationalAccess: boolean;
}): boolean {
  if (input.impersonating) return false;
  return input.accessReady && !input.hasOperationalAccess;
}

/**
 * Lista rotas visitáveis durante um acesso temporário de suporte
 * (impersonation). Fora do modo restrito, todas as rotas são permitidas.
 */
export function impersonationAllowedPaths(
  _isAdmin: boolean,
  _isOwner: boolean,
  restricted = false,
): string[] {
  return restricted ? ["/dashboard", "/configuracoes", "/history"] : ["*"];
}

/** Indica se o caminho pertence à seção Clientes/Acessos do admin. */
export function isAdminAccessSectionPath(pathname: string): boolean {
  return pathname === "/admin/clientes" || pathname === "/admin/acessos";
}
