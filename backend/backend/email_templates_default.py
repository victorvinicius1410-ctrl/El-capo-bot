"""
Conteúdo de fábrica dos e-mails transacionais (assunto + HTML por evento).

Cada corpo é montado com os componentes de :mod:`backend.email_layout`, então a
marca, o espaçamento e os estados de cor são idênticos em todos os e-mails.

Regras de conteúdo respeitadas aqui:

* Nenhum ``href`` aponta para variável que o backend pode não preencher.
  ``checkout_url`` e ``support_url`` nunca chegam no payload dos eventos, então
  não viram link — o caminho de suporte é "responda este e-mail" (o remetente é
  a caixa de suporte real).
* ``login_url``, ``recovery_url`` e ``first_access_url`` são os únicos destinos
  garantidos; ``first_access_url`` só é usado no evento que o exige.
* Valores que podem faltar (``amount_display``, ``expires_at_br``,
  ``expires_in_human``, ``plan_name_display``) têm fallback textual em
  ``EmailService.build_variables`` — nunca aparecem em branco.
"""

from __future__ import annotations

from backend.email_layout import (
    AMBER,
    CYAN,
    SLATE,
    bullets,
    cta,
    divider,
    document,
    eyebrow,
    foot_link,
    foot_text,
    headline,
    notice,
    paragraph,
    raw_link_box,
    section_title,
    stat_box,
    steps,
    strong,
)
from backend.webhook_models import DomainEventType


DEFAULT_SUBJECTS: dict[DomainEventType, str] = {
    DomainEventType.PURCHASE_COMPLETED: "Seu acesso ao ElCapo AutoBot está liberado",
    DomainEventType.PURCHASE_EXISTING_ACCOUNT: "Compra confirmada — seu acesso já está liberado",
    DomainEventType.SUBSCRIPTION_RENEWED: "Assinatura renovada — seu acesso continua ativo",
    DomainEventType.SUBSCRIPTION_CANCELED: "Sua assinatura do ElCapo foi cancelada",
    DomainEventType.PAYMENT_REFUNDED: "Reembolso confirmado — ElCapo AutoBot",
    DomainEventType.PAYMENT_CHARGEBACK: "Contestação registrada na sua compra — ElCapo",
    DomainEventType.SUBSCRIPTION_PAYMENT_FAILED: "Seu pagamento não foi aprovado — acesso suspenso",
    DomainEventType.TRIAL_STARTED: "Seu teste do ElCapo AutoBot começou",
    DomainEventType.TRIAL_ENDED: "Seu teste do ElCapo terminou — não perca o que você configurou",
    DomainEventType.PASSWORD_RECOVERY_REQUESTED: "Redefinir a senha da sua conta ElCapo",
}


# --------------------------------------------------------------------------- #
# 1. Compra aprovada — conta nova (1º acesso)
# --------------------------------------------------------------------------- #
_PURCHASE_COMPLETED = document(
    title="Seu ElCapo foi liberado",
    status="PAGAMENTO APROVADO &bull; ACESSO LIBERADO",
    tone=CYAN,
    body=(
        eyebrow("BEM-VINDO AO EL CAPO", CYAN)
        + headline("Seu robô já está esperando por você.")
        + paragraph("Olá, " + strong("{{customer_name}}") + ".", bottom=10)
        + paragraph(
            "Seu pagamento foi confirmado e o seu acesso ao "
            + strong("ElCapo AutoBot", CYAN)
            + " está oficialmente liberado.",
            bottom=28,
        )
        + stat_box("SEU PLANO", "{{plan_name_display}}", "PAGAMENTO", "{{amount_display}}", CYAN)
        + divider()
        + section_title("O que acontece agora?")
        + steps(
            [
                ("Crie sua senha", "Ative sua conta e proteja seu acesso ao painel."),
                ("Entre no painel", "Acesse o ambiente do ElCapo AutoBot."),
                ("Prepare o ElCapo", "Siga as instruções do painel para iniciar sua configuração."),
            ],
            CYAN,
        )
        + cta(
            "{{first_access_cta_url}}",
            "ATIVAR MEU EL CAPO",
            CYAN,
            hint="Use o botão acima para definir sua senha de primeiro acesso.",
        )
    ),
    foot=foot_link("Já realizou o primeiro acesso?", "Entrar no painel", "{{login_url}}", CYAN),
)


