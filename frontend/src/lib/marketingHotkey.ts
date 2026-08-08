/**
 * Detecção do atalho oculto Shift+O da conta marketing.
 *
 * O atalho não deve disparar enquanto o usuário digita em campos de texto
 * e não usa Ctrl/Meta/Alt para evitar conflito com atalhos do sistema.
 */

const EDITABLE_SELECTOR =
  'input, textarea, select, [contenteditable=""], [contenteditable="true"], [role="textbox"]';

type KeyboardLike = {
  key?: string;
  code?: string;
  shiftKey?: boolean;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
  target?: EventTarget | null;
};

/**
 * Indica se o evento corresponde a Shift+O sem outros modificadores.
 */
export function isMarketingHotkey(event: KeyboardLike): boolean {
  if (!event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return false;
  return event.key === "O" || event.key === "o" || event.code === "KeyO";
}

/**
 * Retorna true quando o foco está em um campo editável (não deve abrir o painel).
 */
export function isEditableKeyboardTarget(target: EventTarget | null): boolean {
  if (!target || typeof target !== "object") return false;
  const element = target as {
    isContentEditable?: boolean;
    closest?: (selector: string) => unknown;
  };
  if (element.isContentEditable) return true;
  if (typeof element.closest === "function") {
    return Boolean(element.closest(EDITABLE_SELECTOR));
  }
  return false;
}

/**
 * Decide se o atalho deve abrir/fechar o painel marketing.
 */
export function shouldToggleMarketingPanel(event: KeyboardLike, enabled: boolean): boolean {
  if (!enabled) return false;
  if (!isMarketingHotkey(event)) return false;
  if (isEditableKeyboardTarget(event.target ?? null)) return false;
  return true;
}
