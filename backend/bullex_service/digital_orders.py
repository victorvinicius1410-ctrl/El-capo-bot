"""Compra e leitura de resultado no canal DIGITAL da corretora.

## Por que este módulo existe

Em 2026-09-04 os logs de produção mostraram ativos de **mercado aberto**
(GBPUSD, EURUSD, AUDUSD...) com payout 82–88, aprovados na varredura e
escolhidos como melhor candidato do ciclo — e nenhuma ordem saindo. A causa
são dois canais diferentes:

- o payout que o robô lê vem de ``read_digital_payout`` → canal **digital**;
- a ordem que o robô manda vai por ``client.buy()`` → canal **turbo/binary**.

Nos pares abertos o digital abre e o turbo/binary fica fechado, então a compra
morre em *"asset is not available at the moment"*. Em 11.628 operações reais,
**zero** caíram em ativo de mercado aberto — não porque a corretora não venda,
mas porque não havia caminho de compra no canal onde ele está aberto.

## Por que não usar a função da biblioteca

``stable_api.buy_digital_spot_v2`` termina com::

    while self.api.digital_option_placed_id.get(request_id) is None:
        pass

Espera ocupada, sem timeout e sem sleep. Se a corretora não responder, a thread
gira a 100% de CPU para sempre. É a mesma classe de defeito que travou o
``robot-runtime`` em 08/08. Aqui o ``place`` é o mesmo, mas a espera tem
timeout e cede o processador.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("bullex-service")

# Teto de espera pelo aceite da corretora. Acima disso a ordem é dada como não
# colocada — melhor devolver erro do que segurar a thread.
DIGITAL_PLACE_TIMEOUT_SECONDS = 12.0
# Intervalo de sondagem. Nunca zero: é o que diferencia isto da espera ocupada.
DIGITAL_POLL_INTERVAL_SECONDS = 0.05
# Antecedência mínima até a expiração, exigida pela grade do canal digital.
DIGITAL_MIN_SECONDS_TO_EXPIRY = 30


class DigitalOrderError(RuntimeError):
    """Falha ao colocar ou ler uma ordem digital."""


def build_instrument_id(active_id: int | str, duration_minutes: int, action: str, expiry: datetime) -> str:
    """Monta o identificador do instrumento digital.

    Formato da corretora: ``do<ativo>A<AAAAMMDD>D<HHMM>00T<duração>M<C|P>SPT``.

    Args:
        active_id: Id numérico do ativo no mapa da corretora.
        duration_minutes: Duração da opção em minutos (1, 5, 15).
        action: ``call`` ou ``put``.
        expiry: Instante de expiração, em UTC.

    Returns:
        O identificador pronto para ``place_digital_option_v2``.

    Raises:
        ValueError: Se a direção não for call/put.
    """
    lado = str(action or "").strip().lower()
    if lado not in {"call", "put"}:
        raise ValueError(f"DIGITAL_ACTION_INVALID:{action}")
    letra = "C" if lado == "call" else "P"
    marcado = expiry.astimezone(timezone.utc).strftime("%Y%m%d%H%M")
    return (
        f"do{active_id}A{marcado[:8]}D{marcado[8:]}00T{int(duration_minutes)}M{letra}SPT"
    )


def resolve_expiry(server_timestamp: float, duration_minutes: int) -> datetime:
    """Encontra a próxima expiração válida da grade digital.

    A grade do canal digital fecha em minutos múltiplos da duração e exige uma
    antecedência mínima — pedir uma expiração perto demais faz a corretora
    recusar a ordem sem explicar.

    Args:
        server_timestamp: Relógio da corretora, em Unix.
        duration_minutes: Duração desejada, em minutos.

    Returns:
        O instante de expiração em UTC.

    Raises:
        ValueError: Se a duração não for positiva.
    """
    if duration_minutes <= 0:
        raise ValueError(f"DIGITAL_DURATION_INVALID:{duration_minutes}")
    agora = datetime.fromtimestamp(float(server_timestamp), tz=timezone.utc)
    candidato = (agora + timedelta(minutes=1)).replace(second=0, microsecond=0)
    for _ in range(0, 240):
        alinhado = candidato.minute % duration_minutes == 0
        antecedencia = (candidato - agora).total_seconds()
        # `>` e não `>=`: `expiration.get_expiration_time` usa
        # `(...) - timestamp > 30`. Com exatamente 30s de antecedência a
        # biblioteca pula para o minuto seguinte, e um instrument_id montado
        # com a outra escolha aponta para uma expiração fora da grade.
        if alinhado and antecedencia > DIGITAL_MIN_SECONDS_TO_EXPIRY:
            return candidato
        candidato = candidato + timedelta(minutes=1)
    raise ValueError("DIGITAL_EXPIRY_NOT_FOUND")


def place_digital_order(
    client: Any,
    *,
    active: str,
    active_id: int | str,
    amount: float,
    action: str,
    duration_minutes: int,
    timeout_seconds: float = DIGITAL_PLACE_TIMEOUT_SECONDS,
) -> tuple[bool, Any]:
    """Coloca uma ordem no canal digital, com espera limitada.

    Args:
        client: Cliente ``Bullex`` conectado.
        active: Nome do ativo (para log).
        active_id: Id numérico do ativo.
        amount: Valor da entrada.
        action: ``call`` ou ``put``.
        duration_minutes: Duração da opção em minutos.
        timeout_seconds: Teto de espera pelo aceite.

    Returns:
        Par ``(ok, order_id_ou_motivo)``.

    Raises:
        DigitalOrderError: Se o cliente não expõe o canal digital.
        ValueError: Se direção ou duração forem inválidas.
    """
    api = getattr(client, "api", None)
    colocar = getattr(api, "place_digital_option_v2", None)
    if colocar is None:
        raise DigitalOrderError("DIGITAL_CHANNEL_UNAVAILABLE")

    expiry = resolve_expiry(float(api.timesync.server_timestamp), duration_minutes)
    instrument_id = build_instrument_id(active_id, duration_minutes, action, expiry)
    logger.info(
        "[DIGITAL_PLACE] active=%s instrument=%s amount=%s expiry=%s",
        active,
        instrument_id,
        amount,
        expiry.isoformat(),
    )
    request_id = colocar(instrument_id, str(active_id), amount)

    limite = time.monotonic() + float(timeout_seconds)
    while time.monotonic() < limite:
        # Relido a cada volta de propósito: a biblioteca REATRIBUI este
        # atributo (`api.py` zera o dict ao reconectar, `stable_api.buy_digital`
        # troca por None). Guardar a referência antes do laço faria a espera
        # consultar um dicionário morto e reportar timeout numa ordem que a
        # corretora aceitou — posição real aberta e sem registro.
        colocados = getattr(api, "digital_option_placed_id", None)
        order_id = colocados.get(request_id) if hasattr(colocados, "get") else None
        if order_id is not None:
            if isinstance(order_id, int):
                logger.info("[DIGITAL_PLACE_OK] active=%s order_id=%s", active, order_id)
                return True, order_id
            logger.warning("[DIGITAL_PLACE_REJECTED] active=%s motivo=%s", active, order_id)
            return False, order_id
        time.sleep(DIGITAL_POLL_INTERVAL_SECONDS)

    logger.warning("[DIGITAL_PLACE_TIMEOUT] active=%s timeout=%ss", active, timeout_seconds)
    return False, "DIGITAL_PLACE_TIMEOUT"


def read_digital_result(client: Any, order_id: Any) -> dict[str, Any]:
    """Lê o resultado de uma ordem digital sem bloquear.

    ``stable_api.check_win_digital_v2`` faz espera ocupada até a posição
    fechar; aqui a leitura é um retrato do estado atual, e quem chama decide
    quando perguntar de novo.

    Args:
        client: Cliente ``Bullex`` conectado.
        order_id: Id devolvido por ``place_digital_order``.

    Returns:
        ``{"order_id", "result", "profit"}`` — ``result`` é ``WIN``, ``LOSS``
        ou ``PENDING_RESULT``.
    """
    pendente = {"order_id": order_id, "result": "PENDING_RESULT", "profit": None}
    leitor = getattr(client, "get_async_order", None)
    if leitor is None:
        return pendente
    try:
        dados = leitor(order_id) or {}
    except Exception:  # noqa: BLE001 - leitura oportunista, nunca derruba o ciclo
        logger.exception("falha ao ler ordem digital %s", order_id)
        return pendente

    mudanca = dados.get("position-changed") or {}
    posicao = mudanca.get("msg") if isinstance(mudanca, dict) else None
    if not isinstance(posicao, dict) or posicao.get("status") != "closed":
        return pendente

    motivo = posicao.get("close_reason")
    if motivo == "expired":
        lucro = float(posicao.get("close_profit") or 0.0) - float(posicao.get("invest") or 0.0)
    else:
        lucro = float(posicao.get("pnl_realized") or 0.0)
    # Mesmo vocabulário que `/orders/{id}/result` devolve para turbo/binary
    # (`win`/`loose`/`equal`), que é o que `trade_result_monitor` já sabe
    # normalizar. Devolução integral fecha com lucro zero e é EMPATE — marcar
    # como derrota distorceria a taxa de acerto e dispararia o cooldown
    # pós-LOSS sem que tenha havido perda.
    if lucro > 0:
        resultado = "win"
    elif lucro < 0:
        resultado = "loose"
    else:
        resultado = "equal"
    return {"order_id": order_id, "result": resultado, "profit": lucro}
