import { useEffect, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { X } from "lucide-react";

export const ADMIN_FIELD_CLASS =
  "min-h-10 w-full rounded-lg border border-slate-600/70 bg-slate-950/60 px-3.5 py-2.5 text-sm text-slate-100 outline-none focus:border-cyan-400/80 focus:ring-4 focus:ring-cyan-400/15";

interface AdminFormDialogProps {
  eyebrow: string;
  title: string;
  description: string;
  steps: readonly string[];
  icon: LucideIcon;
  onClose: () => void;
  children: ReactNode;
}

/** Contêiner modal compartilhado pelos formulários administrativos. */
export function AdminFormDialog({
  eyebrow, title, description, steps, icon: Icon, onClose, children,
}: AdminFormDialogProps) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center overflow-y-auto bg-slate-950/85 p-3 backdrop-blur-xl"
      onMouseDown={(event) => event.currentTarget === event.target && onClose()}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="admin-form-dialog-title"
        className="grid h-[min(92vh,56rem)] w-full max-w-6xl overflow-hidden rounded-3xl border border-cyan-300/20 bg-[#071116] lg:grid-cols-[18rem_minmax(0,1fr)]"
      >
        <aside className="hidden min-h-0 overflow-y-auto border-r border-cyan-200/10 p-6 lg:block">
          <Icon className="mb-5 h-8 w-8 text-cyan-300" />
          <p className="text-xs font-bold uppercase tracking-widest text-cyan-300">{eyebrow}</p>
          <h2 className="mt-2 text-2xl font-semibold text-white">{title}</h2>
          <p className="mt-3 text-sm text-slate-400">{description}</p>
          <ol className="mt-6 space-y-3 text-sm text-slate-300">
            {steps.map((step, index) => (
              <li key={step}>
                {index + 1}. {step}
              </li>
            ))}
          </ol>
        </aside>
        <div className="admin-form-scroll min-h-0 overflow-y-auto overscroll-contain p-5 sm:p-7">
          <header className="mb-5 flex justify-between gap-4">
            <div>
              <p className="text-xs font-bold uppercase tracking-widest text-cyan-300 lg:hidden">
                {eyebrow}
              </p>
              <h2
                id="admin-form-dialog-title"
                className="text-2xl font-semibold text-white lg:hidden"
              >
                {title}
              </h2>
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Fechar"
              className="ml-auto text-slate-400"
            >
              <X className="h-5 w-5" />
            </button>
          </header>
          {children}
        </div>
      </section>
    </div>
  );
}

interface AdminFormSectionProps {
  title: string;
  description?: string;
  icon: LucideIcon;
  step?: number;
  children: ReactNode;
}

/** Agrupa uma etapa do formulário administrativo. */
export function AdminFormSection({ title, description, icon: Icon, step, children }: AdminFormSectionProps) {
  return (
    <fieldset className="rounded-2xl border border-slate-700/70 bg-slate-900/60 p-4 sm:p-5">
      <legend className="sr-only">{title}</legend>
      <div className="mb-4 flex gap-3">
        <Icon className="h-5 w-5 text-cyan-300" />
        <div>
          {step ? <p className="text-xs text-cyan-300">Etapa {String(step).padStart(2, "0")}</p> : null}
          <h3 className="font-semibold text-slate-50">{title}</h3>
          {description ? <p className="text-xs text-slate-400">{description}</p> : null}
        </div>
      </div>
      {children}
    </fieldset>
  );
}

/** Posiciona ações persistentes no rodapé do formulário. */
export function AdminFormActions({ children }: { children: ReactNode }) {
  return <div className="sticky bottom-0 mt-4 flex justify-end gap-2 border-t border-white/10 bg-[#071116] py-4">{children}</div>;
}
