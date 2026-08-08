export interface ServerConfig {
  nodeEnv: string | undefined;
}

/** Lê somente configurações não sensíveis usadas no exemplo server-side. */
export function getServerConfig(): ServerConfig {
  return { nodeEnv: process.env.NODE_ENV };
}
