import { useEffect, useRef, useState } from "react";
import {
  ROBOT_START_NARRATION_TEXT,
  buildRobotNarrationEvents,
  isBlockedNarratorVoice,
  narrationEventPriority,
  pickNarratorVoice,
  sanitizeForSpeech,
  speechPitchForVoice,
  stopReasonKind,
  unlockSpeechSynthesis,
} from "@/lib/robotNarration";
import type { RobotState } from "@/lib/robotState";

const SPEECH_RATE = 0.92;
/** Libera o narrador se onend/onerror do TTS nunca disparar (bug Chrome). */
const BUSY_WATCHDOG_MS = 25_000;
/**
 * Safari/macOS descarta speak() logo após cancel(). Um tick curto evita
 * a fila morta que deixava o Capo mudo no MacBook.
 */
const SPEAK_AFTER_CANCEL_MS = 60;

let cachedVoice: SpeechSynthesisVoice | null | undefined;
let activeSpeechKey: string | null = null;
let lastVoiceoverKey: string | null = null;
let lastVoiceoverAt = 0;
let speechUnlocked = false;

function acquireSpeechSlot(key: string): boolean {
  if (activeSpeechKey && activeSpeechKey !== key) return false;
  activeSpeechKey = key;
  return true;
}

function releaseSpeechSlot(key: string): void {
  if (activeSpeechKey === key) activeSpeechKey = null;
}

function resolveNarratorVoice(): SpeechSynthesisVoice | null {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return null;
  // Revalida cache: no Safari a lista de vozes chega tarde e o cache pode
  // ter ficado null/errado antes do voiceschanged.
  if (cachedVoice !== undefined) {
    if (cachedVoice && !isBlockedNarratorVoice(cachedVoice)) return cachedVoice;
    cachedVoice = undefined;
  }
  const voices = window.speechSynthesis.getVoices();
  if (!voices.length) return null;
  const voice = pickNarratorVoice(voices);
  cachedVoice = voice;
  return cachedVoice;
}

/**
 * Agenda speak() após cancel — necessário no Safari/Chrome macOS.
 * Mantém referência da utterance (Safari GC mata a fala se soltar cedo).
 */
function speakUtterance(
  utterance: SpeechSynthesisUtterance,
  holdRef: { current: SpeechSynthesisUtterance | null },
  onFail: () => void,
): number {
  holdRef.current = utterance;
  window.speechSynthesis.cancel();
  return window.setTimeout(() => {
    try {
      if (window.speechSynthesis.paused) window.speechSynthesis.resume();
      window.speechSynthesis.speak(utterance);
    } catch {
      onFail();
    }
  }, SPEAK_AFTER_CANCEL_MS);
}

function ensureSpeechUnlocked(): void {
  if (speechUnlocked) return;
  unlockSpeechSynthesis();
  speechUnlocked = true;
}

export interface RobotNarrator {
  speaking: boolean;
  muted: boolean;
  supported: boolean;
  silence: () => void;
  unsilence: () => void;
  toggleSilence: () => void;
  /** Chamar no clique de "Iniciar operação" (gesto do usuário → Safari/iOS). */
  unlockAudio: () => void;
}

/**
 * Observa o estado do robô e narra os eventos relevantes com voz pt-BR,
 * incluindo o áudio gravado de início de operação.
 */
