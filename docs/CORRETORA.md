# Aba Corretora (iframe Bullex)

Documento da aba **Corretora** (`/chart`): o gráfico/traderoom da Bullex
embutido no painel ElCapo. Atualizado em **2026-08-13**.

## O que é (e o que não é)

| Tela | Rota | Função |
|------|------|--------|
| **Corretora** | `/chart` | Visualiza o site `trade.bull-ex.com/traderoom` em iframe |
| **Conta Corretora** | `/configuracoes?secao=conta` | Login API (email/senha) para o robô operar |

São fluxos **diferentes**. Conectar em Configurações não “abre” o traderoom
da aba Corretora, e logar no iframe não autentica o robô.

## Sintoma relatado

Cliente entra em **Corretora**, faz login na Bullex (formulário dentro do
painel) e a tela **volta para o login** em vez de abrir a sala de operações.
Para **alguns** usuários funciona; para outros, não.

## Causas (em ordem de frequência)

### 1. Cookies de terceiros no iframe (principal)

O painel roda em `app.elcapobot.online` e embute `trade.bull-ex.com`. O login
da Bullex grava cookies no domínio **dela**. Em contexto de iframe
cross-site:

- Chrome (Privacy Sandbox / third-party cookies)
- Safari (ITP)
- Firefox (Enhanced Tracking Protection)

bloqueiam ou particionam esses cookies. Resultado típico: o formulário
aceita o login, a página recarrega e **volta ao login** — a sessão não
persiste dentro do iframe.

**Por isso “para alguns usuários entra”:** navegador/configuração que ainda
permite cookies de terceiros, ou sessão já existente em 1ª parte.

**Solução para o cliente:** clicar em **Abrir Bullex** (nova aba). Lá o
domínio é first-party e o login funciona normalmente.

**Por que o Safari é o pior caso (2026-09-10).** O bootstrap que a Bullex
serve em `/traderoom` faz, *antes* de desenhar qualquer coisa:

```js
if (!getCookie('release')) {
    fetch(versionApiHost + '/web-client-versions/api/v1/traderoom/version',
          { credentials: "include", headers: {'Authorization': 'QC-SSID ' + window.ssid} })
    ... setupCookie("traderoom_multiversion", 1, {path: '/', expires: 0});
}
```

Ler cookie + gravar cookie + `fetch` cross-origin com credencial — os três são
terceiro-parte dentro do nosso iframe. No Safari o **“Impedir rastreamento
entre sites” vem ligado de fábrica**, então isso nunca funciona; no
Chrome/Edge/Firefox o cookie ainda passa. Daí “no Windows abre, no MacBook
não”. A resposta de `trade.bull-ex.com` **não** manda `X-Frame-Options` nem
`frame-ancestors` — embutir é permitido, o que quebra é a sessão.

Como não há como resolver do nosso lado (quem teria de chamar
`document.requestStorageAccess()` é a Bullex), a aba **detecta WebKit e troca
o iframe por um card** com o CTA de abrir em nova aba, em vez de entregar um
gráfico preto — `isBrokerIframeBlockedByBrowser()` em `brokerChartLayout.ts`.
O card tem um link discreto “Tentar carregar aqui mesmo assim” para quem
desligou o ITP.

### 2. Kick de sessão ao conectar a API do robô

Quando o ElCapo faz `POST /bullex/connect` (ou auto-reconnect com senha),
a Bullex pode invalidar o SSID da sessão web aberta no traderoom. O iframe
volta ao login mesmo após um login bem-sucedido na aba.

Mitigação no backend (já documentada em `BULLEX_CREDENCIAIS.md`):
reconnect soft por SSID antes da senha (`CONNECT_REUSE_ALIVE_SESSION`).
Mesmo assim, um **login novo por senha** ainda pode derrubar a sessão do
site.

### 3. Credenciais inválidas (Conta Corretora / API)

Se o relato for de **Configurações → Conta Corretora** (não do iframe), os
logs do gateway mostram `invalid_credentials` / `[BROKER_LOGIN_REJECTED]`.
Aí a UI permanece em “Desconectado” / formulário de login — email ou senha
errados na corretora (não bug de cache).