# --------------------------------------------------------------------------- #
# 2. Compra aprovada — cliente que já tinha conta
# --------------------------------------------------------------------------- #
_PURCHASE_EXISTING_ACCOUNT = document(
    title="Compra confirmada",
    status="PAGAMENTO APROVADO &bull; ACESSO ATUALIZADO",
    tone=CYAN,
    body=(
        eyebrow("COMPRA CONFIRMADA", CYAN)
        + headline("Tudo certo. Seu acesso já está liberado.")
        + paragraph("Olá, " + strong("{{customer_name}}") + ".", bottom=10)
        + paragraph(
            "Recebemos seu pagamento e o acesso ao "
            + strong("ElCapo AutoBot", CYAN)
            + " já está ativo na sua conta.",
            bottom=28,
        )
        + stat_box("SEU PLANO", "{{plan_name_display}}", "PAGAMENTO", "{{amount_display}}", CYAN)
        + notice(
            "VOCÊ NÃO PRECISA CRIAR NADA",
            "Você já tinha conta no ElCapo, então continue entrando com a "
            + strong("mesma senha de sempre", CYAN)
            + ". Nenhuma configuração sua foi alterada.",
            CYAN,
        )
        + cta(
            "{{login_url}}",
            "ENTRAR NO PAINEL",
            CYAN,
            hint="Seu acesso já está valendo neste momento.",
            top=24,
        )
    ),
    foot=foot_link(
        "Esqueceu sua senha?",
        "Recuperar na tela de login",
        "{{login_url}}",
        CYAN,
    ),
)


# --------------------------------------------------------------------------- #
# 3. Assinatura renovada
# --------------------------------------------------------------------------- #
_SUBSCRIPTION_RENEWED = document(
    title="Assinatura renovada",
    status="RENOVAÇÃO CONFIRMADA &bull; ACESSO ATIVO",
    tone=CYAN,
    body=(
        eyebrow("RENOVAÇÃO CONFIRMADA", CYAN)
        + headline("Seu acesso continua sem interrupção.")
        + paragraph("Olá, " + strong("{{customer_name}}") + ".", bottom=10)
        + paragraph(
            "Recebemos o pagamento da sua renovação. Seu acesso ao "
            + strong("ElCapo AutoBot", CYAN)
            + " segue ativo, e você não precisa fazer nada.",
            bottom=28,
        )
        + stat_box("SEU PLANO", "{{plan_name_display}}", "PAGAMENTO", "{{amount_display}}", CYAN)
        + divider()
        + section_title("O que continua igual")
        + bullets(
            [
                "A mesma conta e a mesma senha de sempre.",
                "Todas as suas configurações e preferências salvas.",
                "Seu histórico completo dentro do painel.",
            ],
            CYAN,
            top=14,
        )
        + cta(
            "{{login_url}}",
            "ACESSAR O PAINEL",
            CYAN,
            hint="Seu login e sua senha continuam os mesmos.",
            top=28,
        )
    ),
    foot=foot_text(
        "Algum problema com esta cobrança?",
        "Responda este e-mail que a gente resolve",
    ),
)


# --------------------------------------------------------------------------- #
# 4. Assinatura cancelada (win-back)
# --------------------------------------------------------------------------- #
_SUBSCRIPTION_CANCELED = document(
    title="Assinatura cancelada",
    status="ASSINATURA ENCERRADA",
    tone=SLATE,
    body=(
        eyebrow("CANCELAMENTO CONFIRMADO", SLATE)
        + headline("Sua assinatura foi cancelada.")
        + paragraph("Olá, " + strong("{{customer_name}}") + ".", bottom=10)
        + paragraph(
            "Confirmamos o cancelamento do plano "
            + strong("{{plan_name_display}}")
            + ". A partir de agora, o acesso ao painel do ElCapo AutoBot fica encerrado.",
            bottom=28,
        )
        + notice(
            "SUA CONTA NÃO FOI APAGADA",
            "Suas configurações e seu histórico continuam guardados. Se você voltar, "
            "encontra tudo exatamente como deixou — sem começar do zero.",
            SLATE,
        )
        + divider(top=26)
        + section_title("Mudou de ideia?")
        + paragraph(
            "Reativar leva menos de um minuto: entre na sua conta e escolha um plano de novo.",
            top=12,
            bottom=0,
        )
        + cta(
            "{{login_url}}",
            "REATIVAR MEU ACESSO",
            CYAN,
            hint="Você entra com o mesmo login de sempre.",
            top=24,
        )
    ),
    foot=foot_text(
        "Cancelou por engano ou teve algum problema?",
        "Responda este e-mail — a gente resolve",
    ),
)


