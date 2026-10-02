import { useMemo, useState } from "react";
import { CircleHelp, ListOrdered, RotateCcw, Undo2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { MoneyInput } from "@/components/MoneyInput";
import { Switch } from "@/components/ui/switch";
import { formatBullExBalance, formatProfitAmount } from "@/lib/bullexConnection";
import {
  MASANIELLO_MAX_OPERATIONS,
  type MasanielloCycle,
  type MasanielloProfile,
  masanielloWillContinue,
  simulateMasaniello,
} from "@/lib/masaniello";
import {
  CONSISTENT_MANAGEMENT_LABEL,
  MASANIELLO_PROFILE_OPTIONS,
  masanielloFormView,
  masanielloRowNote,
} from "@/lib/masanielloPresentation";
import type { RobotSettings } from "@/lib/robotSettings";

/** Os dois formulários (diálogo e página) têm destaque de cor diferente. */
export type ConsistentAccent = "dialog" | "panel";

const SELECTED: Record<ConsistentAccent, string> = {
  dialog: "border-primary bg-primary/15 text-foreground",
  panel: "border-[#7ef0f3]/70 bg-[#7ef0f3]/10 text-foreground",
};
const UNSELECTED = "border-border bg-background/40 text-muted-foreground hover:bg-accent";
const INPUT: Record<ConsistentAccent, string> = {
  dialog:
    "w-full rounded-xl border border-border bg-background/40 px-3 py-2 outline-none ring-primary focus:ring-2 disabled:opacity-50",
  panel: "config-field w-full",
};

export type ConsistentManagementValue = Pick<
  RobotSettings,
  | "masanielloEnabled"
  | "masanielloCapital"
  | "masanielloProfile"
  | "masanielloOperations"
  | "masanielloWins"
>;

/** Aviso mostrado no lugar da chave quando o modo LIVE está ligado. */
export const CONSISTENT_LIVE_NOTICE =
  "Indisponível com o modo LIVE ligado. Desligue o LIVE para usar.";

interface ToggleProps {
  checked: boolean;
  disabled?: boolean;
  currency?: string | null;
  /**
   * Modo LIVE ligado (conta marketing): o LIVE esconde loss e o ciclo não
   * fecharia a conta, então o robô ignora o gerenciamento enquanto ele durar.
   */
  liveOn?: boolean;
  /** Linha baixa com interruptor, para o pop-up de Iniciar. */
  compact?: boolean;
  onChange: (checked: boolean) => void;
}

/** Chave liga/desliga, no mesmo desenho do "Gale ativado", com o "?" de ajuda. */
export function ConsistentManagementToggle({
  checked,
  disabled,
  currency,
  liveOn = false,
  compact = false,
  onChange,
}: ToggleProps) {
  const [helpOpen, setHelpOpen] = useState(false);
  return (
    <div
      className={`flex items-center justify-between gap-3 border bg-background/40 font-medium ${
        compact ? "rounded-lg px-3 py-2 text-xs" : "rounded-xl px-4 py-3 text-sm"
      } ${checked && !liveOn ? "border-primary/60" : "border-border"}`}
    >
      <span className="flex flex-wrap items-center gap-x-2">
        <label htmlFor="consistent-management-toggle">{CONSISTENT_MANAGEMENT_LABEL}</label>
        <button
          type="button"
          onClick={() => setHelpOpen(true)}
          aria-label={`O que é o ${CONSISTENT_MANAGEMENT_LABEL}?`}
          title="Entenda como funciona"
          className="inline-flex h-6 w-6 shrink-0 cursor-pointer items-center justify-center rounded-full text-muted-foreground transition hover:bg-accent hover:text-foreground"
        >
          <CircleHelp className="h-4 w-4" aria-hidden />
        </button>
        {liveOn ? (
          <span className="basis-full text-[11px] font-normal text-muted-foreground">
            {CONSISTENT_LIVE_NOTICE}
          </span>
        ) : null}
      </span>
      {compact ? (
        <Switch
          id="consistent-management-toggle"
          className="data-[state=unchecked]:bg-muted-foreground/30"
          checked={checked && !liveOn}
          disabled={disabled || liveOn}
          onCheckedChange={onChange}
        />
      ) : (
        <input
          id="consistent-management-toggle"
          type="checkbox"
          checked={checked && !liveOn}
          disabled={disabled || liveOn}
          onChange={(event) => onChange(event.target.checked)}
          className="h-4 w-4 accent-primary"
        />
      )}
      <MasanielloHelpDialog open={helpOpen} onOpenChange={setHelpOpen} currency={currency} />
    </div>
  );
}

interface FieldsProps {
  value: ConsistentManagementValue;
  onChange: (patch: Partial<ConsistentManagementValue>) => void;
  accent: ConsistentAccent;
  disabled?: boolean;
  currency?: string | null;
  balance?: number | null;
  payoutRef?: number | null;
  /** Ciclo que o servidor conhece (em andamento ou o último). */
  cycle?: MasanielloCycle | null;
  /** Encerra o ciclo em andamento; sem isto o botão não aparece. */
  onEndCycle?: () => void;
  endingCycle?: boolean;
  /** Campos menores, para o pop-up de Iniciar. */
  compact?: boolean;
  /**
   * Não mostra os quatro números do plano nem a frase de risco: quem usa já
   * os mostra em outro lugar (o painel "Resumo" do pop-up de Iniciar).
   */
  hideSummary?: boolean;
}

/**
 * Campos do plano: capital, perfil, meta e limite calculados, avisos e a
 * prévia das entradas. Entra no lugar de "Valor por entrada" + Stop Win/Loss.
 */
export function ConsistentManagementFields({
  value,
  onChange,
  accent,
  disabled,
  currency,
  balance,
  payoutRef,
  cycle,
  onEndCycle,
  endingCycle,
  compact = false,
  hideSummary = false,
}: FieldsProps) {
  const [previewOpen, setPreviewOpen] = useState(false);
  const custom = value.masanielloProfile === "personalizado";
  const base = useMemo(
    () =>
      masanielloFormView({
        capital: value.masanielloCapital,
        profile: value.masanielloProfile,
        operations: value.masanielloOperations,
        wins: value.masanielloWins,
        payoutRef,
        currency,
        balance,
      }),
    [value, payoutRef, currency, balance],
  );
  const continuing = masanielloWillContinue(cycle, {
    capital: value.masanielloCapital,
    operations: base.operations,
    wins: base.wins,
    payoutRef: base.payoutRef,
  });
  const view = continuing
    ? masanielloFormView({
        capital: value.masanielloCapital,
        profile: value.masanielloProfile,
        operations: value.masanielloOperations,
        wins: value.masanielloWins,
        payoutRef,
        currency,
        balance,
        continuing: true,
      })
    : base;
  const money = (amount: number) => formatBullExBalance(amount, currency);
  const activeCycle = cycle?.status === "ACTIVE" ? cycle : null;

  return (
    <div
      className={
        hideSummary
          ? "space-y-3 sm:col-span-2"
          : "space-y-3 rounded-xl border border-border bg-background/30 p-3 sm:col-span-2"
      }
    >
      <div className="grid gap-3">
        <div className="sm:max-w-[220px]">
          <MoneyInput
            label="Capital do ciclo"
            currency={currency}
            min={0}
            step="any"
            size={compact ? "compact" : "default"}
            value={value.masanielloCapital}
            disabled={disabled}
            helperText={`Mínimo neste plano: ${money(view.summary.min_capital)}`}
            onChange={(raw) => {
              const parsed = Number(String(raw).replace(",", "."));
              if (!Number.isFinite(parsed) || parsed <= 0) return;
              onChange({ masanielloCapital: Math.round(parsed * 100) / 100 });
            }}
          />
        </div>
        <div>
          <p
            className={
              compact
                ? "mb-1 text-xs font-medium text-muted-foreground"
                : "mb-1.5 text-sm font-medium"
            }
          >
            Perfil do plano
          </p>
          <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
            {MASANIELLO_PROFILE_OPTIONS.map((option) => {
              const selected = value.masanielloProfile === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  disabled={disabled}
                  title={option.description}
                  onClick={() => onChange({ masanielloProfile: option.value as MasanielloProfile })}
                  className={`rounded-lg border px-2 py-1.5 text-center transition disabled:opacity-50 ${selected ? SELECTED[accent] : UNSELECTED}`}
                >
                  <span className="block text-[11px] font-semibold leading-tight">
                    {option.label}
                  </span>
                  <span className="block text-[10px] leading-tight opacity-75">{option.short}</span>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {custom ? (
        <div className="grid grid-cols-2 gap-3">
          <label className="block space-y-1 text-xs">
            <span className="font-medium text-muted-foreground">Número de operações</span>
            <input
              type="number"
              min={2}
              max={MASANIELLO_MAX_OPERATIONS}
              step={1}
              value={value.masanielloOperations}
              disabled={disabled}
              onChange={(event) => {
                const parsed = Math.floor(Number(event.target.value));
                if (!Number.isFinite(parsed) || parsed < 2) return;
                const operations = Math.min(MASANIELLO_MAX_OPERATIONS, parsed);
                onChange({
                  masanielloOperations: operations,
                  masanielloWins: Math.min(value.masanielloWins, operations - 1),
                });
              }}
              className={INPUT[accent]}
            />
          </label>
          <label className="block space-y-1 text-xs">
            <span className="font-medium text-muted-foreground">Acertos necessários</span>
            <input
              type="number"
              min={1}
              max={value.masanielloOperations - 1}
              step={1}
              value={value.masanielloWins}
              disabled={disabled}
              onChange={(event) => {
                const parsed = Math.floor(Number(event.target.value));
                if (!Number.isFinite(parsed) || parsed < 1) return;
                onChange({
                  masanielloWins: Math.min(value.masanielloOperations - 1, parsed),
                });
              }}
              className={INPUT[accent]}
            />
          </label>
        </div>
      ) : null}

      {hideSummary ? null : (
        <>
          <dl className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
            <PlanStat
              label="Meta do ciclo"
              value={formatProfitAmount(view.summary.target_profit, currency)}
              hint={`em ${view.wins} acertos`}
              tone="positive"
            />
            <PlanStat
              label="Limite do ciclo"
              value={`−${money(value.masanielloCapital)}`}
              hint={`em ${view.summary.max_errors + 1} erros`}
              tone="negative"
            />
            <PlanStat
              label="1ª entrada"
              value={money(view.summary.first_stake)}
              hint="valor calculado"
            />
            <PlanStat
              label="Maior entrada"
              value={money(view.summary.max_stake)}
              hint="no pior caminho"
            />
          </dl>
          <p className="text-[11px] leading-snug text-muted-foreground">
            O ciclo é o stop: o robô para na meta ou no limite. Se os erros se esgotarem,{" "}
            <strong className="text-foreground">o capital do ciclo é perdido por inteiro</strong>.
            Meta calculada com payout de {view.payoutRef}%.
          </p>
        </>
      )}

      {view.error ? <p className="text-xs text-destructive">{view.error}</p> : null}
      {view.notices.map((notice) => (
        <p
          key={notice}
          className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-2 text-[11px] leading-snug text-amber-600 dark:text-amber-400"
        >
          {notice}
        </p>
      ))}

      {activeCycle ? (
        <div className="rounded-md border border-border bg-background/40 px-2.5 py-2 text-[11px] leading-snug">
          <p className="font-semibold text-foreground">
            Ciclo em andamento: {activeCycle.wins}/{activeCycle.w} acertos · {activeCycle.losses}/
            {activeCycle.max_errors} erros · capital {money(activeCycle.capital_atual)}
          </p>
          <p className="mt-0.5 text-muted-foreground">
            {continuing
              ? "Ao iniciar, o robô continua este ciclo de onde parou."
              : "O plano mudou: ao iniciar, o ciclo atual é encerrado e começa um novo."}
          </p>
          {onEndCycle ? (
            <button
              type="button"
              onClick={onEndCycle}
              disabled={disabled || endingCycle}
              className="mt-1.5 inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-[11px] font-semibold text-muted-foreground transition hover:border-destructive/60 hover:text-destructive disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RotateCcw className="h-3 w-3" aria-hidden />
              {endingCycle ? "Encerrando..." : "Encerrar ciclo e começar do zero"}
            </button>
          ) : null}
        </div>
      ) : null}

      <button
        type="button"
        onClick={() => setPreviewOpen((current) => !current)}
        disabled={Boolean(view.error)}
        aria-expanded={previewOpen}
        className="inline-flex cursor-pointer items-center gap-1.5 text-[11px] font-semibold text-primary transition hover:underline disabled:cursor-not-allowed disabled:opacity-50"
      >
        <ListOrdered className="h-3.5 w-3.5" aria-hidden />
        {previewOpen ? "Esconder a prévia do plano" : "Ver a prévia do plano"}
      </button>
      {previewOpen && !view.error ? (
        <MasanielloPlanPreview
          capital={value.masanielloCapital}
          operations={view.operations}
          wins={view.wins}
          payoutRef={view.payoutRef}
          minEntry={view.minEntry}
          currency={currency}
        />
      ) : null}
    </div>
  );
}

function PlanStat({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "positive" | "negative" | "neutral";
}) {
  const color =
    tone === "positive"
      ? "text-primary"
      : tone === "negative"
        ? "text-destructive"
        : "text-foreground";
  return (
    <div className="min-w-0 rounded-lg border border-border bg-background/40 px-2.5 py-1.5">
      <dt className="text-[10px] font-medium text-muted-foreground">{label}</dt>
      <dd className={`truncate text-[13px] font-bold tabular-nums ${color}`}>{value}</dd>
      {hint ? <dd className="text-[10px] text-muted-foreground">{hint}</dd> : null}
    </div>
  );
}

interface PreviewProps {
  capital: number;
  operations: number;
  wins: number;
  payoutRef: number;
  minEntry: number;
  currency?: string | null;
}

/**
 * Prévia interativa do plano, como a planilha: a pessoa marca W ou L em cada
 * operação e vê o valor da próxima entrada mudar. Usa o payout de referência
 * (pior caso aceito pelo robô).
 */
export function MasanielloPlanPreview({
  capital,
  operations,
  wins,
  payoutRef,
  minEntry,
  currency,
}: PreviewProps) {
  const [results, setResults] = useState<string[]>([]);
  const cycle = useMemo(
    () => simulateMasaniello(capital, operations, wins, payoutRef, results, { minEntry }),
    [capital, operations, wins, payoutRef, results, minEntry],
  );
  const money = (amount: number) => formatBullExBalance(amount, currency);
  const active = cycle.status === "ACTIVE" && cycle.next_stake != null;
  // Se o plano mudou e sobraram marcações além do fim do ciclo, corta.
  const played = cycle.rows.length;

  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        Simule o ciclo: marque se cada operação seria acerto (W) ou erro (L) e veja o valor da
        entrada seguinte. É só uma simulação — nenhuma ordem é enviada.
      </p>
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full min-w-[620px] whitespace-nowrap text-left text-xs">
          <thead className="bg-background/60 text-[11px] uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-2.5 py-2 font-semibold">Nº</th>
              <th className="px-2.5 py-2 font-semibold">W / L</th>
              <th className="px-2.5 py-2 font-semibold">Entrada</th>
              <th className="px-2.5 py-2 font-semibold">Retorno</th>
              <th className="px-2.5 py-2 font-semibold">Capital</th>
              <th className="px-2.5 py-2 font-semibold">% Acerto</th>
              <th className="px-2.5 py-2 font-semibold">Observação</th>
            </tr>
          </thead>
          <tbody>
            {cycle.rows.map((row) => (
              <tr key={row.order_id} className="border-t border-border">
                <td className="px-2.5 py-2 font-semibold">{row.seq ?? "—"}</td>
                <td className="px-2.5 py-2">
                  <ResultPill result={row.result} />
                </td>
                <td className="px-2.5 py-2">{money(row.stake)}</td>
                <td className="px-2.5 py-2">{formatProfitAmount(row.profit, currency)}</td>
                <td className="px-2.5 py-2 font-semibold">{money(row.capital_after)}</td>
                <td className="px-2.5 py-2">{row.hit_rate.toLocaleString("pt-BR")}%</td>
                <td className="px-2.5 py-2 text-muted-foreground">
                  {masanielloRowNote(row, cycle)}
                </td>
              </tr>
            ))}
            {active ? (
              <tr className="border-t border-border bg-background/40">
                <td className="px-2.5 py-2 font-semibold">{cycle.wins + cycle.losses + 1}</td>
                <td className="px-2.5 py-2">
                  <span className="inline-flex gap-1">
                    <button
                      type="button"
                      onClick={() => setResults([...results.slice(0, played), "W"])}
                      className="cursor-pointer rounded-md border border-primary/60 bg-primary/15 px-2.5 py-1 text-[11px] font-bold text-primary transition hover:bg-primary/25"
                    >
                      W
                    </button>
                    <button
                      type="button"
                      onClick={() => setResults([...results.slice(0, played), "L"])}
                      className="cursor-pointer rounded-md border border-border bg-background/40 px-2.5 py-1 text-[11px] font-bold text-muted-foreground transition hover:bg-accent"
                    >
                      L
                    </button>
                  </span>
                </td>
                <td className="px-2.5 py-2 font-semibold text-foreground">
                  {money(cycle.next_stake ?? 0)}
                </td>
                <td className="px-2.5 py-2 text-muted-foreground">—</td>
                <td className="px-2.5 py-2 text-muted-foreground">{money(cycle.capital_atual)}</td>
                <td className="px-2.5 py-2 text-muted-foreground">—</td>
                <td className="px-2.5 py-2 text-muted-foreground">
                  {cycle.next_stake_adjusted
                    ? "Próxima entrada (ajustada ao mínimo)"
                    : "Próxima entrada"}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {cycle.status === "TARGET_HIT" ? (
        <p className="text-xs font-semibold text-primary">
          Meta batida: o robô para aqui com {money(cycle.capital_atual)} (
          {formatProfitAmount(cycle.capital_atual - cycle.capital_inicial, currency)}).
        </p>
      ) : null}
      {cycle.status === "BUST" ? (
        <p className="text-xs font-semibold text-destructive">
          {cycle.end_reason === "NO_CAPITAL"
            ? `O capital que sobrou (${money(cycle.capital_atual)}) não paga o mínimo da corretora: o robô para aqui.`
            : "Erros esgotados: o robô para aqui e o capital do ciclo foi perdido."}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => setResults(results.slice(0, Math.max(0, played - 1)))}
          disabled={played === 0}
          className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-[11px] font-semibold text-muted-foreground transition hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Undo2 className="h-3 w-3" aria-hidden /> Desfazer
        </button>
        <button
          type="button"
          onClick={() => setResults([])}
          disabled={played === 0}
          className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-[11px] font-semibold text-muted-foreground transition hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
        >
          <RotateCcw className="h-3 w-3" aria-hidden /> Recomeçar a simulação
        </button>
      </div>
    </div>
  );
}

export function ResultPill({ result }: { result: "WIN" | "LOSS" | "DRAW" }) {
  if (result === "WIN") {
    return (
      <span className="inline-flex rounded-full bg-primary/15 px-2.5 py-1 text-[11px] font-bold text-primary">
        W
      </span>
    );
  }
  return (
    <span className="inline-flex rounded-full bg-muted px-2.5 py-1 text-[11px] font-bold text-muted-foreground">
      {result === "LOSS" ? "L" : "Empate"}
    </span>
  );
}

interface HelpProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  currency?: string | null;
}

const EXAMPLE_RESULTS = ["W", "L", "L", "W", "W", "W"];

/**
 * Explicação aberta pelo "?". Tem que dizer o que o modo faz E o que ele não
 * faz: organiza o risco, não aumenta o acerto. Não prometer lucro aqui.
 */
export function MasanielloHelpDialog({ open, onOpenChange, currency }: HelpProps) {
  // Capital alto de propósito: nenhuma entrada do exemplo cai no mínimo da
  // corretora, então a meta bate exata e a conta fica fácil de acompanhar.
  const example = useMemo(
    () => simulateMasaniello(1000, 10, 4, 80, EXAMPLE_RESULTS, { minEntry: 0 }),
    [],
  );
  const money = (amount: number) => formatBullExBalance(amount, currency);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto border-border bg-card sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{CONSISTENT_MANAGEMENT_LABEL}</DialogTitle>
          <DialogDescription>
            Em vez de um valor fixo por entrada, o El Capo calcula o valor de cada operação a partir
            de um capital, usando o Algoritmo de Masaniello.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          <ul className="list-disc space-y-1.5 pl-5 text-muted-foreground">
            <li>
              Você informa o <strong className="text-foreground">capital do ciclo</strong> e escolhe
              um perfil: quantas operações o ciclo pode ter e quantos acertos precisa.
            </li>
            <li>
              A cada resultado o valor da próxima entrada é recalculado: depois de um erro ela sobe,
              depois de um acerto ela desce, de modo que os acertos necessários levam sempre à mesma
              meta, em qualquer ordem.
            </li>
            <li>
              O ciclo termina sozinho: na <strong className="text-foreground">meta</strong> (bateu
              os acertos) ou no <strong className="text-foreground">limite</strong> (esgotou os
              erros). Nos dois casos o robô para e avisa; para continuar, você inicia outra
              operação.
            </li>
            <li>
              Por isso não há Stop Win, Stop Loss nem gale neste modo: o plano já é o gerenciamento.
            </li>
          </ul>

          <div>
            <p className="font-medium">
              Exemplo: capital de {money(1000)}, perfil Conservador (10 operações, 4 acertos)
            </p>
            <div className="mt-2 overflow-x-auto rounded-lg border border-border">
              <table className="w-full min-w-[420px] whitespace-nowrap text-left text-xs">
                <thead className="bg-background/60 text-[11px] uppercase tracking-wide text-muted-foreground">
                  <tr>
                    <th className="px-2.5 py-2 font-semibold">Nº</th>
                    <th className="px-2.5 py-2 font-semibold">W / L</th>
                    <th className="px-2.5 py-2 font-semibold">Entrada</th>
                    <th className="px-2.5 py-2 font-semibold">Retorno</th>
                    <th className="px-2.5 py-2 font-semibold">Capital</th>
                    <th className="px-2.5 py-2 font-semibold">Erros aceitos</th>
                  </tr>
                </thead>
                <tbody>
                  {example.rows.map((row) => (
                    <tr key={row.order_id} className="border-t border-border">
                      <td className="px-2.5 py-2 font-semibold">{row.seq}</td>
                      <td className="px-2.5 py-2">
                        <ResultPill result={row.result} />
                      </td>
                      <td className="px-2.5 py-2">{money(row.stake)}</td>
                      <td className="px-2.5 py-2">{formatProfitAmount(row.profit, currency)}</td>
                      <td className="px-2.5 py-2 font-semibold">{money(row.capital_after)}</td>
                      <td className="px-2.5 py-2">{row.errors_left}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              Com 4 acertos e 2 erros o ciclo bate a meta e fecha em {money(example.capital_atual)}{" "}
              ({formatProfitAmount(example.capital_atual - example.capital_inicial, currency)}). Em
              qualquer outra ordem de 4 acertos o resultado seria o mesmo. Se em vez disso viessem 7
              erros antes do 4º acerto, o ciclo terminaria com os {money(1000)} perdidos.
            </p>
          </div>

          <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-600 dark:text-amber-400">
            <p className="font-semibold">Antes de ligar, saiba:</p>
            <ul className="mt-1 list-disc space-y-1 pl-4">
              <li>
                Esgotar os erros do ciclo <strong>perde o capital do ciclo inteiro</strong>. Coloque
                só o que você aceita perder.
              </li>
              <li>
                As entradas variam bastante: perto do fim, com erros acumulados, uma única entrada
                pode passar da metade do capital.
              </li>
              <li>
                O algoritmo organiza o risco; ele <strong>não aumenta a taxa de acerto</strong> das
                operações e não garante lucro.
              </li>
              <li>
                Entrada abaixo do mínimo da corretora sobe para o mínimo — com capital pequeno o
                resultado pode ficar um pouco diferente da meta. A prévia do plano mostra isso.
              </li>
            </ul>
          </div>
        </div>
        <DialogFooter>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="inline-flex cursor-pointer items-center justify-center rounded-lg bg-success px-4 py-2 text-sm font-semibold text-success-foreground transition hover:opacity-90"
          >
            Entendi
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
