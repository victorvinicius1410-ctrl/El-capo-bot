import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, it } from "node:test";

import {
  clearRejectionMemory,
  clearResultFlashMemory,
  getRobotStatusPresentation,
} from "./robotPresentation.ts";
import {
  getStoppedRobotState,
  normalizeRobotState,
  type RobotState,
  type RobotTrade,
} from "./robotState.ts";
import {
  STUDY_IDLE_TITLE,
  filterStudyHistory,
  isStudyActive,
  studyHiddenNotice,
  studyWinSpeech,
} from "./studyMode.ts";

const here = new URL(".", import.meta.url).pathname;
const FINISHED = new Date(Date.now() - 5_000).toISOString();
const NOW = Date.now();

function trade(result: string, overrides: Partial<RobotTrade> = {}): RobotTrade {
  return {
    active: "EURUSD-OTC",
    direction: "CALL",
    amount: 10,
    order_id: "ord-1",
    confidence: 80,
    payout: 85,
    strategy_score: 20,
    strategy_name: "Rejeição no suporte",
    strategy_key: "SR_NIVEL",
    strategy_summary: "Pavio longo tocou o suporte e fechou acima",
    analysis_detail: "Detalhe técnico",
    speech_preview: null,
    used_strategies: [],
    strategy_reason: null,
    entry_reason: null,
    result,
    expires_at: null,
    sent_at: FINISHED,
    finished_at: FINISHED,
    profit: result === "WIN" ? 8.5 : -10,
    gale_step: 0,
    is_gale: false,
    account_mode: "REAL",
    ...overrides,
  };
}

function state(extra: Partial<RobotState> = {}): RobotState {
  return {
    ...getStoppedRobotState(),
    enabled: true,
    worker_running: true,
    connected: true,
    live_demo: true,
    study_mode: true,
    ...extra,
  } as RobotState;
}

beforeEach(() => {
  clearRejectionMemory();
  clearResultFlashMemory();
});

describe("isStudyActive", () => {
  it("vale para todo LIVE, sem depender do antigo Modo Estudo", () => {
    assert.equal(isStudyActive({ live_demo: true, study_mode: true }), true);
    assert.equal(isStudyActive({ live_demo: false, study_mode: true }), false);
    assert.equal(isStudyActive({ live_demo: true, study_mode: false }), true);
    assert.equal(isStudyActive(null), false);
  });

  it("normalizeRobotState hidrata a chave e os campos da fala do last_trade", () => {
    const normalized = normalizeRobotState({
      live_demo: true,
      study_mode: true,
      last_trade: {
        active: "EURUSD-OTC",
        direction: "CALL",
        strategy_key: "SR_NIVEL",
        strategy_summary: "resumo",
        analysis_detail: "detalhe",
        speech_preview: "prévia",
      },
    });
    assert.equal(normalized.study_mode, true);
    assert.equal(normalized.last_trade?.strategy_key, "SR_NIVEL");
    assert.equal(normalized.last_trade?.strategy_summary, "resumo");
    assert.equal(normalized.last_trade?.analysis_detail, "detalhe");
    assert.equal(normalized.last_trade?.speech_preview, "prévia");
    assert.equal(getStoppedRobotState().study_mode, false);
  });
});

describe("apresentação no Modo LIVE", () => {
  it("análise e entrada viram o texto neutro, sem ativo", () => {
    for (const status of ["ANALYZING", "SIGNAL_FOUND", "BUYING", "WAITING_GALE_ENTRY"]) {
      const view = getRobotStatusPresentation(state({ status }), NOW);
      assert.equal(view.title, STUDY_IDLE_TITLE, status);
      assert.equal(view.trade, null);
      assert.equal(view.signal, null);
    }
    const open = getRobotStatusPresentation(
      state({
        status: "PENDING_RESULT",
        operation_in_progress: true,
        last_trade: trade("PENDING_RESULT"),
      }),
      NOW,
    );
    assert.equal(open.title, STUDY_IDLE_TITLE);
    assert.equal(open.trade, null);
  });

  it("loss e empate não aparecem", () => {
    for (const result of ["LOSS", "DRAW"]) {
      const view = getRobotStatusPresentation(
        state({ status: result, cycle_result: result, last_trade: trade(result) }),
        NOW,
      );
      assert.equal(view.title, STUDY_IDLE_TITLE, result);
      assert.equal(view.result, null);
    }
  });

  it("win aparece com a estratégia", () => {
    const view = getRobotStatusPresentation(
      state({ status: "WIN", cycle_result: "WIN", last_trade: trade("WIN") }),
      NOW,
    );
    assert.equal(view.title, "WIN");
    assert.equal(view.result, "WIN");
    assert.match(view.detail ?? "", /Rejeição no suporte/);
  });

  it("stop loss continua aparecendo", () => {
    const view = getRobotStatusPresentation(state({ status: "STOP_LOSS_HIT" }), NOW);
    assert.equal(view.title, "Stop Loss atingido");
  });

  it("sem o LIVE, loss aparece como sempre", () => {
    const view = getRobotStatusPresentation(
      state({ live_demo: false, status: "LOSS", cycle_result: "LOSS", last_trade: trade("LOSS") }),
      NOW,
    );
    assert.equal(view.title, "LOSS");
  });
});

