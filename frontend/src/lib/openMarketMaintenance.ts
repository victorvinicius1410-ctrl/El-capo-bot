/**
 * Trava de manutenção do modo "Mercado aberto" — REABERTA em 08/09/2026.
 *
 * ⛔ O diagnóstico de 07/09 que fechou esta trava estava ERRADO. Ele concluiu
 * que "a BullEx só vende opção binária em par OTC", mas perguntou "o EURUSD
 * está no catálogo?" em vez de olhar o que ESTÁ nele — e caiu num feriado, com
 * 18% do volume normal.
 *
 * Refeito em 08/09 (terça, dia útil) no dump de `get_all_init_v2`: a corretora
 * VENDE opção nos pares abertos. Ela só os nomeia com sufixo `-op`
 * (`EURUSD-op`), e o nosso mapa de canais era indexado por esse nome enquanto a
 * consulta pedia `EURUSD` — por isso todo par aberto voltava `payout=None` e
 * morria em PAYOUT_UNAVAILABLE. Era código nosso, não ausência de oferta.
 *
 * Medido no mesmo dia: EURUSD, GBPUSD, USDJPY, EURJPY, EURGBP, GBPJPY e AUDUSD
 * abertos nos dois canais com payout 84 (turbo) e 85 (binária) — equivalente ao
 * OTC. USDCAD e NZDUSD só na binária. NZDUSD paga 30 e é reprovado pelo
 * `min_payout` sozinho.
 *
 * A tradução de nome foi corrigida no `bullex_service` e o modo OPEN passou a
 * varrer só os 11 pares que a corretora oferece.
 *
 * Para fechar de novo (se a corretora tirar a oferta):
 * `OPEN_MARKET_UNDER_MAINTENANCE = true`.
 */

import type { RobotMarketMode } from "./robotSettings.ts";

/** Chave única para fechar de novo, se a corretora tirar a oferta. */
export const OPEN_MARKET_UNDER_MAINTENANCE = false;

export const OPEN_MARKET_MAINTENANCE_TITLE = "Em manutenção";

export const OPEN_MARKET_MAINTENANCE_MESSAGE =
  "O mercado aberto está temporariamente indisponível: a corretora não oferece " +
  "opção binária nos pares fora de OTC — eles aparecem lá só como forex. " +
  "Estamos resolvendo. Use OTC enquanto isso.";

/** Resumo curto, para caber embaixo do rótulo do botão. */
export const OPEN_MARKET_MAINTENANCE_SHORT = "Em manutenção — toque para saber mais";

export type MarketModeLockReason = "maintenance" | "forex_closed";

export interface MarketModeLock {
  reason: MarketModeLockReason;
  title: string;
  message: string;
}

/**
 * Diz se um modo de mercado está bloqueado e por quê.
 *
 * A manutenção vem ANTES do horário: com a opção em manutenção, dizer "abre em
 * 2 dias" seria promessa falsa.
 *
 * @param mode - Modo avaliado
 * @param options.openMarketAvailable - Sessão forex aberta agora
 * @param options.forexClosedMessage - Contagem regressiva já formatada
 * @returns Motivo do bloqueio, ou null se o modo pode ser escolhido
 */
export function resolveMarketModeLock(
  mode: RobotMarketMode,
  options: { openMarketAvailable: boolean; forexClosedMessage?: string | null },
): MarketModeLock | null {
  if (mode !== "OPEN") return null;
  if (OPEN_MARKET_UNDER_MAINTENANCE) {
    return {
      reason: "maintenance",
      title: OPEN_MARKET_MAINTENANCE_TITLE,
      message: OPEN_MARKET_MAINTENANCE_MESSAGE,
    };
  }
  if (!options.openMarketAvailable) {
    return {
      reason: "forex_closed",
      title: "Mercado aberto fechado",
      message:
        options.forexClosedMessage ??
        "A sessão forex está fechada. O mercado aberto volta na abertura de domingo.",
    };
  }
  return null;
}

/** True quando o modo não pode ser escolhido nem salvo. */
export function isMarketModeLocked(
  mode: RobotMarketMode,
  options: { openMarketAvailable: boolean },
): boolean {
  return resolveMarketModeLock(mode, options) !== null;
}
