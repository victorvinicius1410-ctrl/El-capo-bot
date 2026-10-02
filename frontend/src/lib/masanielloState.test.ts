import assert from "node:assert/strict";
import { test } from "node:test";

import { getRobotStatusPresentation } from "./robotPresentation.ts";
import {
  DEFAULT_ROBOT_SETTINGS,
  getRobotSettingsSnapshot,
  markRobotSettingsSynced,
  masanielloConfigPayload,
  normalizeRobotSettings,
  rememberRobotSettingsFromState,
} from "./robotSettings.ts";
import { getStoppedRobotState, normalizeRobotState } from "./robotState.ts";

// O padrão que mais custou tempo neste projeto: campo novo descartado em
// silêncio por uma lista fixa. Estes testes fazem o campo dar a volta inteira.

const CYCLE = {
  id: "c1",
  rev: 4,
  status: "ACTIVE",
  capital_inicial: 100,
  capital_atual: 93.18,
  n: 10,
  w: 4,
  payout_ref: 80,
  min_entry: 5,
  target: 110.58,
  max_errors: 6,
  wins: 0,
  losses: 1,
  next_stake: 10.23,
  pending: null,
  rows: [
    {
      seq: 1,
      order_id: "77",
      result: "LOSS",
      stake: 6.82,
      profit: -6.82,
      capital_after: 93.18,
      hit_rate: 0,
      errors_left: 5,
    },
  ],
};

test("configuração: os cinco campos sobrevivem à normalização", () => {
  const settings = normalizeRobotSettings({
    ...DEFAULT_ROBOT_SETTINGS,
    masanielloEnabled: true,
    masanielloCapital: 250,
    masanielloProfile: "moderado",
  });
  assert.equal(settings.masanielloEnabled, true);
  assert.equal(settings.masanielloCapital, 250);
  assert.equal(settings.masanielloProfile, "moderado");
  assert.equal(settings.masanielloOperations, 10);
  assert.equal(settings.masanielloWins, 5);
});

test("configuração: ligado, o gale fica desligado", () => {
  const settings = normalizeRobotSettings({
    ...DEFAULT_ROBOT_SETTINGS,
    masanielloEnabled: true,
    martingaleEnabled: true,
  });
  assert.equal(settings.martingaleEnabled, false);
  assert.equal(
    normalizeRobotSettings({ ...DEFAULT_ROBOT_SETTINGS, martingaleEnabled: true })
      .martingaleEnabled,
    true,
  );
});

test("configuração: personalizado guarda os números digitados, dentro da faixa", () => {
  const settings = normalizeRobotSettings({
    ...DEFAULT_ROBOT_SETTINGS,
    masanielloProfile: "personalizado",
    masanielloOperations: 12,
    masanielloWins: 20,
  });
  assert.equal(settings.masanielloOperations, 12);
  assert.equal(settings.masanielloWins, 11);
});

test("corpo do POST /robot/config leva os cinco campos em snake_case", () => {
  assert.deepEqual(
    masanielloConfigPayload(
      normalizeRobotSettings({
        ...DEFAULT_ROBOT_SETTINGS,
        masanielloEnabled: true,
        masanielloCapital: 300,
        masanielloProfile: "agressivo",
      }),
    ),
    {
      masaniello_enabled: true,
      masaniello_capital: 300,
      masaniello_profile: "agressivo",
      masaniello_operations: 10,
      masaniello_wins: 6,
    },
  );
});

test("estado: campos e ciclo chegam do servidor", () => {
  const state = normalizeRobotState({
    ok: true,
    data: {
      status: "WAITING_NEXT_CYCLE",
      enabled: true,
      min_payout: 80,
      masaniello_enabled: true,
      masaniello_capital: 100,
      masaniello_profile: "conservador",
      masaniello_operations: 10,
      masaniello_wins: 4,
      masaniello_cycle: CYCLE,
    },
  });
  assert.equal(state.masaniello_enabled, true);
  assert.equal(state.masaniello_capital, 100);
  assert.equal(state.masaniello_profile, "conservador");
  assert.equal(state.min_payout, 80);
  assert.equal(state.masaniello_cycle?.rows.length, 1);
  assert.equal(state.masaniello_cycle?.next_stake, 10.23);
});

test("estado: servidor antigo (sem os campos) não desliga nem apaga nada", () => {
  const state = normalizeRobotState({ data: { status: "STOPPED" } });
  assert.equal(state.masaniello_enabled, undefined);
  assert.equal(state.masaniello_cycle, undefined);
  assert.equal(getStoppedRobotState().masaniello_cycle, undefined);
});

test("estado -> configuração: o que o servidor salvou volta para o formulário", () => {
  const userId = "u-masaniello-roundtrip";
  markRobotSettingsSynced(userId, DEFAULT_ROBOT_SETTINGS);
  rememberRobotSettingsFromState(userId, {
    masanielloEnabled: true,
    masanielloCapital: 180,
    masanielloProfile: "moderado",
    masanielloOperations: 10,
    masanielloWins: 5,
  });
  const settings = getRobotSettingsSnapshot(userId);
  assert.equal(settings.masanielloEnabled, true);
  assert.equal(settings.masanielloCapital, 180);
  assert.equal(settings.masanielloProfile, "moderado");
});

test("edição local ainda não salva não é atropelada pelo poll", () => {
  const userId = "u-masaniello-edit";
  markRobotSettingsSynced(userId, DEFAULT_ROBOT_SETTINGS);
  // Poll antigo chega com o modo desligado depois de a pessoa ligar na tela.
  rememberRobotSettingsFromState(userId, { masanielloEnabled: false });
  const before = getRobotSettingsSnapshot(userId);
  assert.equal(before.masanielloEnabled, false);
});

test("overlay: fim do ciclo tem título próprio", () => {
  const base = {
    enabled: false,
    masaniello_enabled: true,
    masaniello_capital: 100,
    masaniello_profile: "conservador",
  };
  const meta = normalizeRobotState({
    data: { ...base, status: "STOP_WIN_HIT", masaniello_cycle: { ...CYCLE, status: "TARGET_HIT" } },
  });
  assert.equal(getRobotStatusPresentation(meta, Date.now()).title, "Meta do ciclo batida");
  const perdido = normalizeRobotState({
    data: { ...base, status: "STOP_LOSS_HIT", masaniello_cycle: { ...CYCLE, status: "BUST" } },
  });
  assert.equal(getRobotStatusPresentation(perdido, Date.now()).title, "Capital do ciclo perdido");
  // Modo LIVE ligado: o gerenciamento está suspenso, o ciclo antigo não fala.
  const live = normalizeRobotState({
    data: {
      ...base,
      live_demo: true,
      status: "STOP_WIN_HIT",
      masaniello_cycle: { ...CYCLE, status: "TARGET_HIT" },
    },
  });
  assert.notEqual(getRobotStatusPresentation(live, Date.now()).title, "Meta do ciclo batida");
  // Modo desligado: nada muda no comportamento de sempre do overlay.
  const classico = normalizeRobotState({ data: { status: "STOP_WIN_HIT", enabled: false } });
  assert.equal(getRobotStatusPresentation(classico, Date.now()).title, "Robo parado");
});
