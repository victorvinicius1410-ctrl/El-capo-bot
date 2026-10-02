import { Calculator } from "lucide-react";
import { ResultPill } from "@/components/ConsistentManagement";
import { formatBullExBalance, formatProfitAmount } from "@/lib/bullexConnection";
import type { MasanielloCycle } from "@/lib/masaniello";
import {
  CONSISTENT_MANAGEMENT_LABEL,
  masanielloCycleBalance,
  masanielloCycleHeadline,
  masanielloRowNote,
} from "@/lib/masanielloPresentation";

interface CalculatorProps {
  /** `robotState.masaniello_cycle`: vem no snapshot do robô, não há evento por operação. */
  cycle: MasanielloCycle | null | undefined;
  currency?: string | null;
  /** Classe do contêiner (cada página tem a sua superfície). */
  className?: string;
}

function hourLabel(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString("pt-BR", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "America/Sao_Paulo",
  });
}

/**
 * Calculadora ao vivo do Gerenciamento Consistente.
 *
 * Mostra o ciclo como o backend o conhece: cada linha é uma ordem real, com o
 * valor que saiu para a corretora e o capital depois do resultado. Não some
 * quando o ciclo termina — fica como o resumo do último ciclo até começar outro.
 */
export function MasanielloCalculator({ cycle, currency, className }: CalculatorProps) {
  if (!cycle) return null;
  const money = (amount: number) => formatBullExBalance(amount, currency);
  const headline = masanielloCycleHeadline(cycle, currency);
  const balance = masanielloCycleBalance(cycle);
  const toneClass =
    headline.tone === "positive"
      ? "text-primary"
      : headline.tone === "negative"
        ? "text-destructive"
        : "text-foreground";
  const balanceClass =
    balance.amount > 0
      ? "text-primary"
      : balance.amount < 0
        ? "text-destructive"
        : "text-foreground";
  const pending = cycle.status === "ACTIVE" ? cycle.pending : null;

  return (
    <section className={className} data-testid="masaniello-calculator">
      <div className="border-b border-border px-5 py-4">
        <h2 className="flex items-center gap-2 font-semibold">
          <Calculator className="h-4 w-4" aria-hidden /> Calculadora · {CONSISTENT_MANAGEMENT_LABEL}
        </h2>
        <p className={`mt-1 text-sm font-semibold ${toneClass}`}>{headline.title}</p>
        <p className="text-xs text-muted-foreground">{headline.detail}</p>
      </div>

      <dl className="grid grid-cols-2 gap-2 px-5 py-4 text-sm sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="Capital inicial" value={money(cycle.capital_inicial)} />
        <Stat label="Capital atual" value={money(cycle.capital_atual)} />
        <Stat
          label="Balanço"
          value={`${formatProfitAmount(balance.amount, currency)} (${balance.percent.toLocaleString("pt-BR")}%)`}
          valueClass={balanceClass}
        />
        <Stat label="Meta do ciclo" value={money(cycle.target)} />
        <Stat
          label="Acertos · Erros"
          value={`${cycle.wins}/${cycle.w} · ${cycle.losses}/${cycle.max_errors}`}
        />
        <Stat
          label="Próxima entrada"
          value={
            pending
              ? "Aguardando resultado"
              : cycle.next_stake != null
                ? `${money(cycle.next_stake)}${cycle.next_stake_adjusted ? " (mín.)" : ""}`
                : "—"
          }
        />
      </dl>

      {cycle.rows.length === 0 && !pending ? (
        <p className="px-5 pb-5 text-sm text-muted-foreground">
          Nenhuma operação neste ciclo ainda. As linhas aparecem aqui a cada resultado.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[980px] whitespace-nowrap text-left text-sm">
            <thead className="border-y border-border bg-background/40 text-[11px] uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-4 py-2.5 font-semibold">Nº</th>
                <th className="px-4 py-2.5 font-semibold">Hora</th>
                <th className="px-4 py-2.5 font-semibold">Ativo</th>
                <th className="px-4 py-2.5 font-semibold">W / L</th>
                <th className="px-4 py-2.5 font-semibold">Entrada</th>
                <th className="px-4 py-2.5 font-semibold">Payout</th>
                <th className="px-4 py-2.5 font-semibold">Retorno</th>
                <th className="px-4 py-2.5 font-semibold">Capital atual</th>
                <th className="px-4 py-2.5 font-semibold">% Acerto</th>
                <th className="px-4 py-2.5 font-semibold">Observação</th>
              </tr>
            </thead>
            <tbody>
              {cycle.rows.map((row) => (
                <tr key={row.order_id} className="border-b border-border last:border-b-0">
                  <td className="px-4 py-2.5 font-semibold">{row.seq ?? "—"}</td>
                  <td className="px-4 py-2.5 text-muted-foreground">{hourLabel(row.at)}</td>
                  <td className="px-4 py-2.5">{row.asset ?? "—"}</td>
                  <td className="px-4 py-2.5">
                    <ResultPill result={row.result} />
                  </td>
                  <td className="px-4 py-2.5">{money(row.stake)}</td>
                  <td className="px-4 py-2.5 text-muted-foreground">
                    {row.payout != null ? `${row.payout.toLocaleString("pt-BR")}%` : "—"}
                  </td>
                  <td
                    className={`px-4 py-2.5 ${row.profit > 0 ? "text-primary" : "text-muted-foreground"}`}
                  >
                    {formatProfitAmount(row.profit, currency)}
                  </td>
                  <td className="px-4 py-2.5 font-semibold">{money(row.capital_after)}</td>
                  <td className="px-4 py-2.5">{row.hit_rate.toLocaleString("pt-BR")}%</td>
                  <td className="px-4 py-2.5 text-xs text-muted-foreground">
                    {masanielloRowNote(row, cycle)}
                  </td>
                </tr>
              ))}
              {pending ? (
                <tr className="bg-background/40">
                  <td className="px-4 py-2.5 font-semibold">{cycle.wins + cycle.losses + 1}</td>
                  <td className="px-4 py-2.5 text-muted-foreground">{hourLabel(pending.at)}</td>
                  <td className="px-4 py-2.5">{pending.asset ?? "—"}</td>
                  <td className="px-4 py-2.5 text-xs text-muted-foreground">em aberto</td>
                  <td className="px-4 py-2.5">{money(pending.stake)}</td>
                  <td className="px-4 py-2.5 text-muted-foreground">
                    {pending.payout != null ? `${pending.payout.toLocaleString("pt-BR")}%` : "—"}
                  </td>
                  <td className="px-4 py-2.5 text-muted-foreground">—</td>
                  <td className="px-4 py-2.5 text-muted-foreground">
                    {money(cycle.capital_atual)}
                  </td>
                  <td className="px-4 py-2.5 text-muted-foreground">—</td>
                  <td className="px-4 py-2.5 text-xs text-muted-foreground">
                    {pending.unknown
                      ? "Resultado não chegou: confira na corretora"
                      : pending.adjusted_to_min
                        ? "Aguardando resultado · entrada ajustada ao mínimo"
                        : "Aguardando resultado"}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Stat({
  label,
  value,
  valueClass = "text-foreground",
}: {
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-background/40 px-3 py-2">
      <dt className="text-[11px] text-muted-foreground">{label}</dt>
      <dd className={`text-sm font-semibold ${valueClass}`}>{value}</dd>
    </div>
  );
}
