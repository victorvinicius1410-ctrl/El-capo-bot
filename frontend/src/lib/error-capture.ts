let lastCapturedError: unknown;

/** Registra o último erro catastrófico observado pelo processo. */
export function captureError(error: unknown): void {
  lastCapturedError = error;
}

/** Consome o erro capturado para evitar reutilização em outra requisição. */
export function consumeLastCapturedError(): unknown {
  const error = lastCapturedError;
  lastCapturedError = undefined;
  return error;
}

if (typeof process !== "undefined") {
  process.on("uncaughtExceptionMonitor", captureError);
  process.on("unhandledRejection", captureError);
}
