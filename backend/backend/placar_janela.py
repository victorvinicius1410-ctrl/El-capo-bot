"""Janela do placar e do stop: de um "Reiniciar placar" ao próximo.

Regra do dono (30/09/2026): o placar e o stop (operações e dinheiro) só zeram
quando o cliente clica em **Reiniciar placar**. Não existe mais virada à
meia-noite. Antes a janela era "hoje, depois do ``stop_reset_at``", e a virada
do dia foi a origem de uma família inteira de defeitos (placar de ontem
voltando, placar caindo sozinho no primeiro restart da madrugada, stop batido
que "destravava" à meia-noite).

Transição: quem nunca reiniciou, ou reiniciou antes da entrada da regra, conta
a partir de :data:`PLACAR_CONTINUO_DESDE` (início do dia do deploy). Assim, no
deploy, o placar de cada cliente é exatamente o de hoje, e daí em diante ele
só cresce até o próximo Reiniciar. Ninguém "herda" dias antigos de uma vez.

Ver docs/PLACAR_OVERLAY.md §2026-10-01.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.brasilia_time import BRASILIA_TZ

# Início do dia (Brasília) em que a regra entrou no ar. O script de deploy
# confere que é o dia de hoje: com data errada, o placar de todos saltaria
# (data antiga) ou perderia operações de hoje (data futura).
PLACAR_CONTINUO_DESDE = datetime(2026, 10, 1, tzinfo=BRASILIA_TZ).astimezone(timezone.utc)

# Carimbo ``score_day`` que o runtime grava no placar (snapshot Redis e
# ``robot_states``). O nome do campo ficou por compatibilidade: antes guardava
# o dia do placar; agora diz "placar da regra contínua". Placar carimbado com
# um dia antigo é de antes da regra e não pode ganhar do atual.
MARCA_PLACAR_CONTINUO = "continuo"


def _como_datetime(valor: Any) -> datetime | None:
    """Converte ISO/datetime em datetime UTC; ``None`` se não der."""
    if isinstance(valor, datetime):
        instante = valor
    elif isinstance(valor, str) and valor.strip():
        try:
            instante = datetime.fromisoformat(valor.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=timezone.utc)
    return instante.astimezone(timezone.utc)


def inicio_do_placar(stop_reset_at: Any) -> datetime:
    """Instante a partir do qual as operações contam no placar e no stop.

    Args:
        stop_reset_at: Último "Reiniciar placar" (datetime, ISO ou ``None``).

    Returns:
        O mais recente entre o último Reiniciar e :data:`PLACAR_CONTINUO_DESDE`.
    """
    reset = _como_datetime(stop_reset_at)
    if reset is None or reset < PLACAR_CONTINUO_DESDE:
        return PLACAR_CONTINUO_DESDE
    return reset


def conta_no_placar(finished_at: Any, stop_reset_at: Any) -> bool:
    """True se uma operação encerrada em ``finished_at`` está na janela atual.

    Args:
        finished_at: Fim da operação (datetime ou ISO).
        stop_reset_at: Último "Reiniciar placar".

    Returns:
        False para data ilegível ou anterior ao início da janela.
    """
    fim = _como_datetime(finished_at)
    if fim is None:
        return False
    return fim >= inicio_do_placar(stop_reset_at)


def placar_da_regra_atual(score_day: Any) -> bool:
    """True se um placar gravado já segue a regra contínua.

    Aceita também o carimbo do dia do deploy: o placar gravado nesse dia,
    antes da troca, é exatamente o da regra nova (janela desde
    :data:`PLACAR_CONTINUO_DESDE`).

    Args:
        score_day: Valor do campo ``score_day`` do snapshot ou do banco.
    """
    if score_day == MARCA_PLACAR_CONTINUO:
        return True
    dia_do_deploy = PLACAR_CONTINUO_DESDE.astimezone(BRASILIA_TZ).date().isoformat()
    return score_day == dia_do_deploy


def momento_da_operacao(trade: dict[str, Any]) -> datetime | None:
    """Quando a operação terminou, pelo mesmo campo que o placar usa.

    ``finished_at`` é o campo do recálculo do placar e do stop. A linha do
    Shift+O devolvida pela exclusão em ``marketing_simulated_trades`` só traz
    ``created_at`` — para ela é o mesmo instante que vai ao espelho.

    Returns:
        Instante em UTC, ou ``None`` se a operação não tem data legível.
    """
    for campo in ("finished_at", "closed_at", "created_at"):
        momento = _como_datetime(trade.get(campo))
        if momento is not None:
            return momento
    return None


def apagada_fora_do_placar(trade: dict[str, Any], stop_reset_at: Any) -> bool:
    """True se a operação apagada é de antes do placar atual.

    Caso de 02/10/2026: com o placar recém-reiniciado (0x0), apagar no
    Histórico 4 LOSS de ANTES do reset descontou cada uma do placar zerado —
    terminou 0x0 com lucro de +81,89 que nunca existiu. Operação fora da janela
    já não estava no placar nem no stop: apagá-la não pode mexer em nenhum dos
    dois. (O contador no banco já fazia certo: ``placar_apagar`` só desfaz
    lançamentos do período atual.)

    Sem data legível a resposta é False — fica o comportamento de antes
    (desconta), em vez de esconder do placar uma operação que podia estar nele.
    """
    momento = momento_da_operacao(trade)
    if momento is None:
        return False
    return not conta_no_placar(momento, stop_reset_at)
