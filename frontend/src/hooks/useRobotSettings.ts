import { useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, robotConfig } from "@/lib/api";
import {
  cycleMinutesForTimeframe,
  getRobotSettingsSnapshot,
  markRobotSettingsSynced,
  setRobotSettingsForUser,
  subscribeRobotSettings,
  type RobotSettings,
} from "@/lib/robotSettings";

/** Mantém a configuração do robô por usuário e a sincroniza com o backend. */
export function useRobotSettings(userId?: string | null) {
  const queryClient = useQueryClient();
  const settings = useSyncExternalStore(
    subscribeRobotSettings,
    () => getRobotSettingsSnapshot(userId),
    () => getRobotSettingsSnapshot(userId),
  );

  function setSettings(next: RobotSettings): void {
    if (userId) setRobotSettingsForUser(userId, next);
  }

  async function saveSettings(
    next: RobotSettings,
    state: { enabled: boolean; cycleMinutes?: number; accountMode: string; allowReal: boolean; confirmReal: boolean },
  ): Promise<void> {
    if (!userId) throw new ApiError("Não autenticado", "NO_AUTH");
    markRobotSettingsSynced(userId, next);
    const response = await robotConfig({
      enabled: state.enabled,
      account_mode: state.accountMode,
      allow_real: state.allowReal,
      confirm_real: state.confirmReal,
      entry_value: next.entryValue,
      cycle_minutes: cycleMinutesForTimeframe(next.timeframe),
      timeframe: next.timeframe,
      market_mode: next.marketMode,
      stop_win: next.stopWin,
      stop_loss: next.stopLoss,
      stop_win_mode: next.stopWinMode,
      stop_loss_mode: next.stopLossMode,
      stop_win_operations: next.stopWinOperations,
      stop_loss_operations: next.stopLossOperations,
      martingale_enabled: next.martingaleEnabled,
      martingale_steps: next.martingaleSteps,
      martingale_multiplier: next.martingaleMultiplier,
      ai_analysis_enabled: false,
      ai_confirmation_required: false,
      ai_min_confidence: null,
    });
    if (!response.ok) throw new ApiError(response.error, response.code);
    await queryClient.refetchQueries({ queryKey: ["robot-state", userId], exact: true });
  }

  return { settings, setSettings, saveSettings };
}

/** O estado exibido é o próprio estado do robô (sem transformação). */
export function useRobotDisplayState<T>(robotState: T): T {
  return robotState;
}
