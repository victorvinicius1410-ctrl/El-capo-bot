# Frontend El Capo AutoBot

Painel TanStack Start + Vite.

## Documentação operacional

- Autenticação (cookies / sessão): [`../docs/AUTENTICACAO.md`](../docs/AUTENTICACAO.md)
- Crash Chrome `removeChild` / `CancelledError` em `/admin`: [`../docs/CHROME_REMOVECHILD_LOGIN.md`](../docs/CHROME_REMOVECHILD_LOGIN.md)
- Execução local + login demo: [`../docs/LOCALHOST.md`](../docs/LOCALHOST.md)
- Tela de login (UI / UX): [`../docs/LOGIN.md`](../docs/LOGIN.md)
- Layout e menu do painel: [`../docs/LAYOUT.md`](../docs/LAYOUT.md)
- Dashboard: [`../docs/DASHBOARD.md`](../docs/DASHBOARD.md)
- Corretora / gráfico: [`../docs/CORRETORA.md`](../docs/CORRETORA.md)
- Configurações (conta + robô): [`../docs/CONFIGURACOES.md`](../docs/CONFIGURACOES.md)
- Feedbacks (envio + aprovação admin): [`../docs/FEEDBACKS.md`](../docs/FEEDBACKS.md)
- Operação / timeframe 1m·5m·15m: [`../docs/OPERACAO.md`](../docs/OPERACAO.md)
- Branding Bullex: [`../docs/BRANDING.md`](../docs/BRANDING.md)
- Financeiro (produto e ofertas): [`../docs/FINANCEIRO.md`](../docs/FINANCEIRO.md)
- Admin: [`../docs/ADMIN.md`](../docs/ADMIN.md)
- Performance navegação admin: [`../docs/ADMIN_NAV_PERFORMANCE.md`](../docs/ADMIN_NAV_PERFORMANCE.md)
- Webhooks e API: [`../docs/WEBHOOKS_API.md`](../docs/WEBHOOKS_API.md)
- Emails nativos: [`../docs/EMAILS.md`](../docs/EMAILS.md)
- Simulação marketing (Shift+O): [`../docs/MARKETING_SIMULATION.md`](../docs/MARKETING_SIMULATION.md)
- Rotas: [`src/routes/README.md`](src/routes/README.md)

## Dev

```powershell
npm install
npm run dev
```

Abra http://localhost:5173/login

Configure `.env` com `VITE_API_BASE_URL=http://127.0.0.1:8080` para o backend local.

## Verificação

```powershell
npm test
npm run lint
npx tsc --noEmit
npm run build
```

O lint ignora os artefatos gerados em `.vercel`, assim pode ser executado antes
ou depois do build sem percorrer a saída de deploy.


## Deploy nesta VPS

Build estático servido pelo Nginx em `/var/www/elcapobot`.

```bash
cd /opt/elcapo/frontend
# .env: VITE_API_BASE_URL=https://api.elcapobot.online
npm install
npm run build
rsync -a --delete dist/client/ /var/www/elcapobot/
```

DNS e SSL: [`/opt/elcapo/docs/DNS.md`](/opt/elcapo/docs/DNS.md) e [`DEPLOY_VPS.md`](/opt/elcapo/docs/DEPLOY_VPS.md).

## Deploy nesta VPS

Painel em **https://app.elcapobot.online** (não usar @/www — reservados para LP).

```bash
cd /opt/elcapo/frontend
# .env: VITE_API_BASE_URL=https://api.elcapobot.online
npm install
npm run build
rsync -a --delete dist/client/ /var/www/elcapobot/
```

DNS e SSL: `/opt/elcapo/docs/DNS.md` e `DEPLOY_VPS.md`.
