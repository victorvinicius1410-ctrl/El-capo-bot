"""Pacote de testes do backend.

Placar contínuo (``backend.placar_janela``): em produção a regra vale desde o
dia do deploy (``PLACAR_CONTINUO_DESDE``). Nos testes ela vale desde sempre,
para as operações com data "agora" contarem em qualquer dia em que a suíte
rodar; os testes da transição ajustam a data explicitamente.
"""

from datetime import datetime, timezone

from backend import placar_janela
from backend.brasilia_time import BRASILIA_TZ

placar_janela.PLACAR_CONTINUO_DESDE = datetime(2020, 1, 1, tzinfo=BRASILIA_TZ).astimezone(timezone.utc)
