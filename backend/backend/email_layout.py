"""
Design system dos e-mails transacionais do ElCapo AutoBot.

Todo HTML aqui é "bulletproof" para clientes de e-mail: tabelas
``role="presentation"``, estilos inline, largura máxima de 560px e nenhuma
dependência de CSS externo, webfont ou media query. A paleta espelha o painel
(fundo escuro + acento ciano) para que o e-mail e o produto pareçam a mesma
coisa.

O motor de render (:func:`backend.email_service.EmailService.render`) só faz
substituição simples de ``{{variavel}}`` — não existe ``{% if %}``. Por isso
nenhum template pode depender de lógica condicional: variáveis que podem vir
vazias recebem fallback textual em ``build_variables`` ou são entregues como
bloco HTML pronto (ex.: ``first_access_block``).
"""

from __future__ import annotations

from backend.webhook_models import DomainEventType

FONT = "Arial, Helvetica, sans-serif"

# Paleta — mesma linguagem visual do painel.
BG_PAGE = "#070d16"
CARD = "#101827"
CARD_BORDER = "#1d3945"
CARD_FOOT = "#0c1420"
INNER = "#0a111d"
INNER_BORDER = "#1e3540"
LINE = "#20313d"
WHITE = "#ffffff"
TEXT = "#bcd6d9"
MUTED = "#829fa3"
DIM = "#607d82"
FAINT = "#506c70"
FAINTER = "#3f5a5e"
EXTERNAL = "#5d787c"
EXTERNAL_STRONG = "#a2bec1"


class Tone:
    """Cor de acento de um e-mail (barra, badge, botão e destaques)."""

    def __init__(
        self,
        *,
        accent: str,
        ink: str,
        badge_bg: str,
        badge_border: str,
        step_bg: str,
        step_border: str,
    ) -> None:
        self.accent = accent
        self.ink = ink
        self.badge_bg = badge_bg
        self.badge_border = badge_border
        self.step_bg = step_bg
        self.step_border = step_border


# Ciano: positivo / ação desejada (compra, renovação, trial, senha).
CYAN = Tone(
    accent="#7ef0f3",
    ink="#071116",
    badge_bg="#102a2f",
    badge_border="#24545a",
    step_bg="#172733",
    step_border="#28505a",
)

# Âmbar: exige atenção do cliente (falha de cobrança, contestação).
AMBER = Tone(
    accent="#ffc46b",
    ink="#1d1204",
    badge_bg="#2b2010",
    badge_border="#5c4626",
    step_bg="#241d10",
    step_border="#5c4626",
)

# Neutro: encerramento sem culpa do cliente (cancelamento, reembolso, fim do teste).
SLATE = Tone(
    accent="#a7c3c7",
    ink="#0b1116",
    badge_bg="#16212b",
    badge_border="#2c4049",
    step_bg="#16212b",
    step_border="#2c4049",
)


def _spacer(height: int) -> str:
    """Respiro vertical entre blocos (nenhum cliente de e-mail respeita margin)."""
    if height <= 0:
        return ""
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
        f'<td height="{height}" style="height:{height}px; line-height:{height}px; font-size:0;">'
        "&nbsp;</td></tr></table>"
    )


def badge(text: str, tone: Tone) -> str:
    """Pílula de status acima do card (ex.: PAGAMENTO APROVADO)."""
    return (
        '<tr><td align="center" style="padding-bottom:14px;">'
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr>'
        f'<td style="background-color:{tone.badge_bg}; border:1px solid {tone.badge_border};'
        f' border-radius:30px; padding:8px 14px; font-family:{FONT}; font-size:11px;'
        f' line-height:14px; font-weight:700; letter-spacing:1.4px; color:{tone.accent};">'
        f"{text}</td>"
        "</tr></table></td></tr>"
    )


def eyebrow(text: str, tone: Tone) -> str:
    """Rótulo curto que abre o card."""
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
        f'<td style="font-family:{FONT}; font-size:12px; line-height:18px; font-weight:700;'
        f' letter-spacing:1.5px; color:{tone.accent}; padding-bottom:12px;">{text}</td>'
        "</tr></table>"
    )


