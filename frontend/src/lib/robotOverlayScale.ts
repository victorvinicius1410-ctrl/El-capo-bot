/** Controle da escala do robô flutuante (zoom por arraste, roda e pinça). */

export const ROBOT_OVERLAY_SCALE_MIN = 0.7;
export const ROBOT_OVERLAY_SCALE_MAX = 1.3;
export const ROBOT_OVERLAY_SCALE_DEFAULT = 1;

const STORAGE_KEY = "elcapo.robotOverlayScale";
const DRAG_DISTANCE_FOR_FULL_RANGE = 220;
const PERCENT_MIN = 70;
const PERCENT_MAX = 130;
const PERCENT_STEP = 10;
const PERCENT_DEFAULT = 100;

function scaleToPercent(scale: number): number {
  return Math.round(scale * 100);
}

function percentToScale(percent: number): number {
  return percent / 100;
}

function snapPercent(percent: number): number {
  if (!Number.isFinite(percent)) return PERCENT_DEFAULT;
  const clamped = Math.min(PERCENT_MAX, Math.max(PERCENT_MIN, percent));
  return Math.round(clamped / PERCENT_STEP) * PERCENT_STEP;
}

/** Restringe a escala contínua ao intervalo permitido. */
export function clampRobotOverlayScale(scale: number): number {
  return Number.isFinite(scale)
    ? Math.min(ROBOT_OVERLAY_SCALE_MAX, Math.max(ROBOT_OVERLAY_SCALE_MIN, scale))
    : ROBOT_OVERLAY_SCALE_DEFAULT;
}

/** Normaliza qualquer valor persistido para uma escala em passos de 10%. */
export function normalizeRobotOverlayScale(value: unknown): number {
  const parsed =
    typeof value === "number" ? value : typeof value === "string" ? Number.parseFloat(value) : Number.NaN;
  return Number.isFinite(parsed)
    ? percentToScale(snapPercent(scaleToPercent(parsed)))
    : ROBOT_OVERLAY_SCALE_DEFAULT;
}

/** Escala resultante de um arraste no canto de redimensionamento. */
export function scaleFromResizeDrag(startScale: number, deltaX: number, deltaY: number): number {
  const delta = (deltaX + deltaY) / 2;
  return clampRobotOverlayScale(startScale + delta / DRAG_DISTANCE_FOR_FULL_RANGE);
}

/** Escala resultante de rolagem com Ctrl/Cmd pressionado. */
export function scaleFromWheel(startScale: number, deltaY: number): number {
  return clampRobotOverlayScale(startScale - deltaY * 0.0012);
}

/** Escala resultante do gesto de pinça em telas de toque. */
export function scaleFromPinch(startScale: number, startDistance: number, currentDistance: number): number {
  if (!(startDistance > 0) || !Number.isFinite(currentDistance)) return clampRobotOverlayScale(startScale);
  return clampRobotOverlayScale(startScale * (currentDistance / startDistance));
}

/** Lê a escala persistida no navegador. */
export function loadRobotOverlayScale(): number {
  if (typeof window === "undefined") return ROBOT_OVERLAY_SCALE_DEFAULT;
  try {
    return normalizeRobotOverlayScale(window.localStorage.getItem(STORAGE_KEY));
  } catch {
    return ROBOT_OVERLAY_SCALE_DEFAULT;
  }
}

/** Persiste (com snap) a escala escolhida e a retorna. */
export function persistRobotOverlayScale(value: unknown): number {
  const normalized = normalizeRobotOverlayScale(value);
  if (typeof window === "undefined") return normalized;
  try {
    window.localStorage.setItem(STORAGE_KEY, String(normalized));
  } catch {
    // Sem armazenamento disponível, mantém apenas em memória.
  }
  return normalized;
}
