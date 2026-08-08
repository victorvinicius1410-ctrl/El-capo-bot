# Configurações — Conta Corretora e Robô

Documento da tela `/configuracoes` (abas Conta Corretora e Robô).

## Objetivo visual

Cliente, trial e marketing usam **o mesmo layout**. Não há variante
visual por `account_type` nesta página.

## Conta Corretora (`?secao=conta`)

Componente: `BullexConnectionPanel`.

- **Marca Bullex** em placa escura (`config-brand-plate`) com a logo
  oficial branca:
  - CDN: `https://bull-ex.com/wp-content/uploads/2024/09/logo-bullex-white-1.webp`
  - Local: `/branding/logo-bullex-white-1.webp` (cópia publicada no deploy)
  - Fallback: SVG embutido (`BullexMarkSvg`) se CDN e local falharem.
- Status em pill (`Conectado` / `Desconectado`).
- Métricas: email, saldo, modo, sessão.
- **Login salvo**: após o primeiro connect, email/senha ficam criptografados
  no backend (`encrypted_password`) para auto-reconexão. Ver
  `BULLEX_CREDENCIAIS.md`.
- CTA `Entrar na Bullex` / `Reconectar com login salvo` / `Desconectar Bullex`
  / `Esquecer credenciais salvas`.
- Formulário de credenciais no mesmo design system (`config-field`,
  `config-cta`, `config-btn-ghost`).

## Assets de marca

| Arquivo / URL | Uso |
|---|---|
| `https://bull-ex.com/wp-content/uploads/2024/09/logo-bullex-white-1.webp` | Fonte canônica (CDN) |
| `frontend/public/branding/logo-bullex-white-1.webp` | Cópia local no painel |
| `frontend/public/branding/bullex-logo-white.svg` | Asset legado (não é a marca oficial) |
| `BullexLogo.tsx` | Render + fallbacks |

Publish deve copiar `public/branding/` para `/var/www/elcapobot/branding/`.

## Robô (`?secao=robo`)

`RobotControlPanel` — configuração completa alinhada à corretora:

| Campo | Opções / regra |
|---|---|
| Timeframe | M1 / M5 / M15 (monitoramento contínuo por vela; compra 0–5s) |
| Mercado | OTC / Aberto (cadeado + “Abre em X horas” quando forex fechado) / Ambos (opera sempre em OTC) |
| Valor por entrada | ≥ R$ 5 (default 5) |
| Stop Win / Stop Loss | Modo **Por valor** (≥ R$ 5) ou **Por operações** (≥ 1 WIN/LOSS). Ver `STOP_WIN_LOSS.md`. |
| Gale | on/off + quantidade + multiplicador |
| Ações | Salvar configurações · Iniciar/Parar robô |

O botão **Config** do overlay do robô foi removido: a configuração operacional
é feita em **Iniciar operação** (e neste painel).

Sessão de suporte (impersonation) pode ocultar a aba Robô.
Cadência completa: ver `ROBO_E_SUPORTE.md`.

## Arquivos

- `routes/_authenticated/configuracoes.tsx`
- `components/BullexConnectionPanel.tsx`
- `components/BullexLogo.tsx`
- `components/RobotControlPanel.tsx`
- `styles.css` (bloco `.config-*` e `.bullex-*`)
- `docs/BULLEX_CREDENCIAIS.md`

## Relação com marketing

Ver `MARKETING_SIMULATION.md`: a conta marketing usa o mesmo layout de
configurações. Diferenças: Shift+O para editar métricas; operações ao vivo
mostram WIN/LOSS real no placar (`marketing_win_rate` só no AUTO do Shift+O).

## Histórico

- **2026-08-07 (noite)** — **Iniciar robô** não bloqueia mais por
  `connected` do poll (backoff/cache falso após stop). O `POST /robot/start`
  valida a Bullex. Ver `ROBO_E_SUPORTE.md`.
- **2026-07-31** — Stop Win/Loss com dois modos (por valor / por operações);
  botão Config removido do overlay. Ver `STOP_WIN_LOSS.md`.
- **2026-07-25 (tarde)** — Com forex fechado, **Mercado aberto** permanece
  visível com cadeado e texto “Abre em X horas” (até domingo 22:00 UTC).
  Continua impossível selecionar; Ambos opera só OTC. Ver `ESTRATEGIA.md` §7.
- **2026-07-25** — Com forex fechado (sáb / sex 22:00 UTC→dom 22:00 UTC) a
  opção **Mercado aberto** some do painel e do diálogo Iniciar Operação.
  **Ambos** permanece, mas o backend só varre/opera OTC. Ver `ESTRATEGIA.md` §7.
- **2026-07-24 (noite)** — Botão **Iniciar Operação** do overlay não bloqueia
  mais por `loginPending` preso no auto-reconnect (nas Configurações já
  funcionava). `canStartRobotOperation` + limpeza de pending ao conectar.
  Ver `ROBO_E_SUPORTE.md`.
- **2026-07-24 (noite)** — Mínimo R$ 5 para entrada, stop win e stop loss.
- **2026-07-24** — Corrige reset de timeframe/mercado para M1/OTC no diálogo
  e no painel (poll não sobrescreve edição local); config de entrada/stops/gale
  aplicada de fato no `POST /robot/config` ao iniciar. Ver `ROBO_E_SUPORTE.md`.
- **2026-07-22 (tarde)** — Painel Robô completo (mercado, stops, gale);
  login Bullex salvo criptografado + auto-reconexão.
- **2026-07-22** — Logo oficial webp (`logo-bullex-white-1.webp`) via CDN
  + cópia local; timeframe do painel alinhado a M1/M5/M15.