def headline(text: str) -> str:
    """Título principal do e-mail (uma frase, sem ponto final longo)."""
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
        f'<td style="font-family:{FONT}; font-size:29px; line-height:37px; font-weight:800;'
        f' color:{WHITE}; padding-bottom:18px;">{text}</td>'
        "</tr></table>"
    )


def paragraph(html: str, *, top: int = 0, bottom: int = 14) -> str:
    """Parágrafo de corpo com espaçamento controlado nos dois lados."""
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
        f'<td style="font-family:{FONT}; font-size:16px; line-height:25px; color:{TEXT};'
        f' padding-top:{top}px; padding-bottom:{bottom}px;">{html}</td>'
        "</tr></table>"
    )


def strong(text: str, tone: Tone | None = None) -> str:
    """Destaque inline em branco ou na cor de acento."""
    color = tone.accent if tone else WHITE
    return f'<strong style="color:{color};">{text}</strong>'


def stat_box(left_label: str, left_value: str, right_label: str, right_value: str, tone: Tone) -> str:
    """Caixa de resumo com dois campos (ex.: plano + valor)."""
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"'
        f' style="background-color:{INNER}; border:1px solid {INNER_BORDER}; border-radius:12px;">'
        '<tr><td style="padding:20px 22px;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
        '<td width="50%" valign="top" style="padding-right:10px;">'
        f'<div style="font-family:{FONT}; font-size:10px; line-height:15px; font-weight:700;'
        f' letter-spacing:1.3px; color:{DIM};">{left_label}</div>'
        f'<div style="padding-top:6px; font-family:{FONT}; font-size:15px; line-height:21px;'
        f' font-weight:700; color:{WHITE};">{left_value}</div></td>'
        '<td width="50%" valign="top" align="right" style="padding-left:10px;">'
        f'<div style="font-family:{FONT}; font-size:10px; line-height:15px; font-weight:700;'
        f' letter-spacing:1.3px; color:{DIM};">{right_label}</div>'
        f'<div style="padding-top:6px; font-family:{FONT}; font-size:15px; line-height:21px;'
        f' font-weight:700; color:{tone.accent};">{right_value}</div></td>'
        "</tr></table></td></tr></table>"
    )


def divider(*, top: int = 30) -> str:
    """Linha divisória interna do card."""
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
        f'<td style="padding:{top}px 0 0 0; border-bottom:1px solid {LINE};"></td>'
        "</tr></table>"
    )


def section_title(text: str, *, top: int = 28) -> str:
    """Subtítulo de seção dentro do card."""
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
        f'<td style="padding-top:{top}px; font-family:{FONT}; font-size:19px; line-height:26px;'
        f' font-weight:700; color:{WHITE};">{text}</td>'
        "</tr></table>"
    )


def steps(items: list[tuple[str, str]], tone: Tone) -> str:
    """Passos numerados; o primeiro vem preenchido para puxar o olho."""
    rows: list[str] = []
    for index, (title, description) in enumerate(items, start=1):
        first = index == 1
        bubble_bg = tone.accent if first else tone.step_bg
        bubble_border = "none" if first else f"1px solid {tone.step_border}"
        bubble_color = tone.ink if first else tone.accent
        bottom = 5 if index == len(items) else 20
        rows.append(
            '<tr>'
            '<td width="42" valign="top">'
            '<table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr>'
            f'<td align="center" style="width:32px; height:32px; background-color:{bubble_bg};'
            f' border:{bubble_border}; border-radius:50%; font-family:{FONT}; font-size:14px;'
            f' line-height:32px; font-weight:800; color:{bubble_color};">{index}</td>'
            "</tr></table></td>"
            f'<td valign="top" style="padding:1px 0 {bottom}px 0;">'
            f'<div style="font-family:{FONT}; font-size:15px; line-height:20px; font-weight:700;'
            f' color:{WHITE};">{title}</div>'
            f'<div style="padding-top:4px; font-family:{FONT}; font-size:13px; line-height:20px;'
            f' color:{MUTED};">{description}</div></td>'
            "</tr>"
        )
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
        '<td style="padding-top:18px;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">'
        + "".join(rows)
        + "</table></td></tr></table>"
    )


