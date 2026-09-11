"""Revalidação de canal fora do caminho crítico da compra.

Medição em produção 2026-09-01, 99 entradas reais correlacionadas por
``cycle_id`` (relógio local, independente do relógio da corretora):

- ``[ENTRY_COUNTDOWN_ZERO]`` caía no segundo 3,28 da vela (mediana).
- ``[ENTRY_SENT]`` caía no segundo 9,92 (mediana), p90 16,89.
- Entre os dois: 4,00s de mediana, dos quais 2,00s eram
  ``COUNTDOWN_ZERO -> ORDER_ATTEMPT`` — a revalidação de canal rodando DENTRO
  da janela de compra.

Esses 2s saem do começo da vela, que é justamente o que se quer preservar. O
pré-aquecimento roda a MESMA checagem enquanto a vela anterior ainda corre, e
deixa o cache fresco para ``refresh_candidate_execution_channel`` na compra.
Nenhuma validação é afrouxada: a checagem da compra continua lá.
"""

import unittest
from unittest.mock import AsyncMock, patch

from backend import main


class ChannelPrewarmTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        main._channel_prewarmed_cycle_by_user.clear()

    async def _run(self, **kwargs):
        defaults = dict(
            user_id="u1",
            candidate={"symbol": "EURUSD-OTC"},
            timeframe="M1",
            seconds_until_entry=1.0,
            cycle_id="c1",
        )
        defaults.update(kwargs)
        candidate = defaults.pop("candidate")
        timeframe = defaults.pop("timeframe")
        user_id = defaults.pop("user_id")
        with patch.object(main, "fresh_asset_open_for_active", new=AsyncMock(return_value=True)) as fake:
            await main.prewarm_execution_channel_before_entry(
                user_id, candidate, timeframe, **defaults
            )
        return fake

    async def test_pre_aquece_quando_a_janela_esta_perto(self):
        fake = await self._run(seconds_until_entry=1.0)
        fake.assert_awaited_once()

    async def test_nao_pre_aquece_cedo_demais(self):
        # Antes disso o dado chegaria à compra fora de
        # CHANNEL_CACHE_MAX_AGE_SECONDS e a chamada seria refeita à toa.
        fake = await self._run(seconds_until_entry=main.CHANNEL_PREWARM_LEAD_SECONDS + 0.5)
        fake.assert_not_awaited()

    async def test_nao_pre_aquece_com_a_janela_ja_aberta(self):
        fake = await self._run(seconds_until_entry=0)
        fake.assert_not_awaited()

    async def test_roda_uma_unica_vez_por_ciclo(self):
        # O worker faz poll de 0,15s perto da abertura: sem a trava isto viraria
        # uma rajada de /payouts na corretora.
        await self._run(seconds_until_entry=1.0)
        fake = await self._run(seconds_until_entry=1.0)
        fake.assert_not_awaited()

    async def test_ativo_novo_no_mesmo_ciclo_pre_aquece(self):
        # `cycle_id` NAO roda a cada entrada: medido em 08/09/2026, um mesmo
        # ciclo serviu GBPCHF-OTC, GBPAUD-OTC e EURCAD-OTC em ~6 minutos. Travar
        # so por ciclo deixava o 2o e o 3o ativo sem pre-aquecimento, e foi um
        # deles que estourou o timeout dentro da janela de compra.
        await self._run(cycle_id="c1", candidate={"symbol": "EURUSD-OTC"})
        fake = await self._run(cycle_id="c1", candidate={"symbol": "GBPUSD-OTC"})
        fake.assert_awaited_once()

    async def test_ciclo_novo_pre_aquece_de_novo(self):
        await self._run(cycle_id="c1")
        fake = await self._run(cycle_id="c2")
        fake.assert_awaited_once()

    async def test_candidato_sem_symbol_nao_chama_a_corretora(self):
        fake = await self._run(candidate={"symbol": ""})
        fake.assert_not_awaited()

    async def test_sem_cycle_id_nao_chama_a_corretora(self):
        fake = await self._run(cycle_id=None)
        fake.assert_not_awaited()

    async def test_falha_na_corretora_nao_propaga(self):
        # A espera da entrada não pode quebrar por causa do pré-aquecimento: o
        # caminho antigo continua inteiro na hora da compra.
        with patch.object(
            main, "fresh_asset_open_for_active", new=AsyncMock(side_effect=RuntimeError("boom"))
        ):
            await main.prewarm_execution_channel_before_entry(
                "u1", {"symbol": "EURUSD-OTC"}, "M1", seconds_until_entry=1.0, cycle_id="c1"
            )

    async def test_orcamento_e_maior_que_o_da_compra(self):
        # Pre-aquecer roda FORA da janela: apertar aqui os mesmos 0,9s da compra
        # fazia 5 de 9 tentativas voltarem vazias, e a compra refazia a consulta
        # dentro da janela — o custo que isto existe para evitar.
        self.assertGreater(
            main.CHANNEL_PREWARM_TIMEOUT_SECONDS,
            main.CHANNEL_REVALIDATION_TIMEOUT_SECONDS,
        )
        with patch.object(main, "fresh_asset_open_for_active", new=AsyncMock(return_value=True)) as fake:
            await main.prewarm_execution_channel_before_entry(
                "u1", {"symbol": "EURUSD-OTC"}, "M1", seconds_until_entry=5.0, cycle_id="c1"
            )
        self.assertGreater(
            fake.await_args.kwargs["timeout_seconds"], main.CHANNEL_REVALIDATION_TIMEOUT_SECONDS
        )

    async def test_timeout_nao_passa_do_tempo_que_sobra(self):
        # Pré-aquecer não pode atrasar a própria abertura da vela.
        with patch.object(main, "fresh_asset_open_for_active", new=AsyncMock(return_value=True)) as fake:
            await main.prewarm_execution_channel_before_entry(
                "u1", {"symbol": "EURUSD-OTC"}, "M1", seconds_until_entry=0.4, cycle_id="c1"
            )
        self.assertLessEqual(fake.await_args.kwargs["timeout_seconds"], 0.4)


if __name__ == "__main__":
    unittest.main()
