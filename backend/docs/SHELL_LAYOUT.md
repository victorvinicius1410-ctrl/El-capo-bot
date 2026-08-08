# Shell layout — menu do painel (desktop + mobile)

Documento do layout autenticado (`AppShell`): sidebar no desktop e menu
hamburger no celular. Atualizado em **2026-08-06**.

## 1. Objetivo

No mobile, o menu horizontal antigo (scroll de ícones + rodapé de usuário)
ocupava demais a tela. O padrão de mercado é:

1. Barra superior fixa com marca + botão **hamburger** (3 linhas / `Menu`)
2. Drawer lateral que abre ao tocar
3. Backdrop escuro; fecha com X, backdrop, Escape ou ao navegar

No desktop (≥768px) permanece a sidebar sticky de sempre.

## 2. Estrutura

| Peça | Classe / estado | Papel |
|---|---|---|
| Barra mobile | `.shell-mobile-bar` | Sticky; só `< md` |
| Botão | `.shell-hamburger` | Alterna `mobileNavOpen` |
| Backdrop | `.shell-mobile-backdrop` | Fecha o drawer |
| Drawer | `.shell-aside` + `.shell-aside-open` | Off-canvas → slide-in |
| Fechar | `.shell-drawer-close` | X no header do drawer |
| Nav | `.shell-nav` | Lista vertical (também no drawer) |

Arquivos:

- `frontend/src/components/AppShell.tsx` — estado `mobileNavOpen`, a11y
- `frontend/src/styles.css` — estilos `.shell-mobile-*` / `.shell-aside-open`

## 3. Comportamento

- Abre/fecha pelo hamburger; ícone vira `X` quando aberto.
- `aria-expanded` + `aria-controls="shell-main-nav"`.
- Fecha ao mudar de rota (`pathname`), Escape, backdrop ou link.
- Com drawer aberto: `document.body.style.overflow = "hidden"`.
- Variável `--shell-menu-offset-top` (≈ `3.35rem`) continua valendo no
  gráfico expandido no mobile (altura da barra).

## 4. Desktop

A partir de `768px`:

- `.shell-mobile-bar` / backdrop / close ficam `display: none`
- `.shell-aside` volta a `position: sticky`, largura `--shell-aside-width`
- Sem `transform` / sem depender de `.shell-aside-open`

## 5. Robô / polling fora do admin

Em rotas `/admin/*`, o AppShell **não** monta `LiveTradingDataProvider` nem
`FloatingRobot` (`showRobot` exige `!pathname.startsWith("/admin")`). Isso
evita polling BullEx/robô competindo com a navegação do painel.

Detalhes: [`ADMIN_NAV_PERFORMANCE.md`](./ADMIN_NAV_PERFORMANCE.md).

## 6. Testes

`frontend/src/lib/robotNarration.test.ts` → bloco **menu hamburger mobile**
(garante classes e ícone Menu no AppShell/CSS).

## 7. Histórico

- **2026-08-06** — Desliga robô/polling em `/admin/*` (performance).
- **2026-07-31** — Hamburger + drawer mobile; remove nav horizontal scrollável.
- **Antes** — Sidebar desktop + nav horizontal no topo no mobile.
