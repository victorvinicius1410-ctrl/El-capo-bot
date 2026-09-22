/**
 * Modo privacidade: esconde o saldo da conta sem mexer na operação.
 *
 * Ligado pelo ícone de olho do robô flutuante, vale só para o saldo mostrado
 * na linha de baixo do robô. Resultado financeiro, placar, WIN, LOSS e o resto
 * do painel continuam visíveis. A escolha fica no navegador; nada vai para o
 * servidor.
 */
import { useSyncExternalStore } from "react";

const STORAGE_KEY = "elcapo.privacyMode";

/** Texto que substitui o valor escondido. */
export const PRIVACY_MASK = "••••";

const listeners = new Set<() => void>();
let hidden = readStored();

function readStored(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function persist(value: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, value ? "1" : "0");
  } catch {
    // Sem armazenamento disponível, o modo vale só nesta aba.
  }
}

/** Estado atual sem inscrever o caller em re-render (útil em effects). */
export function isPrivacyModeOn(): boolean {
  return hidden;
}

/** Liga ou desliga o modo e avisa todas as telas montadas. */
export function setPrivacyMode(value: boolean): void {
  if (hidden === value) return;
  hidden = value;
  persist(value);
  listeners.forEach((listener) => listener());
}

/** Alterna o modo e devolve o novo estado. */
export function togglePrivacyMode(): boolean {
  setPrivacyMode(!hidden);
  return hidden;
}

/** Observa o modo privacidade (mesmo valor em qualquer componente). */
export function usePrivacyMode(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => hidden,
    () => false,
  );
}

/**
 * Troca um valor já formatado pela máscara quando o modo está ligado.
 *
 * Recebe texto pronto (e não o número) para que quem exibe continue dono da
 * própria formatação de moeda.
 */
export function maskMoney(formatted: string, privacyOn: boolean): string {
  return privacyOn ? PRIVACY_MASK : formatted;
}

/** Rótulo do botão de olho, igual no título e no leitor de tela. */
export function privacyToggleLabel(privacyOn: boolean): string {
  return privacyOn ? "Mostrar saldo" : "Esconder saldo";
}