# --------------------------------------------------------------------------- #
# 5. Reembolso
# --------------------------------------------------------------------------- #
_PAYMENT_REFUNDED = document(
    title="Reembolso confirmado",
    status="REEMBOLSO CONFIRMADO",
    tone=SLATE,
    body=(
        eyebrow("REEMBOLSO PROCESSADO", SLATE)
        + headline("Seu reembolso já foi enviado.")
        + paragraph("Olá, " + strong("{{customer_name}}") + ".", bottom=10)
        + paragraph(
            "Processamos o reembolso da sua compra do plano "
            + strong("{{plan_name_display}}")
            + ". Não é preciso fazer mais nada da sua parte.",
            bottom=28,
        )
        + stat_box(
            "PLANO",
            "{{plan_name_display}}",
            "VALOR REEMBOLSADO",
            "{{amount_display}}",
            SLATE,
        )
        + notice(
            "SOBRE O PRAZO DE COMPENSAÇÃO",
            "O valor volta pelo mesmo meio de pagamento usado na compra. O tempo até "
            "aparecer no seu extrato ou fatura depende do seu banco ou da bandeira do "
            "cartão — nós já fizemos a nossa parte.",
            SLATE,
        )
        + divider(top=26)
        + section_title("E o seu acesso?")
        + paragraph(
            "Com o reembolso, o acesso ao painel foi encerrado. Sua conta continua "
            "existindo: se um dia quiser voltar, é só entrar e escolher um plano.",
            top=12,
            bottom=0,
        )
        + cta(
            "{{login_url}}",
            "ACESSAR MINHA CONTA",
            SLATE,
            hint="Você entra com o mesmo e-mail de sempre.",
            top=24,
        )
    ),
    foot=foot_text(
        "Ficou alguma dúvida sobre este reembolso?",
        "Responda este e-mail",
    ),
)


# --------------------------------------------------------------------------- #
# 6. Chargeback / contestação
# --------------------------------------------------------------------------- #
_PAYMENT_CHARGEBACK = document(
    title="Contestação registrada",
    status="PAGAMENTO CONTESTADO &bull; ACESSO SUSPENSO",
    tone=AMBER,
    body=(
        eyebrow("CONTESTAÇÃO REGISTRADA", AMBER)
        + headline("Recebemos uma contestação da sua compra.")
        + paragraph("Olá, " + strong("{{customer_name}}") + ".", bottom=10)
        + paragraph(
            "Seu banco ou a operadora do cartão abriu uma disputa sobre este pagamento. "
            "Enquanto ela estiver em análise, o acesso ao ElCapo AutoBot fica suspenso.",
            bottom=28,
        )
        + stat_box("PLANO", "{{plan_name_display}}", "VALOR CONTESTADO", "{{amount_display}}", AMBER)
        + divider(top=26)
        + section_title("Se você não reconhece esta contestação")
        + bullets(
            [
                "Responda este e-mail contando o que aconteceu.",
                "Verifique com seu banco se a disputa foi aberta por engano.",
                "Assim que a contestação for encerrada, restauramos seu acesso.",
            ],
            AMBER,
            top=14,
        )
        + notice(
            "QUERO RESOLVER RÁPIDO",
            "Basta responder este e-mail. Nós acompanhamos o caso junto ao processador de "
            "pagamento e te avisamos assim que houver uma definição.",
            AMBER,
        )
        + cta(
            "{{login_url}}",
            "VER MINHA CONTA",
            AMBER,
            hint="No painel você acompanha a situação do seu acesso.",
            top=24,
        )
    ),
    foot=foot_text(
        "Foi você mesmo que abriu a contestação?",
        "Responda este e-mail para revisarmos juntos",
    ),
)


