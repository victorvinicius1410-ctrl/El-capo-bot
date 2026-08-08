import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

describe("narração de início contínuo", () => {
  it("usa TTS contínuo no start e não toca o MP3 legado", () => {
    const narration = readFileSync(join(here, "robotNarration.ts"), "utf8");
    const narrator = readFileSync(join(here, "../hooks/useRobotNarrator.ts"), "utf8");

    const linesMatch = narration.match(
      /export const ROBOT_START_NARRATION_LINES = \[([\s\S]*?)\] as const/,
    );
    assert.ok(linesMatch);
    const linesBlock = linesMatch?.[1] ?? "";
    assert.match(linesBlock, /El Capo está analisando o mercado/);
    assert.match(linesBlock, /Identificando uma oportunidade de operação lucrativa/);
    assert.doesNotMatch(linesBlock, /5\s*minut/i);
    assert.doesNotMatch(linesBlock, /demorar/i);
    assert.doesNotMatch(linesBlock, /aguarde/i);

    assert.match(narration, /text: ROBOT_START_NARRATION_TEXT/);
    assert.doesNotMatch(narration, /Áudio de início do robô/);

    assert.match(narrator, /ROBOT_START_NARRATION_TEXT/);
    assert.doesNotMatch(narrator, /new Audio\(ROBOT_START_VOICEOVER_SRC\)/);
    assert.doesNotMatch(narrator, /robot-voiceover\.mp3/);
  });

  it("força pronúncia El Kápo no TTS (evita 'El Cepo')", () => {
    const narration = readFileSync(join(here, "robotNarration.ts"), "utf8");
    assert.match(narration, /export const BRAND_NAME_SPEECH = "El Kápo"/);
    assert.match(narration, /replace\(\/\\bEl Capo\\b\/gi,\s*BRAND_NAME_SPEECH\)/);
    assert.match(narration, /replace\(\/\\bCapo\\b\/g,\s*"Kápo"\)/);
  });
});

describe("narração do placar (WIN/LOSS)", () => {
  it("prioriza RESULT na fila, chave por order_id e preempta no narrador", () => {
    const narration = readFileSync(join(here, "robotNarration.ts"), "utf8");
    const narrator = readFileSync(join(here, "../hooks/useRobotNarrator.ts"), "utf8");

    assert.match(narration, /return \["RESULT", result, orderId \?\? "-"/);
    assert.match(narration, /export function narrationEventPriority/);
    assert.match(narration, /return dedupeEvents\(\[\.\.\.resultEvents, \.\.\.events\]\)/);
    assert.match(narrator, /narrationEventPriority/);
    assert.match(narrator, /BUSY_WATCHDOG_MS/);
    assert.match(narrator, /speechSynthesis\.resume/);
    assert.match(
      narrator,
      /narrationEventPriority\(nextEvent\) > narrationEventPriority\(\{ key: currentKeyRef\.current \}\)/,
    );
  });

  it("fala pelo canal result_voice do backend, sem depender do status", () => {
    const narration = readFileSync(join(here, "robotNarration.ts"), "utf8");
    const state = readFileSync(join(here, "robotState.ts"), "utf8");

    assert.match(state, /export interface RobotResultVoice/);
    assert.match(state, /result_voice: normalizeResultVoice\(raw\.result_voice \?\? raw\.resultVoice\)/);
    assert.match(narration, /function resultVoiceEvent/);
    assert.match(narration, /const voiceEvent = resultVoiceEvent\(state\.result_voice, currency\)/);
    // Canal novo e caminho legado são exclusivos: nunca falam o mesmo resultado 2x.
    assert.match(narration, /if \(voiceEvent\) \{\s*resultEvents\.push\(voiceEvent\);\s*\} else \{/);
  });

  it("mantém a janela operacional de resultado em 5s no backend", () => {
    const autoTrader = readFileSync(join(here, "../../../Backend/backend/auto_trader.py"), "utf8");
    assert.match(autoTrader, /result_display_until = finished_at \+ timedelta\(seconds=5\)/);
    assert.match(autoTrader, /RESULT_VOICE_TTL_SECONDS = 25/);
  });
});

describe("seleção de voz masculina (macOS/Safari)", () => {
  it("usa name+voiceURI e prefere masculino, com fallback pt-BR", () => {
    const narration = readFileSync(join(here, "robotNarration.ts"), "utf8");
    assert.match(narration, /export function voiceIdentity/);
    assert.match(narration, /voice\.name.*voice\.voiceURI|voiceURI.*voice\.name/s);
    assert.match(narration, /felipe/i);
    assert.match(narration, /reed/i);
    assert.match(narration, /luciano/i);
    assert.match(narration, /if \(gender !== "male"\) return Number\.NEGATIVE_INFINITY/);
    assert.match(narration, /scoreVoiceFallback/);
    assert.match(narration, /export function speechPitchForVoice/);
    assert.match(narration, /export function unlockSpeechSynthesis/);
    // Não bloqueia "Google português" do Chrome (Mac/Android).
    assert.doesNotMatch(narration, /BLOCKED_VOICE_HINTS = \["google"/);
    assert.match(narration, /return fallbackRanked\[0\]\?\.voice \?\? null/);
  });

  it("revalida cache no voiceschanged, unlock e delay pós-cancel no Safari", () => {
    const narrator = readFileSync(join(here, "../hooks/useRobotNarrator.ts"), "utf8");
    assert.match(narrator, /cachedVoice = undefined/);
    assert.match(narrator, /voiceschanged/);
    assert.match(narrator, /speechPitchForVoice/);
    assert.match(narrator, /SPEAK_AFTER_CANCEL_MS/);
    assert.match(narrator, /unlockSpeechSynthesis|unlockAudio/);
    assert.match(narrator, /utteranceRef/);
  });
});

describe("menu hamburger mobile", () => {
  it("AppShell usa barra com Menu/X e drawer shell-aside-open", () => {
    const shell = readFileSync(join(here, "../components/AppShell.tsx"), "utf8");
    const styles = readFileSync(join(here, "../styles.css"), "utf8");
    assert.match(shell, /shell-mobile-bar/);
    assert.match(shell, /shell-hamburger/);
    assert.match(shell, /shell-aside-open/);
    assert.match(shell, /mobileNavOpen/);
    assert.match(shell, /\bMenu\b/);
    assert.match(styles, /\.shell-hamburger/);
    assert.match(styles, /\.shell-aside-open/);
    assert.match(styles, /translateX\(-105%\)/);
  });
});

describe("visual do overlay do robô", () => {
  // Visual vigente: WebM lima → canvas + filtro CSS (Safari/iOS igual Windows).
  it("delega o avatar ao RobotAvatarVideo (canvas), sem video inline", () => {
    const overlay = readFileSync(join(here, "../components/RobotOverlay.tsx"), "utf8");
    const styles = readFileSync(join(here, "../styles.css"), "utf8");
    assert.match(overlay, /RobotAvatarVideo/);
    assert.doesNotMatch(overlay, /hue-rotate/);
    assert.doesNotMatch(overlay, /robo-wink-classic/);
    assert.match(styles, /\.robot-avatar-video/);
    assert.match(styles, /hue-rotate\(175deg\)/);
    assert.match(styles, /37,\s*219,\s*224/);
  });
});
