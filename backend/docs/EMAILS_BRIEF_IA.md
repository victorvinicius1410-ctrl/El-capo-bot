# Brief para IA — Templates de e-mail ElCapo AutoBot

Use este documento como **prompt completo** em qualquer IA (ChatGPT, Claude, Cursor, etc.)
para ela criar/refinar os e-mails do painel **Admin → E-mails**.

Cole o bloco abaixo (ou o arquivo inteiro) e peça:  
**“Gere os 10 templates seguindo este brief.”**

---

## Prompt para a IA (copie a partir daqui)

```text
Você é um copywriter + designer de e-mail HTML para o produto ElCapo AutoBot
(robô de operações / painel SaaS brasileiro).

Sua tarefa: criar ASSUNTO + HTML completo para cada evento de e-mail do sistema.
Eu vou colar o resultado no Admin → E-mails do ElCapo (campos Assunto e HTML).

### Produto e tom
- Marca: ElCapo AutoBot
- Domínio do painel: https://app.elcapobot.online
- Remetente tipico: ElCapo AutoBot <suporte@visaodarota.com>
- Idioma: português do Brasil
- Tom: direto, confiante, profissional, sem enrolação; premium sem ser frio
- Público: traders / clientes de assinatura
- Evite emojis em excesso; no máximo 0–1 por e-mail, se fizer sentido
- Não invente nomes de planos; use sempre {{plan_name}}
- Não invente URLs; use só as variáveis {{...}} listadas

### Regras técnicas OBRIGATÓRIAS
1. Entregar, para CADA evento:
   - código do evento
   - assunto (texto puro, máx. ~80 caracteres, pode usar {{variaveis}})
   - html_body: HTML COMPLETO (DOCTYPE + html + body), pronto para colar
2. HTML de e-mail clássico:
   - layout em <table role="presentation">
   - CSS SOMENTE inline (style="...")
   - fontes web-safe: Arial, Helvetica, sans-serif
   - largura do conteúdo ~560px
   - compatível com Gmail / Outlook / Apple Mail
3. NÃO usar:
   - JavaScript
   - CSS externo ou <style> complexo (evite; prefira inline)
   - frameworks (React, Tailwind classes)
   - Jinja/Liquid/{% if %} — o motor só substitui {{variavel}}
   - variáveis que não estejam na lista oficial
4. Variáveis oficiais (use exatamente assim, com chaves duplas):

   {{customer_name}}     → nome do cliente
   {{customer_email}}    → e-mail do cliente
   {{plan_name}}         → nome da oferta/plano
   {{amount}}            → valor (ex.: 147.90)
   {{currency}}          → moeda (ex.: BRL)
   {{first_access_url}}  → link para definir senha no 1º acesso
   {{recovery_url}}      → link de recuperação de senha
   {{expires_at}}        → expiração do link (ISO)
   {{expires_in_seconds}}→ segundos até expirar
   {{login_url}}         → login do painel
   {{company_name}}      → ElCapo AutoBot
   {{event_type}}        → código do evento
   {{checkout_url}}      → checkout Cakto
   {{support_url}}       → suporte (WhatsApp/site)

   Aliases aceitos (opcional): {{name}}, {{email}}, {{reset_url}}

5. Identidade visual sugerida (pode evoluir, mas mantenha contraste):
   - fundo página: #0b1220
   - card: #121a2b, borda #1e3a4a, radius ~12px
   - título/marca: #7ef0f3
   - texto: #c9e4e6
   - rodapé discreto: #6f8b8e
   - CTA: botão ou link em #7ef0f3 / fundo ciano forte se botão

6. Cada e-mail deve ter:
   - saudação com {{customer_name}}
   - 1 mensagem clara (o que aconteceu)
   - 1 ação principal (CTA) quando fizer sentido
   - rodapé: “Este e-mail foi enviado automaticamente. Não compartilhe links de acesso.”

### Os 10 eventos (gere todos)

1) purchase.completed — Compra / boas-vindas (conta nova)
   Objetivo: confirmar pagamento e dar primeiro acesso (definir senha).
   Variáveis fortes: customer_name, plan_name, amount, currency, first_access_url, first_access_block, login_url
   CTA: definir senha ({{first_access_url}}) e entrar ({{login_url}})
   Assunto exemplo: "Bem-vindo ao ElCapo AutoBot"

1b) purchase.existing_account — Compra de quem já tinha conta
   Objetivo: confirmar pagamento sem trocar senha; orientar a entrar com a conta atual.
   Variáveis: customer_name, plan_name, amount, currency, login_url
   CTA: {{login_url}} — não usar first_access_url
   Assunto exemplo: "Compra confirmada — sua conta já está liberada"

2) subscription.renewed — Assinatura renovada
   Objetivo: confirmar renovação e valor; dizer que o acesso continua.
   Variáveis: customer_name, plan_name, amount, currency, login_url
   CTA: {{login_url}}

3) subscription.canceled — Assinatura cancelada
   Objetivo: informar cancelamento sem drama; oferecer retorno.
   Variáveis: customer_name, plan_name, login_url, checkout_url (opcional)

4) payment.refunded — Reembolso
   Objetivo: confirmar valor estornado.
   Variáveis: customer_name, amount, currency, plan_name

5) payment.chargeback — Chargeback / contestação
   Objetivo: avisar contestação; tom sério e útil; oferecer suporte.
   Variáveis: customer_name, amount, currency, support_url

6) subscription.payment_failed — Falha no pagamento
   Objetivo: urgência educada; pedir atualização de pagamento.
   Variáveis: customer_name, plan_name, login_url, checkout_url

7) trial.started — Teste iniciado
   Objetivo: celebrar início do trial e orientar o primeiro acesso.
   Variáveis: customer_name, expires_at, login_url, first_access_url

8) trial.ended — Teste encerrado
   Objetivo: avisar fim do trial e convidar à assinatura.
   Variáveis: customer_name, checkout_url, login_url, plan_name

9) user.password_recovery_requested — Recuperação de senha
   Objetivo: link seguro para redefinir senha; reforçar que o link expira.
   Variáveis: customer_name, recovery_url (ou reset_url), expires_in_seconds, expires_at
   CTA obrigatório: {{recovery_url}}
   Aviso: se não foi o usuário, ignore o e-mail.

### Formato de resposta
Para cada evento, use exatamente este formato markdown:

### `codigo.do.evento` — Título amigável
**Assunto:**
```
texto do assunto com {{variaveis}} se útil
```

**HTML:**
```html
<!DOCTYPE html>
...html completo...
```

No final, entregue um checklist curto:
- [ ] 10 assuntos
- [ ] 10 HTMLs completos
- [ ] só variáveis oficiais
- [ ] CTAs corretos por evento
```