def bullets(items: list[str], tone: Tone, *, top: int = 16) -> str:
    """Lista curta com marcador na cor de acento."""
    rows: list[str] = []
    for index, item in enumerate(items):
        bottom = 0 if index == len(items) - 1 else 10
        rows.append(
            "<tr>"
            f'<td width="20" valign="top" style="padding:0 0 {bottom}px 0; font-family:{FONT};'
            f' font-size:15px; line-height:23px; color:{tone.accent};">&bull;</td>'
            f'<td valign="top" style="padding:0 0 {bottom}px 0; font-family:{FONT}; font-size:15px;'
            f' line-height:23px; color:{TEXT};">{item}</td>'
            "</tr>"
        )
    return (
        _spacer(top)
        + '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">'
        + "".join(rows)
        + "</table>"
    )


def notice(title: str, body: str, tone: Tone, *, top: int = 22) -> str:
    """Caixa de aviso destacada (motivos, segurança, prazo)."""
    return (
        _spacer(top)
        + '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"'
        f' style="background-color:{tone.badge_bg}; border:1px solid {tone.badge_border};'
        ' border-radius:12px;">'
        '<tr><td style="padding:18px 20px;">'
        f'<div style="font-family:{FONT}; font-size:11px; line-height:16px; font-weight:700;'
        f' letter-spacing:1.3px; color:{tone.accent};">{title}</div>'
        f'<div style="padding-top:8px; font-family:{FONT}; font-size:14px; line-height:22px;'
        f' color:{TEXT};">{body}</div>'
        "</td></tr></table>"
    )


def cta(href: str, label: str, tone: Tone, *, hint: str = "", top: int = 30) -> str:
    """Botão principal em tabela (renderiza em Outlook) + legenda opcional."""
    hint_row = (
        "<tr>"
        f'<td align="center" style="font-family:{FONT}; font-size:12px; line-height:18px;'
        f' color:{DIM}; padding-bottom:30px;">{hint}</td>'
        "</tr>"
        if hint
        else '<tr><td style="padding-bottom:18px; font-size:0; line-height:0;">&nbsp;</td></tr>'
    )
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">'
        f'<tr><td align="center" style="padding:{top}px 0 12px 0;">'
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr>'
        f'<td align="center" bgcolor="{tone.accent}" style="border-radius:9px;">'
        f'<a href="{href}" style="display:inline-block; padding:17px 36px; font-family:{FONT};'
        " font-size:15px; line-height:20px; font-weight:800; letter-spacing:0.3px;"
        f' color:{tone.ink}; text-decoration:none; border-radius:9px;">{label}</a></td>'
        "</tr></table></td></tr>" + hint_row + "</table>"
    )


def raw_link_box(url_variable: str, tone: Tone, *, top: int = 0) -> str:
    """Mostra a URL em texto para quem não consegue clicar no botão."""
    return (
        _spacer(top)
        + '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"'
        f' style="background-color:{INNER}; border:1px solid {INNER_BORDER}; border-radius:10px;">'
        '<tr><td style="padding:14px 16px;">'
        f'<div style="font-family:{FONT}; font-size:10px; line-height:15px; font-weight:700;'
        f' letter-spacing:1.3px; color:{DIM};">OU COPIE ESTE ENDEREÇO</div>'
        f'<div style="padding-top:7px; font-family:{FONT}; font-size:12px; line-height:19px;'
        f' color:{tone.accent}; word-break:break-all;">{{{{{url_variable}}}}}</div>'
        "</td></tr></table>"
    )


def foot_link(question: str, label: str, href: str, tone: Tone) -> str:
    """Rodapé interno do card: uma pergunta e um caminho alternativo."""
    return (
        f'<tr><td style="background-color:{CARD_FOOT}; border-top:1px solid {CARD_BORDER};'
        ' padding:23px 34px 26px 34px;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">'
        f'<tr><td align="center" style="font-family:{FONT}; font-size:13px; line-height:20px;'
        f' color:#789599;">{question}</td></tr>'
        '<tr><td align="center" style="padding-top:7px;">'
        f'<a href="{href}" style="font-family:{FONT}; font-size:14px; line-height:20px;'
        f' font-weight:700; color:{tone.accent}; text-decoration:none;">{label} &rarr;</a>'
        "</td></tr></table></td></tr>"
    )


