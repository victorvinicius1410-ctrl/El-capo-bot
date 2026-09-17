import { STUDY_SEAL_TEXT } from "@/lib/studyMode";

/**
 * Selo do Modo Estudo. Fica no lugar do LOSS no placar enquanto o estudo está
 * ativo e não tem como ser fechado: é o que identifica a tela sem losses.
 */
export function StudyModeSeal() {
  return (
    <div
      className="rounded-xl border border-amber-400/60 bg-amber-500/15 px-2 py-1.5 text-center text-[10px] font-black uppercase leading-tight tracking-wide text-amber-300 shadow-[0_4px_14px_rgba(0,0,0,0.35)] sm:px-3 sm:py-2 sm:text-xs"
      role="status"
      aria-label={STUDY_SEAL_TEXT}
      data-study-seal="true"
    >
      {STUDY_SEAL_TEXT}
    </div>
  );
}