Bug histórico separado (já corrigido): após `POST /bullex/connect` ok, o
poll servia `connected:false` do cache — ver `ROBO_E_SUPORTE.md`
(“Bug do loop de login na corretora”).

## UX no painel (2026-08-13)

Na aba Corretora:

1. Aviso curto: login embutido pode falhar por bloqueio de cookies.
2. CTA primário **Abrir Bullex** → `https://trade.bull-ex.com/traderoom`
   em nova aba (`target=_blank`, `rel=noreferrer`).
3. Iframe permanece para quem já tem sessão compatível / visualização.

Arquivos:

- `frontend/src/routes/_authenticated/chart.tsx`
- `frontend/src/styles.css` (`.broker-login-hint`, `.broker-open-external-primary`,
  `.broker-blocked*`, bloco `@supports (height: 1dvh)`)
- `frontend/src/lib/brokerChartLayout.ts` (+ `.test.ts`)
- `frontend/src/components/AppShell.tsx` (atmosfera `shell-fx-*` desligada em `/chart`)

## Duas armadilhas de CSS nesta aba (2026-09-10)

**1. `dvh` sem fallback.** As alturas do gráfico eram só `calc(100dvh - …)`.
Em Safari < 15.4 a declaração inteira é inválida e cai fora; no modo
expandido o `min-height: 0` tirava o último apoio e o iframe virava uma tira
de ~150px. Agora as regras base usam `vh` e o `dvh` entra por
`@supports (height: 1dvh)`.

**Não escreva `vh` e `dvh` como duas declarações `height` na mesma regra** —
o minificador colapsa e fica só a última (`dvh`), ou seja, o fallback some no
build e o problema volta sem aparecer no código-fonte. O `@supports` fica
**fora do `@layer`** (aninhado lá dentro o bundler descarta o bloco, mesmo
caso do `config-atmosphere-lite` em `CONFIGURACOES.md`).

**2. Atmosfera do shell por baixo do traderoom.** `.shell-fx-*` monta
`filter: blur(64px)` + `mix-blend-mode: screen` animados em loop infinito em
toda rota. Somado ao traderoom (gráfico em tempo real) e ao canvas do robô
(pipeline exclusivo do WebKit, dois `getImageData` por frame), é o mesmo
combo que derrubava o processo da aba em Configurações. Em `/chart` a
atmosfera não é mais montada.

## Como orientar o suporte

1. Pedir para usar **Abrir Bullex** (nova aba) e logar lá.
2. Separar: “não abre o gráfico” (iframe) vs “não conecta o robô”
   (Configurações → Conta Corretora).
3. Se for Conta Corretora: conferir credenciais; logs
   `[CONNECT_FAILED_HANDLED] detail=invalid_credentials`.
4. Se conectou o robô e o traderoom deslogou: esperado em login por senha;
   usar Abrir Bullex de novo ou confiar no reconnect SSID.

## Histórico

- **2026-09-10** — Safari/macOS: card de fallback no lugar do iframe morto,
  `vh` de fallback via `@supports`, atmosfera `shell-fx-*` fora de `/chart`.

## Testes manuais

- [ ] Abrir `/chart`: aviso + botão Abrir Bullex visíveis.
- [ ] “Abrir Bullex” abre `trade.bull-ex.com/traderoom` em nova aba.
- [ ] Expandir/recolher gráfico e Escape continuam ok.
- [ ] Configurações → Conta Corretora (connect API) não regrediu.
- [ ] **Safari/Mac:** `/chart` mostra o card (não o iframe preto); “Tentar
      carregar aqui mesmo assim” volta a montar o iframe.
- [ ] **Chrome/Windows:** `/chart` continua abrindo o iframe direto.
- [ ] Expandido: o iframe ocupa a tela toda (conferir que o `@supports`
      sobreviveu ao build — `grep '@supports (height:1dvh)' dist/client/assets/*.css`).
