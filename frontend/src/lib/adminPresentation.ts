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
