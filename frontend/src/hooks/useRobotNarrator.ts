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
import { splitSpeechChunks } from "@/lib/speechChunks";
import type { RobotState } from "@/lib/robotState";

const SPEECH_RATE = 0.92;
/**
 * Libera o narrador se onend/onerror do TTS nunca disparar (bug Chrome).
 *
 * Só vale quando o motor NÃO está falando — antes ele disparava por tempo puro
 * e cortava a explicação da entrada no meio, que é longa de propósito.
 */
const BUSY_WATCHDOG_MS = 25_000;
/**
 * Trava real: mesmo falando, ninguém segura o canal mais que isto.
 *
 * Existe porque o Chrome pode deixar `speaking` preso em `true` depois de o
 * motor morrer — aí o watchdog normal nunca destravaria a fila.
 */
const BUSY_HARD_WATCHDOG_MS = 120_000;
/** Intervalo do keep-alive que impede o Chrome de matar fala longa. */
const SPEECH_KEEPALIVE_MS = 5_000;
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

interface SpeechSequenceOptions {
  /** Pedaços já sanitizados, na ordem de leitura. */
  chunks: string[];
  voice: SpeechSynthesisVoice;
  holdRef: { current: SpeechSynthesisUtterance | null };
  /** Chamado quando o primeiro pedaço começa a sair. */
  onStart: () => void;
  /** Chamado a cada pedaço iniciado — reinicia o relógio do watchdog. */
  onChunkStart: () => void;
  /** Chamado no fim do último pedaço, em erro, ou se o speak() explodir. */
  onDone: () => void;
  /** Enquanto false a sequência para sozinha (preempção, mute, unmount). */
  isCurrent: () => boolean;
}

/**
 * Fala o texto em pedaços encadeados — necessário no Safari/Chrome macOS.
 *
 * Só o PRIMEIRO pedaço passa pelo `cancel()` + atraso curto (Safari descarta
 * `speak()` logo após `cancel()`); os seguintes entram no `onend` do anterior,
 * então a leitura sai contínua para quem ouve. Mantém referência da utterance
 * viva porque o GC do Safari mata a fala se ela for solta cedo.
 *
 * @returns Handle do timer inicial, para o chamador poder cancelar.
 */
function speakSequence(options: SpeechSequenceOptions): number {
  const { chunks, voice, holdRef, onStart, onChunkStart, onDone, isCurrent } = options;
  let index = 0;
  let started = false;

  const buildUtterance = (text: string): SpeechSynthesisUtterance => {
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.voice = voice;
    utterance.lang = voice.lang || "pt-BR";
    utterance.rate = SPEECH_RATE;
    utterance.pitch = speechPitchForVoice(voice);
    utterance.onstart = () => {
      if (!isCurrent()) return;
      if (!started) {
        started = true;
        onStart();
      }
    };
    utterance.onend = () => {
      if (!isCurrent()) return;
      speakNext();
    };
    utterance.onerror = () => onDone();
    return utterance;
  };

  function speakNext(): void {
    if (!isCurrent()) return;
    if (index >= chunks.length) {
      onDone();
      return;
    }
    const utterance = buildUtterance(chunks[index]);
    index += 1;
    holdRef.current = utterance;
    onChunkStart();
    try {
      if (window.speechSynthesis.paused) window.speechSynthesis.resume();
      window.speechSynthesis.speak(utterance);
    } catch {
      onDone();
    }
  }

  window.speechSynthesis.cancel();
  return window.setTimeout(() => {
    if (!isCurrent()) return;
    speakNext();
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
  //
  // O `resume()` sozinho não bastava: no corte de fala longa do Chrome (~15s) o
  // motor para de emitir som mas mantém `speaking=true` e `paused=false`, então
  // a condição antiga nunca era satisfeita. O `pause()+resume()` reinicia o
  // cronômetro interno do motor sem interromper o áudio para quem ouve.
  useEffect(() => {
    if (!supported) return;
    const timer = window.setInterval(() => {
      try {
        if (!window.speechSynthesis.speaking) return;
        if (window.speechSynthesis.paused) {
          window.speechSynthesis.resume();
          return;
        }
        window.speechSynthesis.pause();
        window.speechSynthesis.resume();
      } catch {
        // ignore
      }
    }, SPEECH_KEEPALIVE_MS);
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
    //
    // `busyStartedAtRef` é reiniciado a cada PEDAÇO falado, então o tempo aqui
    // mede "sem progresso", não a duração total da explicação. Fala em curso
    // (`speaking`/`pending`) nunca é cancelada pelo watchdog normal — só pelo
    // limite duro, que existe para o caso de o motor morrer com `speaking`
    // preso em `true`.
    const busyForMs =
      busyRef.current && busyStartedAtRef.current > 0
        ? Date.now() - busyStartedAtRef.current
        : 0;
    const engineIdle =
      !window.speechSynthesis.speaking && !window.speechSynthesis.pending;
    if (
      busyForMs > BUSY_HARD_WATCHDOG_MS ||
      (busyForMs > BUSY_WATCHDOG_MS && engineIdle)
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
      // Com `busyStartedAtRef` zerado o motor está apenas terminando o pedaço
      // anterior — cancelar aqui cortava o fim da frase. Espera o próximo tick.
      if (busyStartedAtRef.current === 0) return;
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

    const chunks = splitSpeechChunks(sanitizeForSpeech(nextEvent.text));
    if (chunks.length === 0) {
      finish();
      return;
    }
    speakTimerRef.current = speakSequence({
      chunks,
      voice,
      holdRef: utteranceRef,
      onStart: () => setSpeaking(true),
      onChunkStart: () => {
        busyStartedAtRef.current = Date.now();
      },
      onDone: finish,
      isCurrent: () => currentKeyRef.current === nextEvent.key,
    });

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

      const chunks = splitSpeechChunks(sanitizeForSpeech(ROBOT_START_NARRATION_TEXT));
      if (chunks.length === 0) {
        finish();
        return;
      }
      speakTimerRef.current = speakSequence({
        chunks,
        voice,
        holdRef: utteranceRef,
        onStart: () => setSpeaking(true),
        onChunkStart: () => {
          busyStartedAtRef.current = Date.now();
        },
        onDone: finish,
        isCurrent: () => currentKeyRef.current === voiceoverKey,
      });
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
