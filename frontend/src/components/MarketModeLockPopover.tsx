import { useState, type ReactNode } from "react";
import { Wrench } from "lucide-react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { MarketModeLock } from "@/lib/openMarketMaintenance";

interface MarketModeLockPopoverProps {
  lock: MarketModeLock;
  children: ReactNode;
}

/**
 * Explica por que a opção de mercado está com cadeado.
 *
 * Abre no **hover** (mouse) e no **toque/clique** (celular) — o `title` do HTML
 * só resolve o primeiro caso, e era o que existia antes: no celular a pessoa
 * batia num botão morto sem nenhuma explicação.
 *
 * O gatilho **não pode ser um `<button disabled>`**: navegador não dispara
 * evento de mouse nem de clique em elemento desabilitado, então o cadeado
 * ficaria mudo justamente onde precisa falar. Quem usa este componente marca o
 * botão com `aria-disabled` e ignora o clique na própria função de seleção.
 *
 * O conteúdo vai em portal (Radix), então não é cortado pelo diálogo de
 * "Iniciar operação".
 *
 * @param lock - Motivo do bloqueio (manutenção ou sessão forex fechada)
 * @param children - O botão da opção, já estilizado pela tela que o usa
 */
export function MarketModeLockPopover({ lock, children }: MarketModeLockPopoverProps) {
  const [open, setOpen] = useState(false);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        asChild
        onPointerEnter={(event) => {
          // `pointerType` separa mouse de toque: no celular o toque dispara
          // pointerenter logo antes do clique e o popup abriria e fecharia.
          if (event.pointerType === "mouse") setOpen(true);
        }}
        onPointerLeave={(event) => {
          if (event.pointerType === "mouse") setOpen(false);
        }}
      >
        {children}
      </PopoverTrigger>
      <PopoverContent
        side="top"
        align="center"
        className="w-72 space-y-2 text-left"
        role="dialog"
        aria-label={lock.title}
        onOpenAutoFocus={(event) => event.preventDefault()}
      >
        <p className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Wrench className="h-4 w-4 shrink-0 text-amber-400" aria-hidden />
          {lock.title}
        </p>
        <p className="text-xs leading-relaxed text-muted-foreground">{lock.message}</p>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="w-full rounded-lg border border-border bg-background/60 px-3 py-1.5 text-xs font-medium text-foreground transition hover:bg-accent sm:hidden"
        >
          Entendi
        </button>
      </PopoverContent>
    </Popover>
  );
}
