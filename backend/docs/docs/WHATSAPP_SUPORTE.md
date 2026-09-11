# Botão flutuante de WhatsApp — pop-up de contato

Documento da funcionalidade de suporte via WhatsApp no painel (`__root.tsx`).
Atualizado em **2026-08-07**.

## 1. O que é

Um botão circular fixo (canto inferior direito, `z-[60]`) presente em **todas**
as páginas do painel (renderizado uma única vez em `RootComponent`, fora do
`Outlet`). Ao clicar, abre um **pop-up** (`Dialog` do Radix, via
`components/ui/dialog.tsx`) mostrando o número oficial de suporte e um botão
para abrir a conversa no WhatsApp Web/App em uma nova aba.

Antes, o botão abria `wa.me` diretamente numa nova aba, sem confirmação nem
exibição do número. Agora o número aparece primeiro dentro do pop-up.

## 2. Localização no código

| Item | Arquivo | Símbolo |
|---|---|---|
| Constantes do número | `frontend/src/routes/__root.tsx` | `WHATSAPP_NUMBER`, `WHATSAPP_DISPLAY`, `WHATSAPP_LINK` |
| Botão + pop-up | `frontend/src/routes/__root.tsx` | `WhatsAppButton()` |
| Ícone SVG | `frontend/src/routes/__root.tsx` | `WhatsAppIcon()` |
| Ponto de montagem | `frontend/src/routes/__root.tsx` | `RootComponent()` — `<WhatsAppButton />` após o `<Outlet />` |
| Componente de pop-up reutilizado | `frontend/src/components/ui/dialog.tsx` | `Dialog`, `DialogContent`, `DialogHeader`, `DialogTitle`, `DialogDescription` |

## 3. Número oficial

| Campo | Valor |
|---|---|
| Exibido ao usuário | `+55 81 8999-8378` |
| Formato E.164 (sem `+`, usado no link) | `558189998378` |
| Link `wa.me` | `https://wa.me/558189998378` |

Para trocar o número no futuro, edite **apenas** as três constantes no topo de
`__root.tsx`:

```ts
const WHATSAPP_NUMBER = "558189998378"; // DDI(55) + DDD(81) + número
const WHATSAPP_DISPLAY = "+55 81 8999-8378"; // como aparece no pop-up
const WHATSAPP_LINK = `https://wa.me/${WHATSAPP_NUMBER}`;
```

`WHATSAPP_NUMBER` **não** pode ter espaços, `+`, `-` ou parênteses — o link
`wa.me` só funciona com dígitos puros (DDI + DDD + número).

## 4. Comportamento do pop-up

1. Usuário clica no botão flutuante (ícone de WhatsApp) → abre o `Dialog`
   (`open` controlado por `useState` local em `WhatsAppButton`).
2. Pop-up mostra:
   - Título: "Fale com a gente no WhatsApp".
   - Descrição curta.
   - Número em destaque (`WHATSAPP_DISPLAY`).
   - Botão "Abrir conversa no WhatsApp" → `<a href={WHATSAPP_LINK} target="_blank">`.
3. Clicar no botão do link fecha o pop-up (`onClick={() => setOpen(false)}`)
   e abre `wa.me` numa nova aba (o `target="_blank"` não é bloqueado pelo
   fechamento do dialog, pois o `href` já dispara a navegação no mesmo
   evento).
4. Fechar sem clicar: `X` no canto (`DialogClose` já embutido em
   `DialogContent`) ou clique fora / `Esc` (comportamento padrão do Radix
   Dialog).

## 5. Z-index e camadas

O `Dialog` (via `DialogOverlay`/`DialogContent`) usa `z-[100]`, acima do botão
flutuante do WhatsApp (`z-[60]`) e do robô animado (`z-50`/`z-60`) — ver
comentário em `components/ui/dialog.tsx`. Isso garante que o pop-up sempre
aparece por cima de tudo, inclusive quando aberto durante uma operação do
robô.

## 6. Por que não abrir `wa.me` direto (decisão de produto)

- Permite ao usuário **confirmar o número** antes de sair do painel.
- Evita abrir uma aba em branco / bloqueada por pop-up blocker em alguns
  navegadores mobile quando o clique não é síncrono com a navegação.
- Mantém consistência visual com os demais diálogos do painel (mesmo
  componente `Dialog` usado em outras partes do app).

## 7. Testes manuais recomendados após qualquer alteração

- Clicar no botão flutuante em qualquer rota (`/`, `/dashboard`, `/settings`
  etc.) → pop-up abre com o número correto.
- Clicar em "Abrir conversa no WhatsApp" → nova aba com `https://wa.me/...`
  e o pop-up fecha.
- Fechar com `Esc`, com o `X` e clicando fora → todos devem fechar sem erro
  no console.
- Verificar em mobile (viewport estreito) que o pop-up não é cortado nem
  fica atrás do robô flutuante.

## 8. Histórico

- **2026-08-07** — Troca do botão de link direto (`<a target="_blank">`) por
  pop-up (`Dialog`) exibindo o número antes de redirecionar. Número
  atualizado para `+55 81 8999-8378` (`558189998378`).
- **Anterior** — Botão flutuante abria `wa.me/558189984096` direto, sem
  pop-up.
