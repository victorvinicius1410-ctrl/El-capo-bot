import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Combina classes condicionais e resolve conflitos do Tailwind.
 *
 * @param inputs Valores de classe aceitos pelo clsx.
 * @returns String de classes normalizada.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
