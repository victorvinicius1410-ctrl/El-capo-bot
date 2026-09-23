import { Loader2, RotateCcw } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { formatBullExBalance } from "@/lib/bullexConnection";
import { maskMoney, usePrivacyMode } from "@/lib/privacyMode";
import {
  resetScoreConfirmLabel,
  resetScoreHighlight,
  type ResetScoreSummary,
} from "@/lib/resetScoreConfirm";

interface ResetScoreDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Placar exibido no overlay — é o que será apagado. */
  score: ResetScoreSummary | null | undefined;
  currency?: string | null;
  resetting: boolean;
  onConfirm: () => void;
}

/**
 * Confirmação do "Reiniciar placar".
 *
 * O botão fica a 6px do Iniciar/Parar Operação e a ação não tem desfazer: em
 * 23/09/2026 todo relato de "parei, iniciei de novo e o placar zerou" tinha um
 * `POST /robot/reset-score` alguns segundos antes do start. Aqui a pessoa vê o
 * que vai perder antes de confirmar, e "Cancelar" é o botão em destaque.
 */
export function ResetScoreDialog({
  open,
  onOpenChange,
  score,
  currency,
  resetting,
  onConfirm,
}: ResetScoreDialogProps) {
  const privacyOn = usePrivacyMode();
  const highlight = resetScoreHighlight(score);
  const profit = Number(score?.profit ?? 0);
  const profitLabel = maskMoney(formatBullExBalance(profit, currency), privacyOn);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[420px]">
        <DialogHeader>
          <DialogTitle>Reiniciar o placar?</DialogTitle>
          <DialogDescription>
            O contador da sessão volta para 0 × 0 e o resultado financeiro do placar zera.
            Isso não cancela operação nenhuma e não apaga o seu Histórico — as operações do
            dia continuam lá, com os mesmos valores.
          </DialogDescription>
        </DialogHeader>
        {highlight ? (
          <div className="rounded-lg border border-border/60 bg-card/60 px-3 py-2 text-sm">
            <p className="font-semibold text-foreground">Vai sumir do placar: {highlight}</p>
            <p className="text-xs text-muted-foreground">Resultado do placar hoje: {profitLabel}</p>
          </div>
        ) : null}
        <DialogFooter className="gap-2 sm:justify-between">
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="inline-flex cursor-pointer items-center justify-center rounded-full border border-primary/50 bg-success px-4 py-2 text-sm font-bold text-success-foreground transition hover:opacity-90"
          >
            Cancelar
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={resetting}
            className="inline-flex cursor-pointer items-center justify-center gap-1.5 rounded-full border border-border/60 px-4 py-2 text-sm font-medium text-muted-foreground transition hover:border-destructive/60 hover:text-destructive disabled:cursor-not-allowed disabled:opacity-50"
          >
            {resetting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RotateCcw className="h-3.5 w-3.5" />
            )}
            {resetting ? "Reiniciando..." : resetScoreConfirmLabel(score)}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
