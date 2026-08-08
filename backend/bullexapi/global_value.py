"""Estado global por sessão do bullexapi (ContextVar).

Historicamente este módulo guardava SSID/balance_id/flags WS em variáveis
de módulo — um único valor por processo. Com várias sessões no
``bullex-service``, isso obrigava ``BULLEX_MAX_CONCURRENT_API_CALLS=1``
(``MVP_SAFE_MODE``).

Fase 3 (isolamento): cada chamada sob ``SessionManager._session_context``
(e cada thread WS de um ``BullexAPI``) aponta
``current_session_globals`` para um ``SessionGlobals`` próprio. Atributos
como ``global_value.SSID`` continuam funcionando via proxy no módulo.
"""

from __future__ import annotations

import sys
import types
from contextvars import ContextVar, Token
from dataclasses import dataclass, fields
from typing import Any, Optional


@dataclass
class SessionGlobals:
    """Estado mutável de uma sessão Bullex (SSID, balance, flags WS).

    Attributes:
        check_websocket_if_connect: Flag de conexão WS (None/0/1).
        ssl_Mutual_exclusion: Mutex de leitura WS (bloqueia send).
        ssl_Mutual_exclusion_write: Mutex de escrita WS.
        SSID: Cookie/token de sessão da corretora.
        check_websocket_if_error: Se o WS registrou erro.
        websocket_error_reason: Motivo do último erro WS.
        balance_id: Id da conta ativa (REAL/PRACTICE) na corretora.
        balance_id_owner: ``id(api)`` autorizado a gravar ``balance_id``.
    """

    check_websocket_if_connect: Any = None
    ssl_Mutual_exclusion: bool = False
    ssl_Mutual_exclusion_write: bool = False
    SSID: Any = None
    check_websocket_if_error: bool = False
    websocket_error_reason: Any = None
    balance_id: Any = None
    balance_id_owner: Any = None


_FIELD_NAMES = frozenset(f.name for f in fields(SessionGlobals))

# Fallback de processo: testes e código fora de _session_context / WS bind.
_fallback_globals = SessionGlobals()

current_session_globals: ContextVar[Optional[SessionGlobals]] = ContextVar(
    "bullexapi_current_session_globals",
    default=None,
)


def get_session_globals() -> SessionGlobals:
    """Retorna o ``SessionGlobals`` do ContextVar ou o fallback do processo.

    Returns:
        Instância ativa para o contexto atual (task/thread) ou fallback.
    """
    current = current_session_globals.get()
    if current is not None:
        return current
    return _fallback_globals


def set_session_globals(globals_: SessionGlobals) -> Token:
    """Define o ``SessionGlobals`` do contexto atual.

    Args:
        globals_: Estado da sessão a ativar neste ContextVar.

    Returns:
        Token para ``reset_session_globals``.
    """
    return current_session_globals.set(globals_)


def reset_session_globals(token: Token) -> None:
    """Restaura o ContextVar ao valor anterior ao ``set_session_globals``.

    Args:
        token: Token devolvido por ``set_session_globals``.
    """
    current_session_globals.reset(token)


def bind_api_session_globals(api: Any) -> Token:
    """Garante ``api._session_globals`` e ativa no ContextVar.

    Usado pelas callbacks WS (thread do websocket-client), que não herdam
    o ContextVar da task HTTP.

    Args:
        api: Instância ``BullexAPI`` dona do websocket.

    Returns:
        Token para ``reset_session_globals``.
    """
    sg = getattr(api, "_session_globals", None)
    if sg is None:
        sg = SessionGlobals()
        api._session_globals = sg
    return set_session_globals(sg)


class _GlobalValueModule(types.ModuleType):
    """Proxy: ``global_value.SSID`` lê/escreve o ``SessionGlobals`` ativo."""

    def __getattribute__(self, name: str) -> Any:
        if name in _FIELD_NAMES:
            return getattr(get_session_globals(), name)
        return types.ModuleType.__getattribute__(self, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _FIELD_NAMES:
            setattr(get_session_globals(), name, value)
            return
        types.ModuleType.__setattr__(self, name, value)


_module = sys.modules[__name__]
_module.__class__ = _GlobalValueModule
