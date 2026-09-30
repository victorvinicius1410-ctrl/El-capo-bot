"""O cache de mercado não pode crescer para sempre (vazamento de 29–30/09).

A chave de velas leva o ``endtime`` da vela: cada minuto cria chaves novas por
ativo. Entrada vencida nunca era apagada — nem no cache compartilhado nem nas
duas cópias por usuário — e o robot-runtime crescia ~290 MB/h até o kernel
matar processos por falta de memória.
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from unittest.mock import patch

from backend import main
from backend.auto_trader import utc_now


def _chave_vela(ativo: str, minuto: int) -> str:
    return f"/candles?active={ativo}&count=160&endtime={1790000000 + minuto * 60}&interval=60"


class PodaDoCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        main.reset_shared_market_cache()
        main.session_response_cache.clear()

    tearDown = setUp

    def _gravar(self, chave: str, idade_s: int, usuarios: list[str]) -> None:
        entrada = main.BullexResponseCacheEntry(
            status_code=200,
            payload={"ok": True, "data": {"candles": [{"close": 1.0}] * 160}},
            expires_at=utc_now() - timedelta(seconds=idade_s),
        )
        main._shared_market_cache[chave] = entrada
        for usuario in usuarios:
            cache = main.get_session_cache(usuario)
            cache.responses[chave] = entrada
            cache.last_successful_responses[chave] = entrada
            cache.last_request_at[chave] = utc_now() - timedelta(seconds=idade_s)

    def test_dez_horas_de_velas_ficam_limitadas(self) -> None:
        usuarios = [f"u{i}" for i in range(8)]
        ativos = [f"PAR{i}-OTC" for i in range(20)]
        for minuto in range(600):  # 10 h, do mais antigo ao mais novo
            idade = (600 - minuto) * 60
            for ativo in ativos:
                self._gravar(_chave_vela(ativo, minuto), idade, usuarios)
        antes = main.market_cache_sizes()
        self.assertEqual(antes["shared"], 12000)  # sem poda: cresce sem parar
        main.prune_market_caches(force=True)
        depois = main.market_cache_sizes()
        # Ficam só as velas vencidas há menos de 10 min (+ a do minuto atual).
        self.assertLessEqual(depois["shared"], 20 * 11)
        self.assertLessEqual(depois["por_usuario"], 8 * 2 * 20 * 11)
        for cache in main.session_response_cache.values():
            self.assertLessEqual(len(cache.last_request_at), 20 * 11)

    def test_payout_e_entrada_fresca_ficam(self) -> None:
        self._gravar("/payouts?active=EURUSD-OTC", 3600, ["u1"])  # reserva do fallback
        self._gravar(_chave_vela("EURUSD-OTC", 1), -30, ["u1"])  # ainda válida
        self._gravar(_chave_vela("EURUSD-OTC", 0), 3600, ["u1"])  # velha
        main.prune_market_caches(force=True)
        self.assertIn("/payouts?active=EURUSD-OTC", main._shared_market_cache)
        self.assertIn(_chave_vela("EURUSD-OTC", 1), main._shared_market_cache)
        self.assertNotIn(_chave_vela("EURUSD-OTC", 0), main._shared_market_cache)
        cache = main.get_session_cache("u1")
        self.assertIn("/payouts?active=EURUSD-OTC", cache.last_successful_responses)
        self.assertNotIn(_chave_vela("EURUSD-OTC", 0), cache.responses)

    def test_trava_em_uso_nao_e_apagada(self) -> None:
        import asyncio

        velha = _chave_vela("EURUSD-OTC", 0)
        livre = _chave_vela("GBPUSD-OTC", 0)
        trava_ocupada = asyncio.Lock()
        trava_ocupada._locked = True  # simula um fetch em andamento
        main._shared_market_locks[velha] = trava_ocupada
        main._shared_market_locks[livre] = asyncio.Lock()
        main.prune_market_caches(force=True)
        self.assertIn(velha, main._shared_market_locks)
        self.assertNotIn(livre, main._shared_market_locks)

    def test_gravar_no_cache_chama_a_poda(self) -> None:
        with patch.object(main, "prune_market_caches", wraps=main.prune_market_caches) as poda:
            main.store_shared_market_cache("/payouts?active=X", 200, {"ok": True}, 5)
            main.store_shared_market_cache("/payouts?active=Y", 200, {"ok": True}, 5)
        self.assertEqual(poda.call_count, 2)  # o limite de 1x por minuto fica dentro dela


if __name__ == "__main__":
    unittest.main()
