# GitHub — repositório, SSH e sync com a VPS

Documentação do repositório remoto do El Capo AutoBot e de como esta VPS
autentica no GitHub para `git push` / `git pull`.

## Repositório

| Item | Valor |
|------|--------|
| Remote SSH | `git@github.com:victorvinicius1410-ctrl/El-capo-bot.git` |
| Branch padrão | `main` |
| Diretório na VPS (fonte de verdade) | `/opt/elcapo` |
| Espelhos de trabalho | `/root/Backend`, `/root/Frontend`, `/root/docs` |

O que **não** vai para o GitHub (ver `.gitignore` na raiz):

- `.env` / `.env.*` (só `.env.example`)
- `node_modules/`, `.venv/`, `dist/`
- bancos locais em `backend/data/` (`*.db`)
- caches (`.pytest_cache`, `__pycache__`, `.tanstack`, `.vercel`)

## Chave SSH desta VPS (já gerada)

Arquivos:

```text
/root/.ssh/id_ed25519_github      # privada — NUNCA compartilhar / NUNCA commitar
/root/.ssh/id_ed25519_github.pub  # pública — cadastrar no GitHub
/root/.ssh/config                 # Host github.com → IdentityFile acima
```

Chave pública atual (cole no GitHub):

```text
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMrV/IdPn1ryuVDqxaBoBKryNky4tWytcXPt9IxtjFtN elcapo-vps-deploy
```

### Como cadastrar a chave no GitHub (obrigatório para push/pull)

Escolha **uma** das opções:

#### Opção A — Deploy Key no repositório (recomendado para esta VPS)

1. Abra: https://github.com/victorvinicius1410-ctrl/El-capo-bot/settings/keys  
2. **Add deploy key**
3. Title: `elcapo-vps`
4. Key: cole a linha `ssh-ed25519 AAAAC3...` acima
5. Marque **Allow write access** (necessário para `git push` da VPS)
6. Salve

#### Opção B — SSH Key da conta GitHub

1. Abra: https://github.com/settings/keys  
2. **New SSH key**
3. Title: `elcapo-vps`
4. Cole a mesma chave pública
5. Salve

### Testar autenticação

```bash
ssh -T git@github.com
# esperado: Hi victorvinicius1410-ctrl! You've successfully authenticated...
```

## Primeiro push (já preparado em `/opt/elcapo`)

Depois que a chave estiver cadastrada:

```bash
cd /opt/elcapo
git remote -v   # origin → git@github.com:victorvinicius1410-ctrl/El-capo-bot.git
git push -u origin main
```

Se o remote ainda não existir:

```bash
cd /opt/elcapo
git remote add origin git@github.com:victorvinicius1410-ctrl/El-capo-bot.git
git branch -M main
git push -u origin main
```

## Fluxo diário: alterar na VPS → GitHub

```bash
cd /opt/elcapo
# editar código...
git status
git add -A
git commit -m "feat(escopo): descricao curta"
git push origin main
```

Credenciais: **não** usa usuário/senha. O Git usa a chave SSH de
`/root/.ssh/id_ed25519_github` automaticamente via `~/.ssh/config`.

## Fluxo: alterar no PC/notebook → atualizar esta VPS

No seu computador (com sua própria chave SSH ou HTTPS + PAT):

```bash
git clone git@github.com:victorvinicius1410-ctrl/El-capo-bot.git
# ... commits e push ...
git push origin main
```

Nesta VPS, para **aplicar** o que chegou no GitHub:

```bash
cd /opt/elcapo
git pull origin main
/opt/elcapo/scripts/deploy-backend.sh          # se mudou backend
/opt/elcapo/scripts/publish-frontend.sh        # se mudou frontend
```

Ou o atalho:

```bash
/opt/elcapo/scripts/sync-from-github.sh
```

Isso **não** é automático por padrão: um `git push` no seu PC só atualiza o
GitHub. A VPS só muda depois do `git pull` (+ deploy).

## Credenciais no seu PC (para você dar push de outro lugar)

### SSH (recomendado)

1. Gere chave no PC: `ssh-keygen -t ed25519 -C "seu-email"`
2. Cadastre `~/.ssh/id_ed25519.pub` em https://github.com/settings/keys
3. Clone/push com a URL SSH do repositório

### HTTPS + Personal Access Token (PAT)

1. GitHub → Settings → Developer settings → Personal access tokens  
   (classic: escopos `repo`; fine-grained: Contents Read/Write no repo)
2. Ao dar `git push` via HTTPS, use o **token** no lugar da senha
3. No Linux/macOS, o credential helper pode guardar o token:

```bash
git config --global credential.helper store
# na próxima vez que digitar o token, fica salvo em ~/.git-credentials
```

**Atenção:** token no `~/.git-credentials` é texto puro — prefira SSH nesta VPS.

## O que NUNCA colocar no Git

- `SUPABASE_SERVICE_ROLE_KEY`, `PANEL_API_KEY`, senhas Bullex
- `BULLEX_SESSION_ENCRYPTION_KEY`, `ENCRYPTION_KEY`, secrets Cakto/Resend
- Qualquer `.env` de produção (`/opt/elcapo/backend/.env`)

Use só `.env.example` versionado; secrets ficam só na VPS (permissão `600`).

## Relação com deploy

Ver também [`DEPLOY_VPS.md`](./DEPLOY_VPS.md).

| Ação | Comando |
|------|---------|
| Atualizar código da VPS a partir do GitHub | `git -C /opt/elcapo pull` |
| Redeploy API | `/opt/elcapo/scripts/deploy-backend.sh` |
| Publicar painel | `/opt/elcapo/scripts/publish-frontend.sh` |
| Pull + deploys | `/opt/elcapo/scripts/sync-from-github.sh` |

## Troubleshooting

| Sintoma | Causa | Solução |
|---------|--------|---------|
| `Permission denied (publickey)` | Chave não cadastrada no GitHub | Opção A ou B acima |
| `Host key verification failed` | `known_hosts` sem github.com | `ssh-keyscan github.com >> ~/.ssh/known_hosts` |
| Push rejeita (non-fast-forward) | Histórico remoto diferente | `git pull --rebase origin main` e tentar de novo |
| Commit pedindo user.name | Git local sem identidade | usar `git -c user.name=... -c user.email=... commit` (não é obrigatório alterar o global) |