# --------------------------------------------------------------------------- #
# 7. Falha de cobrança
# --------------------------------------------------------------------------- #
_SUBSCRIPTION_PAYMENT_FAILED = document(
    title="Pagamento não aprovado",
    status="PAGAMENTO NÃO APROVADO &bull; AÇÃO NECESSÁRIA",
    tone=AMBER,
    body=(
        eyebrow("AÇÃO NECESSÁRIA", AMBER)
        + headline("Não conseguimos confirmar seu pagamento.")
        + paragraph("Olá, " + strong("{{customer_name}}") + ".", bottom=10)
        + paragraph(
            "A cobrança do plano "
            + strong("{{plan_name_display}}")
            + " não foi aprovada, e por isso seu acesso ao ElCapo AutoBot está suspenso "
            "até a regularização.",
            bottom=28,
        )
        + stat_box("PLANO", "{{plan_name_display}}", "VALOR", "{{amount_display}}", AMBER)
        + divider(top=26)
        + section_title("Por que isso costuma acontecer")
        + bullets(
            [
                "Limite ou saldo insuficiente no momento da cobrança.",
                "Dados do cartão vencidos ou digitados de forma diferente.",
                "Bloqueio preventivo do banco para compras on-line.",
            ],
            AMBER,
            top=14,
        )
        + notice(
            "É RÁPIDO DE RESOLVER",
            "Entre no painel e refaça o pagamento com um cartão válido ou por Pix. "
            "Assim que a aprovação chegar, o seu acesso volta na hora.",
            AMBER,
        )
        + cta(
            "{{login_url}}",
            "REGULARIZAR PAGAMENTO",
            AMBER,
            hint="Seu acesso é liberado automaticamente após a aprovação.",
            top=24,
        )
    ),
    foot=foot_text(
        "Já pagou e mesmo assim recebeu este aviso?",
        "Responda este e-mail com o comprovante",
    ),
)


# --------------------------------------------------------------------------- #
# 8. Teste iniciado
# --------------------------------------------------------------------------- #
_TRIAL_STARTED = document(
    title="Seu teste começou",
    status="TESTE ATIVADO &bull; ACESSO LIBERADO",
    tone=CYAN,
    body=(
        eyebrow("SEU TESTE COMEÇOU AGORA", CYAN)
        + headline("O ElCapo já está liberado para você.")
        + paragraph("Olá, " + strong("{{customer_name}}") + ".", bottom=10)
        + paragraph(
            "Seu período de teste do "
            + strong("ElCapo AutoBot", CYAN)
            + " está ativo. Use este tempo para conhecer o painel por dentro, sem "
            "compromisso nenhum.",
            bottom=28,
        )
        + stat_box("SEU TESTE VAI ATÉ", "{{expires_at_br}}", "ACESSO", "Liberado", CYAN)
        + divider()
        + section_title("Comece por aqui")
        + steps(
            [
                ("Entre no painel", "Use o mesmo e-mail que recebeu esta mensagem."),
                ("Configure sua conta", "Siga as instruções do painel para deixar tudo pronto."),
                ("Acompanhe de perto", "Veja o ElCapo trabalhando e entenda o que ele faz."),
            ],
            CYAN,
        )
        + cta(
            "{{login_url}}",
            "COMEÇAR AGORA",
            CYAN,
            hint="Quanto antes você configurar, mais tempo de teste aproveita.",
        )
    ),
    foot=foot_text(
        "Travou em alguma etapa da configuração?",
        "Responda este e-mail que a gente te orienta",
    ),
)


