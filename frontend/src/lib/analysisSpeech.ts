/**
 * Texto da análise pronto para a voz do El Capo: só os detalhes medidos.
 *
 * Até 10/09/2026 a narrativa do backend terminava com um veredito — ressalva
 * ("é um sinal razoável, não uma certeza", "com a ressalva de que o setup não
 * está perfeito") ou convicção ("com margem confortável", "o desenho está
 * claro") — e punha adjetivo de dúvida nas observações ("é o ponto fraco
 * daqui", "e isso incomoda"). O dono pediu que a fala tenha só os detalhes da
 * análise, sem incerteza.
 *
 * `narrativa_analise.py` já não gera esses trechos. Este filtro existe porque o
 * frontend sobe separado do backend: cobre o texto do backend que ainda estiver
 * no ar e o que chega por outros caminhos (REV-Z, estratégias nomeadas).
 */

/** Fecho da narrativa. A direção já foi dita no começo da fala. */
const FECHO = /^(?:entrei de|vou de|é|entrada de)\s+(?:call|put)\b/i;

/** Frases que só expressam dúvida ou convicção, sem medida nenhuma. */
const FRASES_DE_OPINIAO = [
  /o cenário favorece/i,
  /não desenhou nada firme/i,
  /não entregou nada muito claro/i,
  /a tendência é voltar/i,
];

/** Opinião colada numa frase que tem medida: sai só a opinião. */
const TRECHOS: Array<[RegExp, string]> = [
  [
    /as médias ainda apontam para o outro lado\s*[—-]\s*é o ponto fraco daqui/gi,
    "as médias curtas apontam para o lado contrário",
  ],
  [/fechou indecisa, corpo de só (\d+%)/gi, "fechou com corpo de $1 do range"],
  [
    /tem um pavio de (\d+%) contra a entrada, e isso incomoda/gi,
    "a última vela deixou um pavio de $1 contra a direção",
  ],
  [/vêm alternando, o mercado está indeciso/gi, "vêm alternando de cor"],
  [/O que pesa contra:/g, "Por outro lado,"],
];

/**
 * Tira do texto da análise o veredito e os trechos de dúvida.
 *
 * @param text Texto da análise vindo do backend.
 * @returns Só as frases com medida, na ordem original; vazio quando nada sobra.
 */
export function cleanAnalysisForSpeech(text: string | null | undefined): string {
  if (!text) return "";
  let limpo = text.replace(/\s+/g, " ");
  for (const [padrao, troca] of TRECHOS) limpo = limpo.replace(padrao, troca);
  // Quebra só em pontuação seguida de espaço: "2.4 desvios" e "1.08520" ficam
  // inteiros. Sem lookbehind, que derruba o bundle no Safari antigo.
  return limpo
    .replace(/([.!?])\s+/g, "$1\n")
    .split("\n")
    .map((frase) => frase.trim())
    .filter(
      (frase) =>
        frase.length > 0 &&
        !FECHO.test(frase) &&
        !FRASES_DE_OPINIAO.some((padrao) => padrao.test(frase)),
    )
    .join(" ");
}