describe("narração do Modo LIVE", () => {
  const voice = (result: string) => ({
    order_id: "ord-1",
    result,
    cycle_result: result,
    gale_step: 0,
    wins: 3,
    losses: 2,
    profit: 5,
    at: FINISHED,
  });

  it("fica muda no loss e no gale loss", () => {
    for (const result of ["LOSS", "GALE_LOSS", "DRAW"]) {
      assert.equal(
        studyWinSpeech(state({ result_voice: voice(result), last_trade: trade("LOSS") })),
        null,
        result,
      );
    }
  });

  it("fala o win com a operação da mesma ordem", () => {
    const win = studyWinSpeech(state({ result_voice: voice("WIN"), last_trade: trade("WIN") }));
    assert.equal(win?.cycle, "WIN");
    assert.equal(win?.trade?.strategy_name, "Rejeição no suporte");
  });

  it("no win monta placar completo com estratégia e explicação técnica", () => {
    const source = readFileSync(join(here, "robotNarration.ts"), "utf8");
    assert.match(source, /Estratégia: \$\{reasonForSpeech\(trade\.strategy_name\)\}/);
    assert.match(source, /const analise = analysisSentence/);
    assert.match(source, /Placar: \$\{wins\} e \$\{losses\}/);
  });

  it("não mistura a análise de outra ordem", () => {
    const win = studyWinSpeech(
      state({ result_voice: voice("WIN"), last_trade: trade("WIN", { order_id: "ord-2" }) }),
    );
    assert.equal(win?.trade, null);
  });

  it("robotNarration desvia para o estudo antes das falas de análise", () => {
    const source = readFileSync(join(here, "robotNarration.ts"), "utf8");
    const desvio = source.indexOf("if (isStudyActive(state)) return studyNarrationEvents(state);");
    const stop = source.indexOf("if (stopKind) {");
    const analise = source.indexOf("key: `ANALYSIS_STARTED|");
    assert.ok(desvio > stop, "stop loss precisa continuar falando antes do desvio");
    assert.ok(desvio < analise, "o desvio precisa vir antes das falas de análise");
  });
});

describe("histórico e placar do LIVE", () => {
  const items = [
    { id: "1", result: "WIN", studyMode: true },
    { id: "2", result: "LOSS", studyMode: true },
    { id: "3", result: "DRAW", studyMode: true },
    { id: "4", result: "LOSS", studyMode: false },
  ];

  it("esconde só loss e empate do estudo e conta quantos", () => {
    const filtered = filterStudyHistory(items, true);
    assert.deepEqual(
      filtered.items.map((item) => item.id),
      ["1", "4"],
    );
    assert.equal(filtered.hidden, 2);
    assert.equal(studyHiddenNotice(filtered.hidden), "2 losses antigos ocultos no modo LIVE");
  });

  it("LIVE desligado devolve tudo", () => {
    const filtered = filterStudyHistory(items, false);
    assert.equal(filtered.items.length, 4);
    assert.equal(studyHiddenNotice(filtered.hidden), null);
  });

  it("mantém o contador de LOSS já existente no placar", () => {
    const overlay = readFileSync(join(here, "../components/RobotOverlay.tsx"), "utf8");
    assert.match(overlay, /<ScoreBadge label="LOSS" value=\{scoreLosses\} tone="loss" \/>/);
  });

  it("o histórico mostra o aviso quando esconde algo", () => {
    const page = readFileSync(join(here, "../routes/_authenticated/history.tsx"), "utf8");
    assert.match(page, /studyNotice \?/);
  });
});