# --------------------------------------------------------------------------- #
# 9. Teste encerrado
# --------------------------------------------------------------------------- #
_TRIAL_ENDED = document(
    title="Seu teste terminou",
    status="PERÍODO DE TESTE ENCERRADO",
    tone=SLATE,
    body=(
        eyebrow("SEU TESTE CHEGOU AO FIM", SLATE)
        + headline("Seu acesso de teste foi pausado.")
        + paragraph("Olá, " + strong("{{customer_name}}") + ".", bottom=10)
        + paragraph(
            "O período de teste do "
            + strong("ElCapo AutoBot", CYAN)
            + " terminou e o acesso ao painel está pausado a partir de agora.",
            bottom=28,
        )
        + notice(
            "NADA DO QUE VOCÊ FEZ FOI PERDIDO",
            "Suas configurações e seu histórico continuam salvos na sua conta. "
            "Ao ativar um plano, você volta exatamente de onde parou.",
            SLATE,
        )
        + divider(top=26)
        + section_title("O que você recupera ao ativar")
        + bullets(
            [
                "O painel completo do ElCapo AutoBot liberado de novo.",
                "As configurações que você já deixou prontas no teste.",
                "Acompanhamento contínuo, sem prazo para acabar.",
            ],
            SLATE,
            top=14,
        )
        + cta(
            "{{login_url}}",
            "ATIVAR MEU ACESSO",
            CYAN,
            hint="Entre com o mesmo login que usou no teste.",
            top=24,
        )
    ),
    foot=foot_text(
        "Faltou testar alguma coisa antes de decidir?",
        "Responda este e-mail e conte pra gente",
    ),
)


# --------------------------------------------------------------------------- #
# 10. Recuperação de senha
# --------------------------------------------------------------------------- #
_PASSWORD_RECOVERY = document(
    title="Redefinir sua senha",
    status="SOLICITAÇÃO DE NOVA SENHA",
    tone=CYAN,
    account_label="Redefinição solicitada para",
    legal=(
        "Este e-mail foi enviado automaticamente.<br>"
        "Nunca compartilhe este link — ele dá acesso à sua conta."
    ),
    body=(
        eyebrow("RECUPERAÇÃO DE ACESSO", CYAN)
        + headline("Vamos criar uma nova senha.")
        + paragraph(
            "Recebemos um pedido para redefinir a senha da sua conta no "
            + strong("ElCapo AutoBot", CYAN)
            + ". É só clicar no botão abaixo e escolher a nova senha.",
            bottom=4,
        )
        + cta(
            "{{recovery_url}}",
            "CRIAR NOVA SENHA",
            CYAN,
            hint="Este link vale por {{expires_in_human}} e só pode ser usado uma vez.",
            top=24,
        )
        + raw_link_box("recovery_url", CYAN, top=4)
        + divider(top=26)
        + section_title("Não foi você quem pediu?")
        + paragraph(
            "Pode ignorar esta mensagem com tranquilidade. Sua senha atual continua "
            "valendo e ninguém consegue entrar na sua conta sem usar o link acima.",
            top=12,
            bottom=34,
        )
    ),
    foot=foot_link(
        "Lembrou da sua senha?",
        "Entrar no painel",
        "{{login_url}}",
        CYAN,
    ),
)


DEFAULT_BODIES: dict[DomainEventType, str] = {
    DomainEventType.PURCHASE_COMPLETED: _PURCHASE_COMPLETED,
    DomainEventType.PURCHASE_EXISTING_ACCOUNT: _PURCHASE_EXISTING_ACCOUNT,
    DomainEventType.SUBSCRIPTION_RENEWED: _SUBSCRIPTION_RENEWED,
    DomainEventType.SUBSCRIPTION_CANCELED: _SUBSCRIPTION_CANCELED,
    DomainEventType.PAYMENT_REFUNDED: _PAYMENT_REFUNDED,
    DomainEventType.PAYMENT_CHARGEBACK: _PAYMENT_CHARGEBACK,
    DomainEventType.SUBSCRIPTION_PAYMENT_FAILED: _SUBSCRIPTION_PAYMENT_FAILED,
    DomainEventType.TRIAL_STARTED: _TRIAL_STARTED,
    DomainEventType.TRIAL_ENDED: _TRIAL_ENDED,
    DomainEventType.PASSWORD_RECOVERY_REQUESTED: _PASSWORD_RECOVERY,
}
