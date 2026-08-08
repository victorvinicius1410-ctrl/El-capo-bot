/**
 * Abre um diálogo controlado depois que o gesto de clique/toque atual termina.
 *
 * No mobile, abrir um Radix Dialog de forma síncrona no `onClick` faz o
 * mesmo toque cair no overlay recém-montado e fechar o modal na hora —
 * o botão "parece não funcionar". Adiar com `setTimeout(0)` evita isso.
 *
 * @param open - Callback que seta o estado `open` do diálogo para true
 */
export function scheduleDialogOpen(open: () => void): void {
  setTimeout(open, 0);
}