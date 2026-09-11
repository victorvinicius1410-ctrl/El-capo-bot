# Shell layout — menu do painel (desktop + mobile)

Documento do layout autenticado (`AppShell`): sidebar no desktop e menu
hamburger no celular. Atualizado em **2026-08-07**.

## 1. Objetivo

No mobile, o menu horizontal antigo (scroll de ícones + rodapé de usuário)
ocupava demais a tela. O padrão de mercado é:

1. Barra superior fixa com marca + botão **hamburger** (3 linhas / `Menu`)
2. Drawer lateral que abre ao tocar
3. Backdrop escuro; fecha com X, backdrop, Escape ou ao navegar

No desktop (≥768px) a sidebar fica **fixada no viewport** (`position: fixed`):
ao rolar a página, **só o conteúdo da aba** (`.shell-content`) sobe/desce;
o menu lateral permanece no lugar. A lista de itens (`.shell-nav`) pode
rolar por conta própria se houver muitos links.

## Menu admin (sidebar)

Itens em `ADMIN_NAV_ITEMS` (`AppShell.tsx`), nesta ordem:

| Rota | Rótulo | Visível quando |
|------|--------|----------------|
| `/admin/dashboard` | Dashboard | Todo admin |
| `/admin/clientes` | Acessos | Todo admin |
| `/admin/financeiro` | Financeiro | `finance.view` |
| `/admin/emails` | E-mails | Todo admin |
| `/admin/webhooks-api` | Webhooks e API | `webhooks.view` |
| `/admin/feedbacks` | Feedbacks | Todo admin |

**E-mails** fica sempre no menu do admin (não depende de `emails.view` na
sidebar). Editar templates e envio de teste no backend ainda exigem
`emails.manage` / permissões da API.

Atualizado em **2026-08-06**.

## 2. Estrutura

| Peça | Classe / estado | Papel |
|---|---|---|
| Barra mobile | `.shell-mobile-bar` | Sticky; só `< md` |
| Botão | `.shell-hamburger` | Alterna `mobileNavOpen` |
| Backdrop | `.shell-mobile-backdrop` | Fecha o drawer |
| Drawer / sidebar | `.shell-aside` + `.shell-aside-open` | Off-canvas no mobile; fixed no desktop |
| Fechar | `.shell-drawer-close` | X no header do drawer |
| Nav | `.shell-nav` | Lista vertical (`overflow-y: auto`) |
| Conteúdo | `.shell-content` | Área da aba; `margin-left: var(--shell-aside-width)` |

Arquivos:

- `frontend/src/components/AppShell.tsx` — estado `mobileNavOpen`, a11y
- `frontend/src/styles.css` — estilos `.shell-mobile-*` / `.shell-aside` / `.shell-content`

## 3. Comportamento

- Abre/fecha pelo hamburger; ícone vira `X` quando aberto.
- `aria-expanded` + `aria-controls="shell-main-nav"`.
- Fecha ao mudar de rota (`pathname`), Escape, backdrop ou link.
- Com drawer aberto: `document.body.style.overflow = "hidden"`.
- Variável `--shell-menu-offset-top` (≈ `3.35rem`) continua valendo no
  gráfico expandido no mobile (altura da barra).
- Variável `--shell-aside-width`: `0` no mobile; `17.5rem` no desktop
  (`4.25rem` com gráfico expandido).

## 4. Desktop

A partir de `768px`:

- `.shell-mobile-bar` / backdrop / close ficam `display: none`
- `.shell-aside` usa `position: fixed` (top/bottom/left 0, altura `100dvh`,
  `overflow: hidden`) — **não** sticky
- `.shell-content` recebe `margin-left: var(--shell-aside-width)` para não
  ficar sob o menu
- Sem `transform` / sem depender de `.shell-aside-open`
- Só `.shell-nav` (e o conteúdo da página) rolam

### Por que fixed e não sticky

`html`/`body` usam `overflow-x: hidden`, o que em vários browsers **quebra
`position: sticky`**. O menu rolava junto com a página. `fixed` +
`margin-left` no conteúdo evita isso de forma previsível.

## 5. Testes

`frontend/src/lib/robotNarration.test.ts` → bloco **menu hamburger mobile**
(garante classes e ícone Menu no AppShell/CSS).

## 6. Histórico

- **2026-08-07** — Desktop: sidebar `fixed` + `margin-left` no conteúdo;
  corrige menu que descia com o scroll da página (sticky quebrado por
  `overflow-x: hidden`).
- **2026-07-31** — Hamburger + drawer mobile; remove nav horizontal scrollável.
- **Antes** — Sidebar desktop + nav horizontal no topo no mobile.
