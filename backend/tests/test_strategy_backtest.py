import unittest

from backend.backtesting import run_walk_forward_backtest
from tests.test_candle_analysis import make_support_reversal_candles


def with_timestamps(
    candles: list[dict[str, float]],
    *,
    start_timestamp: int,
    interval: int,
) -> list[dict[str, float]]:
    stamped: list[dict[str, float]] = []
    for index, candle in enumerate(candles):
        stamped.append(
            {
                **candle,
                "from": start_timestamp + index * interval,
            }
        )
    return stamped


def directional_candles(
    *,
    start_timestamp: int,
    interval: int,
    count: int,
    bullish: bool,
) -> list[dict[str, float]]:
    candles: list[dict[str, float]] = []
    price = 1.1000
    step = 0.0003 if bullish else -0.0003
    for index in range(count):
        open_price = price
        candle_step = step * -0.33 if index % 4 == 2 else step
        close_price = price + candle_step
        candles.append(
            {
                "from": start_timestamp + index * interval,
                "open": open_price,
                "close": close_price,
                "min": min(open_price, close_price) - 0.00005,
                "max": max(open_price, close_price) + 0.00005,
                "volume": 1,
            }
        )
        price = close_price
    return candles


class StrategyBacktestTests(unittest.TestCase):
    def test_walk_forward_never_uses_entry_or_future_candle_in_analysis(self) -> None:
        primary_start = 1_700_000_000
        # Repete o setup de reversão em suporte (já validado pelo motor clássico)
        # várias vezes para gerar janelas walk-forward com trade_allowed.
        base = make_support_reversal_candles()
        repeated: list[dict[str, float]] = []
        for block in range(3):
            offset = block * 0.0015
            for candle in base:
                repeated.append(
                    {
                        "open": candle["open"] + offset,
                        "close": candle["close"] + offset,
                        "min": candle["min"] + offset,
                        "max": candle["max"] + offset,
                        "volume": 1,
                    }
                )
        primary = with_timestamps(repeated, start_timestamp=primary_start, interval=60)

        result = run_walk_forward_backtest(
            symbol="EURUSD-OTC",
            candles_by_timeframe={
                "M1": primary,
                "M5": directional_candles(
                    start_timestamp=primary_start - (40 * 300),
                    interval=300,
                    count=56,
                    bullish=True,
                ),
                "M15": directional_candles(
                    start_timestamp=primary_start - (40 * 900),
                    interval=900,
                    count=46,
                    bullish=True,
                ),
            },
            primary_timeframe="M1",
            payout=90,
            strategy_mode="conservative",
        )

        self.assertGreater(result["total_trades"], 0)
        self.assertEqual(result["model_version"], "backup-classic")
        for trade in result["trades"]:
            self.assertLessEqual(trade["analysis_endtime"], trade["entry_time"])
            self.assertEqual(trade["analysis_candles_includes_entry"], False)

    def test_walk_forward_rejects_series_without_timestamps(self) -> None:
        with self.assertRaises(ValueError):
            run_walk_forward_backtest(
                symbol="EURUSD-OTC",
                candles_by_timeframe={
                    "M1": [{"open": 1, "close": 2, "min": 1, "max": 2}] * 40,
                    "M5": [],
                    "M15": [],
                },
                primary_timeframe="M1",
                payout=85,
            )


if __name__ == "__main__":
    unittest.main()
