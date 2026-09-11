import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { cleanAnalysisForSpeech } from "./analysisSpeech.ts";
import { SPEECH_CHUNK_MAX_CHARS, splitSpeechChunks } from "./speechChunks.ts";

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
    const autoTrader = readFileSync(join(here, "../../../backend/backend/auto_trader.py"), "utf8");
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

describe("fala longa não é cortada no meio", () => {
  it("quebra em pedaços curtos sem partir frase", () => {
    const texto =
      "Melhor ativo encontrado. Ativo: euro iene O T C. Direção: CALL, compra. " +
      "Confiança: 100 por cento. Payout: 88 por cento. Valor da entrada: 50 reais. " +
      "Análise: O que chama atenção em GBPAUD-OTC: o R S I em 27 mostra o ativo esticado " +
      "para baixo. Somado a isso, as médias curtas confirmam a direção. " +
      "A última vela fechou com corpo cheio (71% do range). Aguardando janela de entrada.";

    const chunks = splitSpeechChunks(texto);

    assert.ok(chunks.length > 1, "texto longo precisa virar mais de um pedaço");
    for (const chunk of chunks) {
      assert.ok(
        chunk.length <= SPEECH_CHUNK_MAX_CHARS,
        `pedaço acima do limite: ${chunk.length}`,
      );
    }
    // Nada some e nada é reordenado: a leitura é a mesma frase a frase.
    const remontado = chunks.join(" ").replace(/\s+/g, " ");
    assert.equal(remontado, texto.replace(/\s+/g, " ").trim());
  });

  it("mantém frase única inteira e ignora texto vazio", () => {
    const unica = "a".repeat(SPEECH_CHUNK_MAX_CHARS + 40);
    assert.deepEqual(splitSpeechChunks(unica), [unica]);
    assert.deepEqual(splitSpeechChunks("   "), []);
  });

  it("watchdog só corta quando o motor não está falando", () => {
    const narrator = readFileSync(join(here, "../hooks/useRobotNarrator.ts"), "utf8");

    // Sem esta guarda o watchdog cancelava explicação legítima aos 25s.
    assert.match(narrator, /const engineIdle =/);
    assert.match(
      narrator,
      /busyForMs > BUSY_HARD_WATCHDOG_MS \|\|\s*\(busyForMs > BUSY_WATCHDOG_MS && engineIdle\)/,
    );
    // Cada pedaço reinicia o relógio: o watchdog mede falta de progresso.
    assert.match(narrator, /onChunkStart: \(\) => \{\s*busyStartedAtRef\.current = Date\.now\(\);/);
    // Keep-alive do Chrome: pause()+resume() mesmo sem `paused`.
    assert.match(narrator, /window\.speechSynthesis\.pause\(\);\s*window\.speechSynthesis\.resume\(\);/);
  });
});

describe("fala da entrada: só os detalhes da análise", () => {
  it("tira o veredito de dúvida ou convicção e mantém as medidas", () => {
    // Textos reais de 10/09/2026 (robot_trade_history).
    const casos: Array<[string, string]> = [
      [
        "O que chama atenção em GBPAUD-OTC: o RSI em 27 mostra o ativo esticado para baixo. " +
          "Somado a isso, as médias curtas confirmam a direção. " +
          "A última vela fechou com corpo cheio (71% do range). É PUT, e com margem confortável.",
        "O que chama atenção em GBPAUD-OTC: o RSI em 27 mostra o ativo esticado para baixo. " +
          "Somado a isso, as médias curtas confirmam a direção. " +
          "A última vela fechou com corpo cheio (71% do range).",
      ],
      [
        "Na leitura de EURGBP-OTC agora, o RSI em 58 acompanha o lado da compra. " +
          "Entrei de CALL, mas sem exagerar na leitura: é um sinal razoável, não uma certeza.",
        "Na leitura de EURGBP-OTC agora, o RSI em 58 acompanha o lado da compra.",
      ],
      [
        "Olhando o gráfico de USDCHF-OTC: as médias curtas confirmam a direção. " +
          "Vou de PUT. O cenário favorece, ainda que não seja dos mais limpos.",
        "Olhando o gráfico de USDCHF-OTC: as médias curtas confirmam a direção.",
      ],
      [
        "Peguei CHFJPY-OTC num momento interessante: o RSI em 61 acompanha o lado da compra. " +
          "O que pesa contra: as médias ainda apontam para o outro lado — é o ponto fraco daqui. " +
          "A última vela fechou indecisa, corpo de só 22%. " +
          "É CALL, com a ressalva de que o setup não está perfeito.",
        "Peguei CHFJPY-OTC num momento interessante: o RSI em 61 acompanha o lado da compra. " +
          "Por outro lado, as médias curtas apontam para o lado contrário. " +
          "A última vela fechou com corpo de 22% do range.",
      ],
      [
        "EURUSD: preço 2.4 desvios abaixo da média das últimas 20 velas. Esticado assim, " +
          "a tendência é voltar — entrada CALL se o fechamento da vela confirmar (|z| a partir de 2.0).",
        "EURUSD: preço 2.4 desvios abaixo da média das últimas 20 velas.",
      ],
    ];
    for (const [entrada, esperado] of casos) {
      assert.equal(cleanAnalysisForSpeech(entrada), esperado);
    }
  });

  it("não sobra palavra de incerteza em nenhuma frase que o backend gerava", () => {
    const fechos = [
      "Entrei de CALL, mas é um sinal fraco — a leitura não está bonita.",
      "Vou de PUT sabendo que o cenário é magro.",
      "É CALL, e admito: aqui o gráfico não me deu muito.",
      "Vou de PUT — o desenho está claro.",
      "Entrei de CALL com o cenário a favor.",
      "Vou de CALL por retração em zona de exaustão.",
    ];
    for (const fecho of fechos) {
      assert.equal(cleanAnalysisForSpeech(`Na leitura de X agora, o RSI em 40 acompanha o lado da venda. ${fecho}`),
        "Na leitura de X agora, o RSI em 40 acompanha o lado da venda.");
    }
    const observacoes =
      "Olhando o gráfico de X: as médias curtas confirmam a direção. " +
      "Só que tem um pavio de 41% contra a entrada, e isso incomoda. " +
      "As últimas velas vêm alternando, o mercado está indeciso. Vou de CALL.";
    const limpo = cleanAnalysisForSpeech(observacoes);
    assert.doesNotMatch(limpo, /incomoda|indecis|ponto fraco|certeza|fraco|magro|claro|confortável/i);
    assert.match(limpo, /pavio de 41% contra a direção/);
    assert.match(limpo, /alternando de cor/);
    assert.equal(cleanAnalysisForSpeech("Olhando o gráfico de X: o gráfico não entregou nada muito claro. " +
      "Entrei de CALL no que o cenário deu."), "");
  });

  it("a fala não anuncia estratégia nem repete a análise", () => {
    const narration = readFileSync(join(here, "robotNarration.ts"), "utf8");
    assert.doesNotMatch(narration, /Estratégia utilizada|Estratégia confirmada/);
    assert.doesNotMatch(narration, /strategySpeech|\$\{preview\}/);
    assert.match(narration, /Análise: \$\{reasonForSpeech\(cleaned\)\}/);
  });
});
