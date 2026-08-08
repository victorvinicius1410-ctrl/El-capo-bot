import { Link, useRouterState } from "@tanstack/react-router";
import { ShieldCheck, Users } from "lucide-react";

const tabs = [
  { to: "/admin/clientes" as const, label: "Acessos", Icon: Users },
  { to: "/admin/acessos" as const, label: "Administração", Icon: ShieldCheck },
];

/** Alterna entre clientes e administradores na seção de acessos. */
export function AdminAccessTabs() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  return (
    <nav aria-label="Seções de acessos" className="flex gap-2 rounded-2xl border border-border bg-card p-2">
      {tabs.map(({ to, label, Icon }) => (
        <Link key={to} to={to} className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold ${pathname === to ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent"}`}>
          <Icon className="h-4 w-4" />{label}
        </Link>
      ))}
    </nav>
  );
}
