"""Gerador isolado de operações sintéticas para demonstrações de marketing."""

from __future__ import annotations

import hashlib
import random
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from backend.named_strategies import (
    RETRACEMENT_SR_TIMEFRAMES,
    STRATEGY_CANDLE_FLOW,
    STRATEGY_CONTINUATION,
    STRATEGY_EXHAUSTION_REVERSAL,
    STRATEGY_LABELS,
    STRATEGY_RETRACEMENT_SR,
)

if TYPE_CHECKING:
    from backend.admin_repository import AdminRepository

_SIMULATED_STRATEGY_FIELDS = (
    "strategy_name",
    "strategy_key",
    "strategy_summary",
    "analysis_detail",
    "used_strategies",
    "timeframe",
    "period",
)


class MarketingSimulationService:
    """Produz histórico explicitamente sintético sem chamar a corretora."""

    def __init__(
        self,
        *,
        seed: str,
        target_win_rate: int,
        history: list[dict[str, Any]] | None = None,
        repository: AdminRepository | None = None,
        company_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        if target_win_rate < 0 or target_win_rate > 100:
            raise ValueError("Taxa de acerto deve ficar entre 0 e 100")
        if repository is not None and (not company_id or not user_id):
            raise ValueError("Empresa e usuário são obrigatórios para persistência")
        self.target_win_rate = target_win_rate
        self.repository = repository
        self.company_id = company_id
        self.user_id = user_id
        seed_number = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16], 16)
        self._random = random.Random(seed_number)
        self.history: list[dict[str, Any]] = []
        self._total = 0
        self._wins = 0
        self.replace_history(history or [])

    @staticmethod
    def decide_result(*, wins: int, losses: int, target_win_rate: int) -> str:
        """
        Decide WIN/LOSS pela taxa acumulada alvo (igual ao gerador sintético).

        Args:
            wins: Vitórias já contabilizadas no placar.
            losses: Derrotas já contabilizadas no placar.
            target_win_rate: Taxa de acertividade configurada (0-100).

        Returns:
            ``WIN`` ou ``LOSS`` para a próxima operação.
        """
        total = max(0, int(wins)) + max(0, int(losses)) + 1
        expected_wins = round(total * max(0, min(100, int(target_win_rate))) / 100)
        return "WIN" if expected_wins > max(0, int(wins)) else "LOSS"

    @staticmethod
    def profit_for_result(*, result: str, amount: float, payout: float | int) -> float:
        """Calcula o lucro/prejuízo visual a partir do resultado forçado."""
        resolved_amount = float(amount)
        resolved_payout = float(payout)
        if str(result).upper() == "WIN":
            return round(resolved_amount * resolved_payout / 100, 2)
        return -resolved_amount

    ASSET_POOL = (
        "EURUSD-OTC",
        "GBPUSD-OTC",
        "USDJPY-OTC",
        "AUDUSD-OTC",
        "EURGBP-OTC",
        "USDCHF-OTC",
        "EURJPY-OTC",
        "NZDUSD-OTC",
        "USDCAD-OTC",
        "AUDJPY-OTC",
        "GBPJPY-OTC",
    )
    MAX_GENERATED_TRADES = 100
    # Duração da vela operacional (timeframe exibido no histórico).
    PERIOD_SECONDS = {"M1": 60, "M5": 300, "M15": 900}
    DEFAULT_PERIOD = "M5"
    # Intervalos irregulares entre operações (múltiplos da vela). O robô analisa
    # a cada candle, mas só entra quando há sinal — não opera a cada minuto.
    GAP_MULTIPLIER_RANGE = {
        "M1": (4, 22),
        "M5": (2, 9),
        "M15": (2, 6),
    }
    CONFLUENCE_COMPONENTS = (
        "EMA9/EMA21",
        "RSI",
        "Candle Force",
        "Pavios",
        "Suporte/Resistencia",
        "Volatilidade",
        "Payout",
    )
    # Payout típico OTC quando a Bullex não responde.
    FALLBACK_PAYOUT = 85

    @staticmethod
    def _strategy_seed(trade: dict[str, Any]) -> str:
        """Gera semente determinística para estratégia simulada estável."""
        parts = (
            str(trade.get("id") or "").strip(),
            str(trade.get("_synthetic_sequence") or "").strip(),
            str(trade.get("broker_order_id") or "").strip(),
            str(trade.get("asset") or "").strip(),
            str(trade.get("direction") or "").strip(),
            str(trade.get("created_at") or "").strip(),
        )
        return "|".join(part for part in parts if part)

    @classmethod
    def build_simulated_strategy(
        cls,
        *,
        seed: str,
        direction: str,
        asset: str,
        period: str,
    ) -> dict[str, Any]:
        """
        Monta estratégia simulada realista para exibição no histórico.

        Args:
            seed: Semente determinística (id/sequência da operação).
            direction: CALL ou PUT.
            asset: Símbolo do ativo.
            period: Timeframe operacional (M1, M5 ou M15).

        Returns:
            Campos de estratégia compatíveis com ``TRADE_ANALYSIS_FIELDS``.
        """
        period_key = str(period or cls.DEFAULT_PERIOD).strip().upper()
        if period_key not in cls.PERIOD_SECONDS:
            period_key = cls.DEFAULT_PERIOD
        seed_number = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16], 16)
        rng = random.Random(seed_number)
        direction_key = str(direction or "CALL").strip().upper()
        asset_label = str(asset or "EURUSD-OTC").replace("-OTC", "")

        candidates = [
            STRATEGY_RETRACEMENT_SR,
            STRATEGY_EXHAUSTION_REVERSAL,
            STRATEGY_CANDLE_FLOW,
            STRATEGY_CONTINUATION,
            "CONFLUENCE",
            "CONFLUENCE",
        ]
        if period_key not in RETRACEMENT_SR_TIMEFRAMES:
            candidates = [key for key in candidates if key != STRATEGY_RETRACEMENT_SR]
        choice = rng.choice(candidates)

        if choice == "CONFLUENCE":
            used = rng.sample(
                list(cls.CONFLUENCE_COMPONENTS),
                k=rng.randint(2, min(4, len(cls.CONFLUENCE_COMPONENTS))),
            )
            strategy_name = "Confluência " + " + ".join(used)
            strategy_key = STRATEGY_CONTINUATION
        else:
            strategy_name = STRATEGY_LABELS[choice]
            strategy_key = choice
            used = [choice]

        direction_label = "compra" if direction_key == "CALL" else "venda"
        strategy_summary = (
            f"Sinal de {direction_label} em {asset_label} ({period_key}) "
            f"com leitura {strategy_name.lower()}."
        )
        analysis_detail = (
            f"Setup simulado para demonstração: {strategy_name}. "
            f"Direção {direction_key} no timeframe {period_key}, "
            f"com confluência de {', '.join(used)}."
        )
        return {
            "strategy_name": strategy_name,
            "strategy_key": strategy_key,
            "strategy_summary": strategy_summary,
            "analysis_detail": analysis_detail,
            "used_strategies": used,
            "timeframe": period_key,
            "period": period_key,
        }

    @classmethod
    def enrich_trade_with_strategy(
        cls,
        trade: dict[str, Any],
        *,
        period: str | None = None,
    ) -> dict[str, Any]:
        """
        Garante campos de estratégia simulada em trades sintéticos.

        Args:
            trade: Operação marketing (persistida ou recém-gerada).
            period: Timeframe fallback quando ausente no trade.

        Returns:
            Cópia enriquecida com estratégia determinística se faltante.
        """
        if str(trade.get("strategy_name") or "").strip():
            enriched = dict(trade)
            if not str(enriched.get("timeframe") or "").strip():
                enriched["timeframe"] = str(
                    period or enriched.get("period") or cls.DEFAULT_PERIOD
                ).upper()
            return enriched
        resolved_period = str(
            trade.get("timeframe") or trade.get("period") or period or cls.DEFAULT_PERIOD
        ).upper()
        strategy = cls.build_simulated_strategy(
            seed=cls._strategy_seed(trade),
            direction=str(trade.get("direction") or "CALL"),
            asset=str(trade.get("asset") or "EURUSD-OTC"),
            period=resolved_period,
        )
        enriched = dict(trade)
        enriched.update(strategy)
        return enriched

    @classmethod
    def preserve_simulated_metadata(
        cls,
        source: dict[str, Any],
        stored: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Mantém metadados simulados que não existem na tabela marketing.

        Args:
            source: Trade gerado em memória antes do insert.
            stored: Trade retornado pelo repositório.

        Returns:
            Trade mesclado com estratégia/timeframe preservados.
        """
        merged = dict(stored)
        for field in _SIMULATED_STRATEGY_FIELDS:
            if source.get(field) is not None:
                merged[field] = source[field]
        return cls.enrich_trade_with_strategy(merged)

    @staticmethod
    def normalize_created_at(value: str | None) -> str:
        """
        Converte data/hora ISO8601 para UTC ISO8601.

        Args:
            value: Timestamp ISO8601 (com ou sem timezone). ``None``/vazio
                usam o instante atual em UTC.

        Returns:
            Timestamp ISO8601 em UTC.

        Raises:
            ValueError: Quando o valor não é uma data/hora válida.
        """
        if value is None:
            return datetime.now(timezone.utc).isoformat()
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("Data/hora não pode ser vazia")
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Data/hora inválida") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()

    def next_trade(
        self,
        *,
        amount: float | None = None,
        payout: int | None = None,
        asset: str | None = None,
        direction: str | None = None,
        result: str | None = None,
        created_at: str | None = None,
        period: str | None = None,
    ) -> dict[str, Any]:
        """
        Gera a próxima operação simulada.

        Por padrão o resultado (WIN/LOSS) obedece ``target_win_rate``. Quando
        ``result`` é informado (Shift+O), o placar força WIN ou LOSS. Valor,
        payout, ativo e direção podem ser definidos antes da geração.

        Args:
            amount: Valor da operação; se omitido, escolhe um valor típico.
            payout: Percentual de payout (0-100); se omitido, sorteia 78-92.
            asset: Ativo em maiúsculas; se omitido, sorteia um OTC comum.
            direction: CALL ou PUT; se omitido, sorteia.
            result: WIN ou LOSS forçado; se omitido, usa a taxa alvo.
            created_at: Timestamp ISO8601; se omitido, usa o instante atual.
            period: Timeframe operacional (M1/M5/M15) para estratégia simulada.

        Returns:
            Operação com marcadores obrigatórios de conteúdo sintético.

        Raises:
            ValueError: Quando amount/payout/direction/result/created_at
                estão fora das regras.
        """
        resolved_amount = float(amount) if amount is not None else float(
            self._random.choice([2, 5, 10, 20])
        )
        if not (resolved_amount > 0) or not (resolved_amount == resolved_amount):
            raise ValueError("Valor da operação deve ser maior que zero")

        resolved_payout = int(payout) if payout is not None else self._random.randint(78, 92)
        if resolved_payout < 0 or resolved_payout > 100:
            raise ValueError("Payout deve ficar entre 0 e 100")

        resolved_asset = (asset or "").strip().upper() or self._random.choice(
            list(self.ASSET_POOL)
        )
        resolved_direction = (direction or "").strip().upper() or self._random.choice(
            ["CALL", "PUT"]
        )
        if resolved_direction not in {"CALL", "PUT"}:
            raise ValueError("Direção deve ser CALL ou PUT")

        resolved_created_at = self.normalize_created_at(created_at)

        self._total += 1
        if result is not None:
            resolved_result = str(result).strip().upper()
            if resolved_result not in {"WIN", "LOSS"}:
                raise ValueError("Resultado deve ser WIN ou LOSS")
        else:
            resolved_result = self.decide_result(
                wins=self._wins,
                losses=self._total - 1 - self._wins,
                target_win_rate=self.target_win_rate,
            )
        if resolved_result == "WIN":
            self._wins += 1

        profit = self.profit_for_result(
            result=resolved_result,
            amount=resolved_amount,
            payout=resolved_payout,
        )
        resolved_period = str(period or self.DEFAULT_PERIOD).strip().upper()
        strategy = self.build_simulated_strategy(
            seed=f"{self._total}|{resolved_created_at}|{resolved_asset}|{resolved_direction}",
            direction=resolved_direction,
            asset=resolved_asset,
            period=resolved_period,
        )
        item = {
            "id": f"synthetic-{self._total:06d}",
            "is_simulated": True,
            "source": "marketing_demo",
            "account_mode": "SIMULATED_MARKETING",
            "disclaimer": "Resultados simulados — não representam operações reais",
            "result": resolved_result,
            "asset": resolved_asset,
            "direction": resolved_direction,
            "amount": resolved_amount,
            "payout": resolved_payout,
            "profit": profit,
            "created_at": resolved_created_at,
            **strategy,
        }
        self.history.append(item)
        return dict(item)

    def build_score_results(self, *, wins: int, losses: int) -> list[str]:
        """
        Monta a sequência WIN/LOSS embaralhada para o placar desejado.

        Args:
            wins: Quantidade de vitórias no placar.
            losses: Quantidade de derrotas no placar.

        Returns:
            Lista de resultados na ordem em que o histórico será gerado.

        Raises:
            ValueError: Quando o total é zero ou excede o limite.
        """
        resolved_wins = int(wins)
        resolved_losses = int(losses)
        total = resolved_wins + resolved_losses
        if resolved_wins < 0 or resolved_losses < 0:
            raise ValueError("Wins e loss não podem ser negativos")
        if total <= 0:
            raise ValueError("Informe ao menos uma operação no placar")
        if total > self.MAX_GENERATED_TRADES:
            raise ValueError(
                f"Placar máximo de {self.MAX_GENERATED_TRADES} operações por geração"
            )
        results = (["WIN"] * resolved_wins) + (["LOSS"] * resolved_losses)
        self._random.shuffle(results)
        return results

    def build_operation_timestamps(
        self,
        count: int,
        *,
        period: str = DEFAULT_PERIOD,
        now: datetime | None = None,
    ) -> list[str]:
        """
        Gera horários irregulares dentro da janela operacional.

        O El Capo analisa a cada vela, mas só entra quando há sinal válido.
        Os gaps entre operações simuladas variam (múltiplos da vela + segundos
        não redondos), evitando a aparência de uma operação a cada minuto.

        Args:
            count: Quantidade de timestamps.
            period: Timeframe operacional (M1, M5 ou M15).
            now: Instante final da janela (padrão: UTC atual).

        Returns:
            Lista ISO8601 em ordem cronológica crescente.

        Raises:
            ValueError: Quando o período é inválido ou count < 0.
        """
        if count < 0:
            raise ValueError("Quantidade de horários inválida")
        period_key = str(period or self.DEFAULT_PERIOD).strip().upper()
        period_seconds = self.PERIOD_SECONDS.get(period_key)
        if period_seconds is None:
            raise ValueError("Período deve ser M1, M5 ou M15")
        if count == 0:
            return []

        end = now or datetime.now(timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        min_mult, max_mult = self.GAP_MULTIPLIER_RANGE[period_key]
        stamps: list[datetime] = []
        cursor = end - timedelta(seconds=self._random.uniform(45, 240))
        stamps.append(cursor)

        for _ in range(count - 1):
            gap_mult = self._random.uniform(min_mult, max_mult)
            gap_seconds = (gap_mult * period_seconds) + self._random.randint(23, 187)
            cursor = cursor - timedelta(seconds=gap_seconds)
            stamps.append(cursor)

        stamps.reverse()
        normalized: list[str] = []
        for stamp in stamps:
            adjusted = stamp.replace(
                second=self._random.randint(3, 57),
                microsecond=0,
            )
            normalized.append(adjusted.astimezone(timezone.utc).isoformat())
        return normalized

    async def generate_score_history(
        self,
        *,
        wins: int,
        losses: int,
        amount: float,
        payout: int | None = None,
        asset: str | None = None,
        period: str = DEFAULT_PERIOD,
        payout_by_asset: dict[str, int] | None = None,
        payout_resolver: Callable[[str], Awaitable[int | None]] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Cria operações sintéticas para o placar solicitado, sem apagar as antigas.

        Args:
            wins: Quantidade de WINs desejada.
            losses: Quantidade de LOSSes desejada.
            amount: Valor de entrada único para todas as operações.
            payout: Payout fixo opcional; se omitido, consulta por ativo.
            asset: Ativo fixo; se omitido, sorteia por operação.
            period: Timeframe da janela de horários (M1/M5/M15).
            payout_by_asset: Mapa símbolo → payout já resolvido.
            payout_resolver: Callback async (symbol) → payout; usado sob demanda.

        Returns:
            Histórico gerado e persistido (ordem cronológica).

        Raises:
            ValueError: Quando os parâmetros estão fora das regras.
            RuntimeError: Se o simulador não estiver ligado a um repositório.
        """
        repository, company_id, user_id = self._persistence_context()
        resolved_amount = float(amount)
        if not (resolved_amount > 0) or not (resolved_amount == resolved_amount):
            raise ValueError("Valor de entrada deve ser maior que zero")
        if payout is not None:
            resolved_fixed_payout = int(payout)
            if resolved_fixed_payout < 0 or resolved_fixed_payout > 100:
                raise ValueError("Payout deve ficar entre 0 e 100")
        else:
            resolved_fixed_payout = None

        fixed_asset = (asset or "").strip().upper() or None
        results = self.build_score_results(wins=wins, losses=losses)
        timestamps = self.build_operation_timestamps(len(results), period=period)
        asset_payouts = {
            str(symbol).strip().upper(): int(value)
            for symbol, value in (payout_by_asset or {}).items()
        }

        generated: list[dict[str, Any]] = []
        for index, result in enumerate(results):
            trade_asset, trade_payout = await self._resolve_asset_and_payout(
                preferred_asset=fixed_asset,
                fixed_payout=resolved_fixed_payout,
                asset_payouts=asset_payouts,
                payout_resolver=payout_resolver,
            )
            trade = self.next_trade(
                amount=resolved_amount,
                payout=trade_payout,
                asset=trade_asset,
                result=result,
                created_at=timestamps[index],
                period=period,
            )
            stored = await repository.save_simulated_trade(company_id, user_id, trade)
            stored = self.preserve_simulated_metadata(trade, stored)
            self.history[-1] = dict(stored)
            generated.append(dict(stored))
        return generated

    async def _resolve_asset_and_payout(
        self,
        *,
        preferred_asset: str | None,
        fixed_payout: int | None,
        asset_payouts: dict[str, int],
        payout_resolver: Callable[[str], Awaitable[int | None]] | None,
    ) -> tuple[str, int]:
        """
        Escolhe ativo e payout, com fallback quando a Bullex falha.

        Args:
            preferred_asset: Ativo fixo; None sorteia no pool forex permitido.
            fixed_payout: Payout explícito do request (pula consulta).
            asset_payouts: Cache mutável símbolo → payout nesta geração.
            payout_resolver: Callback async de consulta real.

        Returns:
            Par (ativo, payout).

        Raises:
            ValueError: Quando não há payout e não há fallback possível.
        """
        candidates: list[str]
        if preferred_asset:
            candidates = [preferred_asset]
        else:
            candidates = list(self.ASSET_POOL)
            self._random.shuffle(candidates)

        if fixed_payout is not None:
            return candidates[0], fixed_payout

        for symbol in candidates:
            if symbol in asset_payouts:
                return symbol, asset_payouts[symbol]
            if payout_resolver is None:
                continue
            resolved = await payout_resolver(symbol)
            if resolved is None:
                continue
            value = int(resolved)
            asset_payouts[symbol] = value
            return symbol, value

        # Sem resposta da corretora: usa payout típico OTC para não bloquear o demo.
        fallback_asset = preferred_asset or candidates[0]
        asset_payouts[fallback_asset] = self.FALLBACK_PAYOUT
        return fallback_asset, self.FALLBACK_PAYOUT

    def replace_history(self, history: list[dict[str, Any]]) -> None:
        """
        Substitui o cache e recalcula o estado acumulado do gerador.

        Args:
            history: Trades sintéticos persistidos do usuário autenticado.

        Returns:
            None.
        """
        self.history = [dict(item) for item in history]
        self._total = max(
            (
                int(item.get("_synthetic_sequence") or index)
                for index, item in enumerate(self.history, start=1)
            ),
            default=0,
        )
        self._wins = sum(1 for item in self.history if item.get("result") == "WIN")

    def build_stats(self) -> dict[str, Any]:
        """
        Calcula o placar derivado exclusivamente do histórico sintético.

        Returns:
            Contadores usados pelo robô e pelo painel da conta marketing.
        """
        wins = sum(1 for item in self.history if item.get("result") == "WIN")
        losses = sum(1 for item in self.history if item.get("result") == "LOSS")
        total = wins + losses
        profit = round(sum(float(item.get("profit") or 0) for item in self.history), 2)
        win_rate = round((wins / total) * 100, 2) if total else 0.0
        return {
            "wins": wins,
            "losses": losses,
            "total_trades": total,
            "win_rate": win_rate,
            "profit": profit,
        }

    async def update_trade(
        self,
        trade_id: str,
        changes: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        Atualiza uma operação e sincroniza o cache do simulador.

        Args:
            trade_id: Identificador da operação sintética.
            changes: Campos de resultado validados pela camada HTTP.

        Returns:
            Trade atualizado ou None quando ele não pertence à sessão.

        Raises:
            RuntimeError: Se o simulador não estiver ligado a um repositório.
        """
        repository, company_id, user_id = self._persistence_context()
        updated = await repository.update_simulated_trade(
            company_id,
            user_id,
            trade_id,
            changes,
        )
        await self.reload_history()
        return updated

    async def delete_trade(self, trade_id: str) -> dict[str, Any] | None:
        """
        Exclui uma operação e sincroniza o cache do simulador.

        Args:
            trade_id: Identificador da operação sintética (UUID) ou
                ``broker_order_id`` da Bullex.

        Returns:
            A operação excluída, ou None se não existia.

        Raises:
            RuntimeError: Se o simulador não estiver ligado a um repositório.
        """
        repository, company_id, user_id = self._persistence_context()
        deleted = await repository.delete_simulated_trade(company_id, user_id, trade_id)
        await self.reload_history()
        return deleted

    async def reload_history(self) -> list[dict[str, Any]]:
        """
        Recarrega os trades persistidos da sessão e recalcula os contadores.

        Returns:
            Cópia do histórico recarregado.

        Raises:
            RuntimeError: Se o simulador não estiver ligado a um repositório.
        """
        repository, company_id, user_id = self._persistence_context()
        history = await repository.list_simulated_trades(
            company_id,
            user_id,
            limit=10_000,
        )
        self.replace_history(history)
        return [dict(item) for item in self.history]

    def _persistence_context(self) -> tuple[AdminRepository, str, str]:
        """Retorna dependências server-only exigidas pelas mutações."""
        if self.repository is None or self.company_id is None or self.user_id is None:
            raise RuntimeError("Simulador não configurado para persistência")
        return self.repository, self.company_id, self.user_id