export function useRobotNarrator(
  robotState: RobotState | null | undefined,
  enabled: boolean,
  secondsUntilNextCycle: number | null = null,
  currency = "BRL",
): RobotNarrator {
  const spokenKeysRef = useRef(new Set<string>());
  const currentKeyRef = useRef<string | null>(null);
  const busyRef = useRef(false);
  const busyStartedAtRef = useRef(0);
  const mutedRef = useRef(false);
  const lastAnalysisCycleRef = useRef<string | null>(null);
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null);
  const speakTimerRef = useRef<number | null>(null);
  const [speaking, setSpeaking] = useState(false);
  const [muted, setMuted] = useState(false);
  const [tick, setTick] = useState(0);
  const lastStatusRef = useRef<string | null>(null);
  const lastEnabledRef = useRef<boolean | null>(null);
  const analysisSequenceRef = useRef(0);
  const startSequenceRef = useRef(0);
  const startPendingRef = useRef(false);
  const suppressAnalysisUntilRef = useRef(0);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const supported = typeof window !== "undefined" && "speechSynthesis" in window;

  useEffect(() => {
    if (!supported) return;
    const refreshVoices = () => {
      // Sempre re-resolve no voiceschanged (Safari popula a lista tarde).
      cachedVoice = undefined;
      const voice = resolveNarratorVoice();
      if (voice) setTick((value) => value + 1);
    };
    refreshVoices();
    window.speechSynthesis.addEventListener("voiceschanged", refreshVoices);
    return () => {
      window.speechSynthesis.removeEventListener("voiceschanged", refreshVoices);
    };
  }, [supported]);

  // Chrome/Safari: speechSynthesis pausa sozinho; resume periódico evita fila morta.
  useEffect(() => {
    if (!supported) return;
    const timer = window.setInterval(() => {
      try {
        if (window.speechSynthesis.speaking && window.speechSynthesis.paused) {
          window.speechSynthesis.resume();
        }
      } catch {
        // ignore
      }
    }, 5_000);
    return () => window.clearInterval(timer);
  }, [supported]);

  useEffect(() => {
    if (!supported) return;
    stopEverything();
    if (enabled) {
      mutedRef.current = false;
      setMuted(false);
      ensureSpeechUnlocked();
      setTick((value) => value + 1);
    } else {
      mutedRef.current = true;
      setMuted(true);
    }

    function stopEverything() {
      clearSpeakTimer();
      const key = currentKeyRef.current;
      if (key) releaseSpeechSlot(key);
      currentKeyRef.current = null;
      busyRef.current = false;
      busyStartedAtRef.current = 0;
      utteranceRef.current = null;
      window.speechSynthesis.cancel();
      stopAudio(audioRef);
      setSpeaking(false);
    }
  }, [enabled, supported]);

  useEffect(
    () => () => {
      clearSpeakTimer();
      const key = currentKeyRef.current;
      if (key) releaseSpeechSlot(key);
      busyRef.current = false;
      currentKeyRef.current = null;
      utteranceRef.current = null;
      if (typeof window !== "undefined" && "speechSynthesis" in window) {
        window.speechSynthesis.cancel();
      }
      stopAudio(audioRef);
    },
    [],
  );

  useEffect(() => {
    if (!supported || !enabled || mutedRef.current || muted || !robotState) return;

    const justStarted = robotState.enabled && lastEnabledRef.current !== true;
    if (justStarted) {
      startSequenceRef.current += 1;
      startPendingRef.current = true;
      suppressAnalysisUntilRef.current = Math.max(analysisSequenceRef.current + 3, 3);
      spokenKeysRef.current.add("WELCOME_STOPPED");
      for (let sequence = 1; sequence <= suppressAnalysisUntilRef.current; sequence += 1) {
        spokenKeysRef.current.add(`ANALYSIS_STARTED|${sequence}`);
        spokenKeysRef.current.add(`ANALYSIS_STARTED|${sequence}|el-capo`);
        spokenKeysRef.current.add(`ANALYSIS_STARTED|${sequence}|seeking`);
      }
      hardStop();
      ensureSpeechUnlocked();
    }
    lastEnabledRef.current = robotState.enabled;

    // Watchdog: onend do SpeechSynthesis às vezes não dispara (Chrome).
    if (
      busyRef.current &&
      busyStartedAtRef.current > 0 &&
      Date.now() - busyStartedAtRef.current > BUSY_WATCHDOG_MS
    ) {
      const stuckKey = currentKeyRef.current;
      if (stuckKey) releaseSpeechSlot(stuckKey);
      clearSpeakTimer();
      currentKeyRef.current = null;
      busyRef.current = false;
      busyStartedAtRef.current = 0;
      utteranceRef.current = null;
      window.speechSynthesis.cancel();
      setSpeaking(false);
    }

    const pendingEvents = buildRobotNarrationEvents(
      robotState,
      secondsUntilNextCycle,
      analysisSequenceRef.current,
      startSequenceRef.current,
      currency,
      { includeOpeningVoiceover: false, suppressAnalysisUntilSequence: suppressAnalysisUntilRef.current },
    ).filter((event) => !spokenKeysRef.current.has(event.key));
    const nextEvent = pendingEvents.sort(
      (left, right) => narrationEventPriority(right) - narrationEventPriority(left),
    )[0];

    // Placar/resultado preempta fala de análise/entrada em andamento.
    if (
      nextEvent &&
      busyRef.current &&
      currentKeyRef.current &&
      narrationEventPriority(nextEvent) > narrationEventPriority({ key: currentKeyRef.current })
    ) {
      const preempted = currentKeyRef.current;
      releaseSpeechSlot(preempted);
      spokenKeysRef.current.add(preempted);
      clearSpeakTimer();
      currentKeyRef.current = null;
      busyRef.current = false;
      busyStartedAtRef.current = 0;
      utteranceRef.current = null;
      window.speechSynthesis.cancel();
      setSpeaking(false);
    }

    if (busyRef.current) return;

    if (lastStatusRef.current !== robotState.status) {
      if (robotState.status === "ANALYZING") {
        const cycleId = robotState.cycle_id ?? "no-cycle";
        if (lastAnalysisCycleRef.current !== cycleId) {
          lastAnalysisCycleRef.current = cycleId;
          analysisSequenceRef.current += 1;
        }
      }
      lastStatusRef.current = robotState.status;
    }

    const stopped = stopReasonKind(robotState);
    if (((!robotState.enabled || robotState.status === "STOPPED") && !stopped) ||
        document.visibilityState !== "visible") {
      return;
    }

    if (startPendingRef.current || justStarted) {
      const voiceoverKey = `ROBOT_STARTED_VOICEOVER|${startSequenceRef.current}`;
      if (spokenKeysRef.current.has(voiceoverKey)) {
        if (!busyRef.current) startPendingRef.current = false;
        return;
      }
      playStartVoiceover(voiceoverKey);
      return;
    }

    if (window.speechSynthesis.speaking || window.speechSynthesis.pending) {
      try {
        if (window.speechSynthesis.paused) window.speechSynthesis.resume();
      } catch {
        // ignore
      }
      // Só bloqueia se realmente houver fala; se stuck, o watchdog libera.
      if (Date.now() - busyStartedAtRef.current < BUSY_WATCHDOG_MS) return;
      window.speechSynthesis.cancel();
    }
    const voice = resolveNarratorVoice();
    if (!voice) {
      // Lista ainda vazia (Safari): tenta de novo em breve.
      window.setTimeout(() => setTick((value) => value + 1), 400);
      return;
    }

    if (!nextEvent || spokenKeysRef.current.has(nextEvent.key)) return;

    spokenKeysRef.current.add(nextEvent.key);
    currentKeyRef.current = nextEvent.key;
    busyRef.current = true;
    busyStartedAtRef.current = Date.now();
    if (!acquireSpeechSlot(nextEvent.key)) {
      busyRef.current = false;
      busyStartedAtRef.current = 0;
      currentKeyRef.current = null;
      spokenKeysRef.current.delete(nextEvent.key);
      return;
    }

    const finish = () => {
      if (currentKeyRef.current !== nextEvent.key) return;
      clearSpeakTimer();
      currentKeyRef.current = null;
      busyRef.current = false;
      busyStartedAtRef.current = 0;
      utteranceRef.current = null;
      releaseSpeechSlot(nextEvent.key);
      setSpeaking(false);
      setTick((value) => value + 1);
    };

    const utterance = new SpeechSynthesisUtterance(sanitizeForSpeech(nextEvent.text));
    utterance.voice = voice;
    utterance.lang = voice.lang || "pt-BR";
    utterance.rate = SPEECH_RATE;
    utterance.pitch = speechPitchForVoice(voice);
    utterance.onstart = () => {
      if (currentKeyRef.current === nextEvent.key) setSpeaking(true);
    };
    utterance.onend = () => finish();
    utterance.onerror = () => finish();
    speakTimerRef.current = speakUtterance(utterance, utteranceRef, finish);

    function clearSpeakTimer() {
      if (speakTimerRef.current != null) {
        window.clearTimeout(speakTimerRef.current);
        speakTimerRef.current = null;
      }
    }

    function hardStop() {
      clearSpeakTimer();
      const key = currentKeyRef.current;
      if (key) releaseSpeechSlot(key);
      currentKeyRef.current = null;
      busyRef.current = false;
      busyStartedAtRef.current = 0;
      utteranceRef.current = null;
      window.speechSynthesis.cancel();
      stopAudio(audioRef);
    }

    function playStartVoiceover(voiceoverKey: string) {
      const now = Date.now();
      if (lastVoiceoverKey === voiceoverKey && now - lastVoiceoverAt < 5_000) {
        spokenKeysRef.current.add(voiceoverKey);
        startPendingRef.current = false;
        return;
      }
      const voice = resolveNarratorVoice();
      if (!voice) {
        // Sem voz TTS ainda: libera o fluxo e tenta de novo no próximo tick.
        startPendingRef.current = true;
        window.setTimeout(() => setTick((value) => value + 1), 300);
        return;
      }
      spokenKeysRef.current.add(voiceoverKey);
      currentKeyRef.current = voiceoverKey;
      busyRef.current = true;
      busyStartedAtRef.current = Date.now();
      if (!acquireSpeechSlot(voiceoverKey)) {
        busyRef.current = false;
        busyStartedAtRef.current = 0;
        currentKeyRef.current = null;
        startPendingRef.current = false;
        return;
      }
      lastVoiceoverKey = voiceoverKey;
      lastVoiceoverAt = now;
      stopAudio(audioRef);

      const finish = () => {
        if (currentKeyRef.current !== voiceoverKey) return;
        clearSpeakTimer();
        currentKeyRef.current = null;
        busyRef.current = false;
        busyStartedAtRef.current = 0;
        startPendingRef.current = false;
        utteranceRef.current = null;
        releaseSpeechSlot(voiceoverKey);
        setSpeaking(false);
        window.setTimeout(() => {
          setTick((value) => value + 1);
        }, 400);
      };

      const utterance = new SpeechSynthesisUtterance(
        sanitizeForSpeech(ROBOT_START_NARRATION_TEXT),
      );
      utterance.voice = voice;
      utterance.lang = voice.lang || "pt-BR";
      utterance.rate = SPEECH_RATE;
      utterance.pitch = speechPitchForVoice(voice);
      utterance.onstart = () => {
        if (currentKeyRef.current === voiceoverKey) setSpeaking(true);
      };
      utterance.onend = () => finish();
      utterance.onerror = () => finish();
      speakTimerRef.current = speakUtterance(utterance, utteranceRef, finish);
    }
  }, [currency, enabled, muted, secondsUntilNextCycle, robotState, speaking, tick, supported]);

  function clearSpeakTimer() {
    if (speakTimerRef.current != null) {
      window.clearTimeout(speakTimerRef.current);
      speakTimerRef.current = null;
    }
  }

  function stopCurrent(): void {
    if (!supported) return;
    clearSpeakTimer();
    const key = currentKeyRef.current;
    if (key) {
      spokenKeysRef.current.add(key);
      releaseSpeechSlot(key);
      currentKeyRef.current = null;
    }
    busyRef.current = false;
    busyStartedAtRef.current = 0;
    startPendingRef.current = false;
    utteranceRef.current = null;
    window.speechSynthesis.cancel();
    stopAudio(audioRef);
    setSpeaking(false);
  }

  function silence(): void {
    mutedRef.current = true;
    setMuted(true);
    stopCurrent();
  }

  function unsilence(): void {
    if (!enabled) return;
    mutedRef.current = false;
    setMuted(false);
    ensureSpeechUnlocked();
    setTick((value) => value + 1);
  }

  function toggleSilence(): void {
    if (mutedRef.current || muted) {
      unsilence();
      return;
    }
    silence();
  }

  function unlockAudio(): void {
    ensureSpeechUnlocked();
  }

  return { speaking, muted, supported, silence, unsilence, toggleSilence, unlockAudio };
}

function stopAudio(audioRef: { current: HTMLAudioElement | null }): void {
  if (!audioRef.current) return;
  audioRef.current.onended = null;
  audioRef.current.onerror = null;
  audioRef.current.pause();
  audioRef.current = null;
}
