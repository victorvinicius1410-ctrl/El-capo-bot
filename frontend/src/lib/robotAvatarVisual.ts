/**
 * Visual cross-platform do avatar El Capo.
 *
 * O WebM oficial é o lima original. O roxo/teal vem do filtro CSS
 * `hue-rotate(175deg)...` na classe `.robot-avatar-video`.
 *
 * - Windows / Chrome / Firefox / Android: filtro no `<video>` (visual
 *   original — sem “borda” de canvas).
 * - Safari / iOS: o WebKit ignora filter em `<video>`, então o frame vai
 *   para `<canvas>` com o mesmo CSS (MacBook/celular ficam roxos).
 */

/** Asset público do avatar (lima; cor via filtro). */
export const ROBOT_AVATAR_WEBM_SRC = "/robo-wink-orig.webm";

/**
 * Filtro oficial (igual ao backup / Windows Chrome).
 * Aplicar na classe `.robot-avatar-video` (vídeo ou canvas).
 */
export const ROBOT_AVATAR_COLOR_FILTER =
  "hue-rotate(175deg) saturate(1.35) brightness(1.03) contrast(1.12)";

/** Glow teal do personagem (drop-shadow). */
export const ROBOT_AVATAR_GLOW_FILTER =
  "drop-shadow(0 0 14px rgba(37, 219, 224, 0.42)) drop-shadow(0 0 6px rgba(143, 176, 184, 0.24))";

/** Filtro completo para a classe `.robot-avatar-video`. */
export const ROBOT_AVATAR_CSS_FILTER = `${ROBOT_AVATAR_COLOR_FILTER} ${ROBOT_AVATAR_GLOW_FILTER}`;

/**
 * Indica se o motor costuma ignorar CSS filter em elementos `<video>`.
 * Nesses casos usamos o pipeline canvas; nos demais, o vídeo direto
 * (visual Windows sem quadro/borda artificial).
 *
 * Args:
 *   userAgent: string do navegador (opcional; default `navigator.userAgent`)
 *
 * Returns:
 *   true quando UA parece Safari / iOS (WebKit sem Chrome/Chromium/Firefox)
 */
export function isRobotAvatarVideoFilterUnreliable(userAgent?: string): boolean {
  const ua = userAgent ?? (typeof navigator !== "undefined" ? navigator.userAgent : "");
  if (!ua) return false;
  const isIOS =
    /iPad|iPhone|iPod/.test(ua) ||
    (/\bMac OS X\b/.test(ua) && typeof document !== "undefined" && "ontouchend" in document);
  const isSafariDesktop =
    /\bSafari\b/.test(ua) && !/\bChrome\b|\bChromium\b|\bCriOS\b|\bFxiOS\b|\bEdg\b/.test(ua);
  return isIOS || isSafariDesktop;
}

/**
 * Escolhe o pipeline do avatar.
 *
 * Returns:
 *   `"video"` no Chrome/Windows/Android (sem borda de canvas);
 *   `"canvas"` no Safari/iOS (filtro confiável).
 */
export function resolveRobotAvatarPipeline(userAgent?: string): "video" | "canvas" {
  return isRobotAvatarVideoFilterUnreliable(userAgent) ? "canvas" : "video";
}