def foot_text(question: str, answer: str) -> str:
    """Rodapé interno sem link (ex.: 'responda este e-mail')."""
    return (
        f'<tr><td style="background-color:{CARD_FOOT}; border-top:1px solid {CARD_BORDER};'
        ' padding:23px 34px 26px 34px;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">'
        f'<tr><td align="center" style="font-family:{FONT}; font-size:13px; line-height:20px;'
        f' color:#789599;">{question}</td></tr>'
        f'<tr><td align="center" style="padding-top:7px; font-family:{FONT}; font-size:14px;'
        f' line-height:21px; font-weight:700; color:{EXTERNAL_STRONG};">{answer}</td></tr>'
        "</table></td></tr>"
    )


def document(
    *,
    title: str,
    status: str,
    body: str,
    foot: str,
    tone: Tone,
    account_label: str = "Acesso vinculado ao e-mail",
    legal: str = "Este e-mail foi enviado automaticamente.<br>Não compartilhe links de acesso.",
) -> str:
    """
    Monta o documento completo de um e-mail transacional.

    Args:
        title: Título da aba/preview do cliente de e-mail.
        status: Texto da pílula de status acima do card.
        body: HTML do miolo do card (helpers deste módulo).
        foot: Linha final dentro do card (``foot_link`` ou ``foot_text``).
        tone: Paleta de acento do e-mail.
        account_label: Rótulo do bloco que mostra o e-mail do destinatário.
        legal: Aviso legal do rodapé externo.

    Returns:
        HTML completo, pronto para render de ``{{variaveis}}``.
    """
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="x-apple-disable-message-reformatting">
  <title>{title}</title>
</head>

<body style="margin:0; padding:0; background-color:{BG_PAGE}; font-family:{FONT};">

  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%; background-color:{BG_PAGE};">
    <tr>
      <td align="center" style="padding:38px 16px;">

        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%; max-width:560px;">

          <!-- MARCA -->
          <tr>
            <td align="center" style="padding:0 0 22px 0;">
              <table role="presentation" cellspacing="0" cellpadding="0" border="0">
                <tr>
                  <td align="center" style="font-family:{FONT}; font-size:31px; line-height:36px; font-weight:800; letter-spacing:2px; color:{CYAN.accent};">
                    EL CAPO
                  </td>
                </tr>
                <tr>
                  <td align="center" style="padding-top:3px; font-family:{FONT}; font-size:10px; line-height:16px; font-weight:700; letter-spacing:5px; color:{DIM};">
                    AUTOBOT
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- STATUS -->
          {badge(status, tone)}

          <!-- CARD PRINCIPAL -->
          <tr>
            <td style="background-color:{CARD}; border:1px solid {CARD_BORDER}; border-radius:16px; overflow:hidden;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">

                <tr>
                  <td height="4" style="height:4px; line-height:4px; font-size:0; background-color:{tone.accent};">
                    &nbsp;
                  </td>
                </tr>

                <tr>
                  <td style="padding:38px 34px 0 34px;">
                    {body}
                  </td>
                </tr>

                {foot}

              </table>
            </td>
          </tr>

          <!-- CONTA -->
          <tr>
            <td align="center" style="padding:22px 20px 0 20px;">
              <div style="font-family:{FONT}; font-size:12px; line-height:19px; color:{EXTERNAL};">
                {account_label}
              </div>
              <div style="padding-top:4px; font-family:{FONT}; font-size:13px; line-height:20px; color:{EXTERNAL_STRONG};">
                {{{{customer_email}}}}
              </div>
            </td>
          </tr>

          <!-- RODAPÉ -->
          <tr>
            <td align="center" style="padding:22px 20px 0 20px; font-family:{FONT}; font-size:11px; line-height:18px; color:{FAINT};">
              {legal}
            </td>
          </tr>

          <tr>
            <td align="center" style="padding:12px 20px 0 20px; font-family:{FONT}; font-size:10px; line-height:16px; letter-spacing:1px; color:{FAINTER};">
              {{{{company_name}}}}
            </td>
          </tr>

        </table>

      </td>
    </tr>
  </table>

</body>
</html>"""
