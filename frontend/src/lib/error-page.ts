/** Gera uma página HTML segura para falhas catastróficas de SSR. */
export function renderErrorPage(): string {
  return `<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Falha temporária — ElCapo AutoBot</title></head>
<body style="margin:0;background:#071116;color:#f8fafc;font-family:system-ui;display:grid;min-height:100vh;place-items:center">
<main style="max-width:32rem;padding:2rem;text-align:center"><h1>Não foi possível carregar o painel</h1>
<p style="color:#94a3b8">Atualize a página em alguns instantes. Se o problema continuar, fale com o suporte.</p>
<button onclick="location.reload()" style="padding:.75rem 1rem;border:0;border-radius:.6rem;cursor:pointer">Tentar novamente</button>
</main></body></html>`;
}