---

## Como usar o resultado no ElCapo

1. Abra `https://app.elcapobot.online/admin/emails`
2. Selecione o evento na coluna esquerda
3. Cole o **Assunto**
4. Cole o **HTML** completo
5. Marque **Enviar este email** nos que devem sair de verdade
6. Clique **Salvar**
7. (Opcional) **Enviar teste** — preencha o e-mail de destino e clique em
   Enviar teste (exige SMTP ligado no servidor)

## Checklist rápido para você revisar o que a IA gerou

- [ ] Cada HTML abre sozinho no navegador (arquivo .html)
- [ ] Não há `{{variavel_inventada}}`
- [ ] Não há `{% if %}` / lógica de template
- [ ] Links usam `{{login_url}}`, `{{recovery_url}}`, etc.
- [ ] Assunto curto e em português
- [ ] CTA claro em compra, trial, falha de pagamento e reset de senha
- [ ] Visual escuro/ciano coerente com o painel

## Pedidos úteis (frases prontas)

**Gerar tudo do zero:**
> Siga o brief do ElCapo e gere os 9 e-mails agora.

**Refazer só um:**
> Refaça apenas `purchase.completed` com CTA mais forte e botão maior.

**Tom mais curto:**
> Reescreva todos os assuntos com no máximo 55 caracteres e corpos com no máximo 120 palavras.

**Versão WhatsApp/suporte:**
> Inclua {{support_url}} como link secundário nos e-mails de chargeback e falha de pagamento.

## Referência técnica do produto

Documentação operacional: `docs/EMAILS.md`  
Painel: Admin → **E-mails**  
Motor de variáveis: substitui apenas `{{nome}}` (sem condicionais).

Atualizado em **2026-08-06**.
