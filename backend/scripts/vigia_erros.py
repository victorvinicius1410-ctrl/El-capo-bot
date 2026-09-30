#!/usr/bin/env python3
"""Vigia de erros do El Capo: todo erro do sistema vira e-mail para o dono.

Roda a cada 5 min (``/etc/cron.d/elcapo-vigia-erros``). Criado em 29/09/2026
depois que um placar errado só apareceu porque o cliente reclamou — a regra
agora é: erro no servidor chega por e-mail antes do cliente perceber.

**O padrão (o que conta como erro):**

1. ``Traceback`` em qualquer container — exceção com pilha.
2. Linha de log com nível ``ERROR``/``CRITICAL``.
3. Marca com ``FAILED``/``ERROR``/``EXCEPTION`` no nome, ex.:
   ``[ORDER_SEND_FAILED]``. O gateway roda em nível WARNING, então código novo
   que quer ser visto aqui loga ``logger.warning("[ALGO_FAILED] ...")`` ou
   ``logger.error(...)`` — sem mais nada a configurar.
4. Resposta HTTP 5xx (uvicorn e nginx) e ``[error]`` do nginx.
5. ``PAREI``/``FALHOU`` no log do cron que publica o site.
6. Saúde: container parado ou reiniciado, API do gateway sem responder,
   disco acima de 90%.

**Como avisa:** os erros são agrupados por *assinatura* (tipo + onde, sem ids,
números e horários). Assinatura nova → e-mail na hora; a mesma assinatura só
volta a gerar e-mail depois de 24h. Container parado avisa uma vez e avisa de
novo quando volta. Todo dia às 08h (Brasília) chega o resumo das últimas 24h
com a contagem de cada erro, inclusive os já avisados.

Uso::

    python scripts/vigia_erros.py --para dono@exemplo.com
    python scripts/vigia_erros.py --para dono@exemplo.com --simular   # não envia nem grava

Estado em ``/root/deploy-elcapo/logs/vigia-erros-estado.json``.
Ignorar um erro conhecido: acrescente uma regex em ``IGNORAR`` (com o porquê).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import alerta_auditoria_placar  # noqa: E402  (reusa o envio SMTP)

CONTAINERS = ("backend-gateway", "robot-runtime", "bullex-service", "webhook-worker", "webhook-redis")
CONTAINERS_STAGING = (
    "backend-gateway-staging",
    "robot-runtime-staging",
    "bullex-service-staging",
    "webhook-worker-staging",
    "webhook-redis-staging",
)
NGINX_ERRO = Path("/var/log/nginx/error.log")
NGINX_ACESSO = Path("/var/log/nginx/access.log")
LOG_AUTO_SITE = Path("/root/deploy-elcapo/logs/auto-site.log")
SAUDE_GATEWAY = "http://127.0.0.1:8080/health"
ESTADO_PADRAO = "/root/deploy-elcapo/logs/vigia-erros-estado.json"
REAVISO_HORAS = 24
HORA_RESUMO_UTC = 11  # 08h em Brasília
MAX_ITENS_EMAIL = 30
MEMORIA_LIVRE_MINIMA_PCT = 20
FUSO_BRASILIA = datetime.timezone(datetime.timedelta(hours=-3))

# Regex de linhas que NÃO são erro, cada uma com o motivo. Vazio de propósito:
# o dono pediu todo erro. Só entra aqui o que for comprovadamente ruído.
IGNORAR: list[tuple[str, str]] = []

RE_NIVEL = re.compile(r"(^|[\s:])(ERROR|CRITICAL|FATAL)([\s:]|$)|level=(error|fatal|critical)", re.I)
RE_MARCA = re.compile(r"\[([A-Z0-9_]*(?:FAILED|ERROR|EXCEPTION|CRASH)[A-Z0-9_]*)\]")
RE_HTTP_5XX = re.compile(r'"(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD) (\S+) HTTP/[\d.]+" (5\d\d)')
RE_FIM_EXC = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Timeout|Cancelled\w*))(?::\s?(.*))?$")
RE_FRAME = re.compile(r'File "(/app/[^"]+|/w/[^"]+)", line \d+, in (\S+)')


def normalizar(texto: str) -> str:
    """Tira o que muda a cada ocorrência (ids, números, horários) do texto."""
    t = re.sub(r"^\S*\d{4}-\d{2}-\d{2}[T ][\d:.,]+\S*\s*", "", texto)
    t = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<id>", t, flags=re.I)
    t = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "<email>", t)
    t = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ]?[\d:.]*(\+\d{2}:?\d{2}|Z)?", "<data>", t)
    t = re.sub(r"\b[0-9a-f]{16,}\b", "<hex>", t, flags=re.I)
    t = re.sub(r"\d+(\.\d+)?", "#", t)
    t = re.sub(r"(ticket|token|ssid|key)=\S+", r"\1=<x>", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:180]


def _ignorado(texto: str) -> bool:
    return any(re.search(padrao, texto) for padrao, _motivo in IGNORAR)


def _novo_evento(eventos: dict, assinatura: str, origem: str, tipo: str, amostra: str) -> None:
    chave = hashlib.sha1(assinatura.encode()).hexdigest()[:16]
    item = eventos.setdefault(
        chave,
        {"chave": chave, "assinatura": assinatura, "origem": origem, "tipo": tipo,
         "amostra": amostra[:3000], "vezes": 0},
    )
    item["vezes"] += 1


def analisar_linhas(origem: str, linhas: list[str], eventos: dict) -> None:
    """Extrai erros de linhas de log (já sem o carimbo do docker)."""
    i = 0
    contexto = ""
    while i < len(linhas):
        linha = linhas[i]
        if _ignorado(linha):
            i += 1
            continue
        if linha.startswith("Traceback (most recent call last)"):
            bloco = [linha]
            fim = ""
            frame = ""
            j = i + 1
            while j < len(linhas):
                prox = linhas[j]
                exc = RE_FIM_EXC.match(prox)
                if prox.startswith((" ", "\t")) or prox.strip() == "" or prox.startswith(
                    ("Traceback", "During handling", "The above exception")
                ):
                    achou = RE_FRAME.search(prox)
                    if achou:
                        frame = f"{Path(achou.group(1)).name}:{achou.group(2)}"
                    bloco.append(prox)
                    j += 1
                    continue
                if exc:
                    fim = exc.group(1)
                    bloco.append(prox)
                    j += 1
                    continue
                break
            marca = RE_MARCA.search(contexto) or re.search(r"\[([A-Z0-9_]{4,})\]", contexto)
            assinatura = f"{origem} | exceção {fim or '?'} em {frame or '?'}"
            if marca:
                assinatura += f" | {marca.group(1)}"
            _novo_evento(eventos, assinatura, origem, "exceção", "\n".join(([contexto] if contexto else []) + bloco[-40:]))
            i = j
            continue
        http = RE_HTTP_5XX.search(linha)
        if http:
            caminho = normalizar(http.group(2).split("?")[0])
            _novo_evento(eventos, f"{origem} | HTTP {http.group(3)} {http.group(1)} {caminho}", origem, "http 5xx", linha)
        elif RE_NIVEL.search(linha) and "INFO" not in linha[:40]:
            _novo_evento(eventos, f"{origem} | {normalizar(linha)}", origem, "erro no log", linha)
        else:
            marca = RE_MARCA.search(linha)
            if marca:
                resto = normalizar(linha[marca.end():])[:90]
                _novo_evento(eventos, f"{origem} | [{marca.group(1)}] {resto}", origem, "falha registrada", linha)
        if linha.strip():
            contexto = linha
        i += 1


def ler_docker(container: str, desde: str | None) -> tuple[list[str], str | None]:
    """Linhas novas de um container desde o carimbo ``desde`` (RFC3339)."""
    # Carimbo no formato do docker (9 casas), para comparar como texto.
    agora = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"
    cmd = ["docker", "logs", "-t", "--since", desde or "6m", container]
    # 30 s: com o host sem memória o `docker logs` trava; esperar 2 min só
    # atrasava o aviso (30/09 05:25). Estourar vira evento "não consegui ler".
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=30)
    saida = (proc.stdout or "") + (proc.stderr or "")
    linhas: list[str] = []
    # Sem linha nova, o cursor avança mesmo assim: senão a rodada seguinte
    # relê a janela padrão e conta duas vezes o que cair na sobreposição.
    ultimo = max(desde or "", agora) if proc.returncode == 0 else desde
    for bruta in saida.splitlines():
        carimbo, _, texto = bruta.partition(" ")
        if not re.match(r"\d{4}-\d{2}-\d{2}T", carimbo):
            continue
        if desde and carimbo <= desde:
            continue
        linhas.append(texto)
        if ultimo is None or carimbo > ultimo:
            ultimo = carimbo
    return linhas, ultimo


def ler_arquivo(caminho: Path, cursor: dict | None) -> tuple[list[str], dict]:
    """Linhas novas de um arquivo de log, sobrevivendo à rotação."""
    try:
        info = caminho.stat()
    except OSError:
        return [], cursor or {}
    inode, tamanho = info.st_ino, info.st_size
    if not cursor or cursor.get("inode") != inode or cursor.get("pos", 0) > tamanho:
        # Primeira vez ou arquivo rotacionado: começa do fim (sem backlog).
        pos = tamanho if not cursor else 0
    else:
        pos = cursor["pos"]
    with caminho.open("rb") as arq:
        arq.seek(pos)
        dados = arq.read(5_000_000)
    return dados.decode("utf-8", "replace").splitlines(), {"inode": inode, "pos": pos + len(dados)}


def checar_saude(estado: dict, eventos: dict, containers: tuple[str, ...]) -> list[str]:
    """Container parado/reiniciado, API fora e disco cheio."""
    avisos: list[str] = []
    incidentes = estado.setdefault("incidentes", {})
    inicios = estado.setdefault("inicios", {})
    for nome in containers:
        proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}} {{.State.StartedAt}} {{.State.ExitCode}} {{.State.OOMKilled}}", nome],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            continue
        rodando, inicio, codigo, oom = proc.stdout.split()
        chave = f"parado:{nome}"
        if rodando != "true":
            if chave not in incidentes:
                incidentes[chave] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                _novo_evento(eventos, f"{nome} | CONTAINER PARADO", nome, "saúde",
                             f"{nome} parado (exit={codigo}, sem memória={oom}).")
        elif chave in incidentes:
            avisos.append(f"{nome} voltou a rodar (parado desde {incidentes.pop(chave)[:19]}Z).")
        anterior = inicios.get(nome)
        if rodando == "true" and anterior and anterior != inicio:
            _novo_evento(eventos, f"{nome} | CONTAINER REINICIOU {inicio[:16]}", nome, "saúde",
                         f"{nome} reiniciou às {inicio[:19]}Z (deploy ou queda — confira).")
        inicios[nome] = inicio
    if "backend-gateway" in containers:
        try:
            with urllib.request.urlopen(SAUDE_GATEWAY, timeout=10) as resp:
                ok = resp.status == 200
        except Exception:  # noqa: BLE001
            ok = False
        chave = "api:gateway"
        if not ok and chave not in incidentes:
            incidentes[chave] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            _novo_evento(eventos, "backend-gateway | API SEM RESPONDER", "backend-gateway", "saúde",
                         f"{SAUDE_GATEWAY} não respondeu 200.")
        elif ok and chave in incidentes:
            avisos.append(f"API do gateway voltou (fora desde {incidentes.pop(chave)[:19]}Z).")
    uso = shutil.disk_usage("/")
    pct = uso.used * 100 // uso.total
    if pct >= 90:
        _novo_evento(eventos, "servidor | DISCO ACIMA DE 90%", "servidor", "saúde", f"Disco em {pct}%.")
    avisos += checar_memoria(estado, eventos)
    checar_oom(estado, eventos)
    return avisos


def _meminfo() -> dict[str, int]:
    dados = {}
    for linha in Path("/proc/meminfo").read_text().splitlines():
        nome, _, valor = linha.partition(":")
        dados[nome] = int(valor.split()[0])  # kB
    return dados


def checar_memoria(estado: dict, eventos: dict) -> list[str]:
    """Memória livre abaixo de 20% avisa uma vez; volta ao normal avisa também.

    Em 30/09 o host chegou a 97% de uso e o kernel matou a corretora (e já tinha
    matado o robô em 27/09): um vazamento crescendo ~290 MB/h no robot-runtime.
    Com o aviso aqui, dá tempo de reiniciar com calma em vez de levar o OOM.
    """
    incidentes = estado.setdefault("incidentes", {})
    mem = _meminfo()
    livre = mem.get("MemAvailable", 0) * 100 // max(mem.get("MemTotal", 1), 1)
    chave = "memoria:host"
    if livre < MEMORIA_LIVRE_MINIMA_PCT:
        if chave not in incidentes:
            incidentes[chave] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            maiores = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}"],
                capture_output=True, text=True, timeout=30,
            ).stdout
            _novo_evento(eventos, "servidor | MEMÓRIA ACABANDO", "servidor", "saúde",
                         f"Só {livre}% de memória livre (limite {MEMORIA_LIVRE_MINIMA_PCT}%).\n{maiores}")
        return []
    if chave in incidentes:
        return [f"Memória voltou a {livre}% livre (baixa desde {incidentes.pop(chave)[:19]}Z)."]
    return []


def checar_oom(estado: dict, eventos: dict) -> None:
    """Toda morte por falta de memória do kernel vira erro.

    O ``OOMKilled`` do Docker zera quando o container reinicia, então olhar o
    container não basta: lê o log do kernel desde a última rodada.
    """
    cursor = estado.get("cursores", {}).get("kernel")
    cmd = ["journalctl", "-k", "-o", "short-iso", "--show-cursor", "--no-pager"]
    cmd += ["--after-cursor", cursor] if cursor else ["--since", "-6min"]
    try:
        saida = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except Exception:  # noqa: BLE001
        return
    for linha in saida.splitlines():
        if linha.startswith("-- cursor:"):
            estado.setdefault("cursores", {})["kernel"] = linha.split(":", 1)[1].strip()
        elif "Killed process" in linha:  # a linha que diz quem morreu (as outras são contexto)
            processo = re.search(r"Killed process \d+ \((\S+)\)", linha)
            _novo_evento(eventos, f"servidor | KERNEL MATOU PROCESSO POR FALTA DE MEMÓRIA "
                         f"({processo.group(1) if processo else '?'})", "servidor", "saúde", linha)


def coletar(estado: dict, incluir_staging: bool) -> tuple[dict, list[str]]:
    eventos: dict = {}
    cursores = estado.setdefault("cursores", {})
    containers = CONTAINERS + (CONTAINERS_STAGING if incluir_staging else ())
    for nome in containers:
        try:
            linhas, ultimo = ler_docker(nome, cursores.get(nome))
        except Exception as erro:  # noqa: BLE001
            _novo_evento(eventos, f"vigia | não consegui ler {nome}", "vigia", "vigia", repr(erro))
            continue
        analisar_linhas(nome, linhas, eventos)
        if ultimo:
            cursores[nome] = ultimo
    linhas, cursores["nginx_erro"] = ler_arquivo(NGINX_ERRO, cursores.get("nginx_erro"))
    for linha in linhas:
        if re.search(r"\[(error|crit|alert|emerg)\]", linha) and not _ignorado(linha):
            servidor = re.search(r"server: (\S+?),", linha)
            if not incluir_staging and servidor and "elcapo2" in servidor.group(1):
                continue
            motivo = re.sub(r"^.*?\[(error|crit|alert|emerg)\] [\d#]+: \*?\d* ?", "", linha).split(", client:")[0]
            _novo_evento(eventos, f"nginx | {normalizar(motivo)} | {servidor.group(1) if servidor else '?'}",
                         "nginx", "nginx", linha)
    linhas, cursores["nginx_acesso"] = ler_arquivo(NGINX_ACESSO, cursores.get("nginx_acesso"))
    for linha in linhas:
        http = RE_HTTP_5XX.search(linha)
        if http and not _ignorado(linha):
            _novo_evento(eventos, f"nginx | HTTP {http.group(3)} {http.group(1)} {normalizar(http.group(2).split('?')[0])}",
                         "nginx", "http 5xx", linha)
    linhas, cursores["auto_site"] = ler_arquivo(LOG_AUTO_SITE, cursores.get("auto_site"))
    for linha in linhas:
        if re.search(r"PAREI|FALHOU|falhou", linha) and not _ignorado(linha):
            _novo_evento(eventos, f"cron do site | {normalizar(linha)}", "cron do site", "cron", linha)
    avisos = checar_saude(estado, eventos, containers)
    return eventos, avisos


def registrar(estado: dict, eventos: dict, agora: datetime.datetime) -> list[dict]:
    """Soma contagens por hora e devolve os eventos que merecem e-mail agora."""
    assinaturas = estado.setdefault("assinaturas", {})
    hora = agora.strftime("%Y-%m-%dT%H")
    corte = (agora - datetime.timedelta(hours=48)).strftime("%Y-%m-%dT%H")
    novos = []
    for chave, item in eventos.items():
        reg = assinaturas.setdefault(
            chave, {"assinatura": item["assinatura"], "origem": item["origem"], "tipo": item["tipo"],
                    "primeira": agora.isoformat(), "avisado": None, "horas": {}},
        )
        reg["ultima"] = agora.isoformat()
        reg["amostra"] = item["amostra"]
        reg["horas"][hora] = reg["horas"].get(hora, 0) + item["vezes"]
        avisado = reg.get("avisado")
        if not avisado or agora - datetime.datetime.fromisoformat(avisado) >= datetime.timedelta(hours=REAVISO_HORAS):
            novos.append(item)
    for reg in assinaturas.values():
        reg["horas"] = {h: n for h, n in reg["horas"].items() if h >= corte}
    for chave in [c for c, r in assinaturas.items() if not r["horas"]]:
        del assinaturas[chave]
    return novos


def email_novos(novos: list[dict], avisos: list[str], agora: datetime.datetime) -> tuple[str, str]:
    brt = agora.astimezone(FUSO_BRASILIA).strftime("%d/%m %H:%M")
    origens = sorted({n["origem"] for n in novos})
    assunto = f"[El Capo] {len(novos)} erro(s) novo(s) — {', '.join(origens)[:80]}" if novos else "[El Capo] serviço normalizado"
    partes = [f"Vigia de erros — {brt} (Brasília)", ""]
    if avisos:
        partes += ["Voltou ao normal:", *[f"  - {a}" for a in avisos], ""]
    for n, item in enumerate(novos[:MAX_ITENS_EMAIL], 1):
        partes += [
            f"{n}. [{item['tipo']}] {item['assinatura']}",
            f"   {item['vezes']} vez(es) nesta rodada. Exemplo:",
            *["   | " + l for l in item["amostra"].splitlines()[-25:]],
            "",
        ]
    if len(novos) > MAX_ITENS_EMAIL:
        partes.append(f"... e mais {len(novos) - MAX_ITENS_EMAIL} tipo(s) de erro (ver o resumo diário).")
    partes += ["", "Cada tipo de erro só volta a gerar e-mail depois de 24h; o resumo diário chega às 08h.",
               "Padrão e como ignorar ruído: backend/scripts/vigia_erros.py (docstring) e docs/VIGIA_DE_ERROS.md."]
    return assunto, "\n".join(partes)


def email_resumo(estado: dict, agora: datetime.datetime) -> tuple[str, str] | None:
    corte = (agora - datetime.timedelta(hours=24)).strftime("%Y-%m-%dT%H")
    linhas = []
    total = 0
    for reg in estado.get("assinaturas", {}).values():
        vezes = sum(n for h, n in reg["horas"].items() if h >= corte)
        if vezes:
            total += vezes
            linhas.append((vezes, reg))
    linhas.sort(key=lambda x: -x[0])
    brt = agora.astimezone(FUSO_BRASILIA).strftime("%d/%m")
    corpo = [f"Resumo de erros das últimas 24h — {brt}", ""]
    if not linhas:
        corpo.append("Nenhum erro registrado. 👍")
    for vezes, reg in linhas[:80]:
        corpo.append(f"{vezes:>6}x  [{reg['tipo']}] {reg['assinatura']}")
    return f"[El Capo] resumo diário: {total} erro(s) em {len(linhas)} tipo(s)", "\n".join(corpo)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--para", required=True)
    parser.add_argument("--estado", default=ESTADO_PADRAO)
    parser.add_argument("--simular", action="store_true", help="mostra o e-mail e não envia nem grava")
    parser.add_argument("--staging", action="store_true", help="vigia também os containers -staging")
    args = parser.parse_args()

    caminho = Path(args.estado)
    try:
        estado = json.loads(caminho.read_text())
    except (OSError, ValueError):
        estado = {}
    agora = datetime.datetime.now(datetime.timezone.utc)
    try:
        eventos, avisos = coletar(estado, args.staging)
        novos = registrar(estado, eventos, agora)
    except Exception as erro:  # noqa: BLE001
        # O vigia quebrado é erro também: avisa em vez de ficar mudo.
        assunto, corpo = "[El Capo] o vigia de erros falhou", f"{erro.__class__.__name__}: {erro}"
        if not args.simular:
            alerta_auditoria_placar._enviar(args.para, assunto, corpo)
        print(f"[VIGIA] falhou: {erro!r}")
        return 3

    enviar: list[tuple[str, str]] = []
    if novos or avisos:
        enviar.append(email_novos(novos, avisos, agora))
    hoje = agora.date().isoformat()
    if agora.hour == HORA_RESUMO_UTC and estado.get("resumo_enviado") != hoje:
        resumo = email_resumo(estado, agora)
        if resumo:
            enviar.append(resumo)

    print(f"[VIGIA] {agora.isoformat()[:19]}Z eventos={len(eventos)} novos={len(novos)} avisos={len(avisos)}")
    for assunto, corpo in enviar:
        if args.simular:
            print(f"--- {assunto}\n{corpo}\n")
            continue
        try:
            alerta_auditoria_placar._enviar(args.para, assunto, corpo)
            print(f"[VIGIA] e-mail enviado: {assunto}")
            if assunto.startswith("[El Capo] resumo diário"):
                estado["resumo_enviado"] = hoje
            else:
                for item in novos:
                    estado["assinaturas"][item["chave"]]["avisado"] = agora.isoformat()
        except Exception as erro:  # noqa: BLE001
            # Não marca como avisado: a próxima rodada tenta de novo.
            print(f"[VIGIA] falha no e-mail: {erro!r}")
    if not args.simular:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(json.dumps(estado, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
