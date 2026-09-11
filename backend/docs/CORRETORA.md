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

- `Frontend/src/routes/_authenticated/chart.tsx`
- `Frontend/src/styles.css` (`.broker-login-hint`, `.broker-open-external-primary`)
- `Frontend/src/lib/brokerChartLayout.ts`

## Como orientar o suporte

1. Pedir para usar **Abrir Bullex** (nova aba) e logar lá.
2. Separar: “não abre o gráfico” (iframe) vs “não conecta o robô”
   (Configurações → Conta Corretora).
3. Se for Conta Corretora: conferir credenciais; logs
   `[CONNECT_FAILED_HANDLED] detail=invalid_credentials`.
4. Se conectou o robô e o traderoom deslogou: esperado em login por senha;
   usar Abrir Bullex de novo ou confiar no reconnect SSID.

## Testes manuais

- [ ] Abrir `/chart`: aviso + botão Abrir Bullex visíveis.
- [ ] “Abrir Bullex” abre `trade.bull-ex.com/traderoom` em nova aba.
- [ ] Expandir/recolher gráfico e Escape continuam ok.
- [ ] Configurações → Conta Corretora (connect API) não regrediu.
