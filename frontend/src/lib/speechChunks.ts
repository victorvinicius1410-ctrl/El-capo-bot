/**
 * Quebra de texto para fala — sem dependências, para poder ser testado direto.
 */
/**
 * Tamanho alvo de cada pedaço falado.
 *
 * A explicação da entrada (`SIGNAL_FOUND`) passa de 50 palavras — ~22s no
 * `SPEECH_RATE` de 0,92, e mais que isso quando a estratégia tem resumo longo.
 * Falada de uma vez ela batia em dois cortes: o Chrome mata utterance longa por
 * volta de 15s (sem marcar `paused`, então o `resume()` periódico nunca pegava)
 * e o watchdog de 25s do narrador cancelava por cima. Como a chave já entrou em
 * `spokenKeys` antes do `speak()`, a frase cortada nunca se repetia — era o
 * "El Capo para de falar no meio da explicação".
 *
 * Falando em pedaços curtos e encadeados nenhum dos dois limites é alcançado, e
 * o texto continua sendo exatamente o mesmo para quem ouve.
 */
export const SPEECH_CHUNK_MAX_CHARS = 140;

/**
 * Quebra o texto em pedaços curtos, sem cortar frase no meio.
 *
 * Agrupa períodos inteiros enquanto couberem em `SPEECH_CHUNK_MAX_CHARS`. Uma
 * frase sozinha maior que o limite é mantida inteira — cortar no meio dela
 * soaria pior do que o pedaço grande.
 *
 * @param text Texto já sanitizado para fala.
 * @returns Pedaços na ordem de leitura (lista vazia se não houver texto).
 */
export function splitSpeechChunks(text: string): string[] {
  const clean = text.trim();
  if (!clean) return [];
  const sentences = clean.match(/[^.!?]+[.!?]*\s*/g) ?? [clean];
  const chunks: string[] = [];
  let current = "";
  for (const raw of sentences) {
    const sentence = raw.trim();
    if (!sentence) continue;
    if (!current) {
      current = sentence;
      continue;
    }
    if (`${current} ${sentence}`.length <= SPEECH_CHUNK_MAX_CHARS) {
      current = `${current} ${sentence}`;
      continue;
    }
    chunks.push(current);
    current = sentence;
  }
  if (current) chunks.push(current);
  return chunks;
}
