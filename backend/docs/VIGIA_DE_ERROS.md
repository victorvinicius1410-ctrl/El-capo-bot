# Vigia de erros

> **Regra (29/09/2026):** todo erro do sistema chega por e-mail ao dono antes do
> cliente perceber. Criado depois que um placar errado só apareceu porque o
> cliente reclamou.

`backend/scripts/vigia_erros.py` roda a cada 5 min
(`/etc/cron.d/elcapo-vigia-erros` → `scripts/vigia-erros.sh`) e manda e-mail
para victorvinicius1410@gmail.com pelo SMTP de produção (`PROD_SMTP_*`).

## O que conta como erro

| Fonte | Regra |
|---|---|
| Containers de produção (`backend-gateway`, `robot-runtime`, `bullex-service`, `webhook-worker`, `webhook-redis`) | `Traceback`; linha com nível `ERROR`/`CRITICAL`; marca com `FAILED`/`ERROR`/`EXCEPTION` no nome (`[ORDER_SEND_FAILED]`); resposta HTTP 5xx |
| nginx | `[error]`/`[crit]`/`[alert]`/`[emerg]` no `error.log`; 5xx no `access.log` |
| Cron do site | `PAREI`/`FALHOU` em `/root/deploy-elcapo/logs/auto-site.log` |
| Saúde | container parado (avisa ao parar e ao voltar) ou reiniciado; `/health` do gateway sem 200; disco ≥ 90% |
| O próprio vigia | se ele quebrar, manda "o vigia de erros falhou" |

O staging (`*-staging`, `elcapo2`) fica fora; `--staging` inclui.

## O padrão para código novo

Para um erro aparecer no e-mail **não precisa configurar nada**: basta
`logger.error(...)`, uma exceção com `exc_info=True`, ou uma marca terminada em
`_FAILED` (`logger.warning("[PAGAMENTO_FAILED] ...")`). O gateway roda em nível
WARNING — `logger.info` nunca chega ao log dele (ver memória "logs INFO invisíveis").
Nunca engolir exceção num `except` mudo: isso é erro que o vigia não vê.

## Como avisa

- Erros são agrupados por **assinatura**: tipo + onde, sem ids, números e horários.
  Cem `ORDER_SEND_FAILED` de clientes diferentes são um item só, com a contagem.
- Assinatura nova → e-mail na hora (um e-mail por rodada com todos os novos).
- A mesma assinatura só volta a gerar e-mail depois de **24h**.
- **Resumo diário às 08h** (Brasília) com a contagem de cada erro das últimas 24h,
  inclusive os já avisados.
- Falha no envio não marca como avisado: a rodada seguinte tenta de novo.

## Ruído conhecido

Para ignorar um erro comprovadamente inofensivo, acrescente `(regex, motivo)` em
`IGNORAR` no script. Começa vazio de propósito: o dono pediu todo erro.

## Operação

- Log: `/root/deploy-elcapo/logs/vigia-erros.log`
- Estado (cursores, assinaturas, avisos): `/root/deploy-elcapo/logs/vigia-erros-estado.json`
- Simular sem enviar: `python3 scripts/vigia_erros.py --para x --simular`
- Testes: `python3 -m unittest tests.test_vigia_erros`

A auditoria do placar (`auditoria_placar.py`, a cada 15 min) continua separada:
ela compara dados (placar × Histórico), coisa que log nenhum mostra.
