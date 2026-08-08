import argparse
import json
from pathlib import Path
from typing import Any

from backend.backtesting import run_walk_forward_backtest


def load_dataset(path: Path) -> dict[str, Any]:
    """
    Carrega um dataset JSON de candles para o backtest.

    Args:
        path: Arquivo contendo símbolo, configuração e candles M1/M5/M15.

    Returns:
        Objeto JSON validado como dicionário.

    Raises:
        ValueError: Se a raiz do arquivo não for um objeto JSON.
        OSError: Se o arquivo não puder ser lido.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("BACKTEST_DATASET_OBJECT_REQUIRED")
    return payload


def main() -> None:
    """
    Executa o backtest walk-forward pela linha de comando.

    Args:
        Nenhum; os parâmetros são lidos via argparse.

    Returns:
        Não retorna valor; imprime o relatório JSON.

    Raises:
        Propaga erros de arquivo e validação para encerrar com código diferente de zero.
    """
    parser = argparse.ArgumentParser(description="Backtest walk-forward do motor ElCapo")
    parser.add_argument("dataset", type=Path, help="JSON com candles M1, M5 e M15")
    parser.add_argument("--output", type=Path, help="Arquivo opcional para salvar o relatório")
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)
    report = run_walk_forward_backtest(
        symbol=str(dataset.get("symbol") or "EURUSD-OTC"),
        candles_by_timeframe=dict(dataset.get("candles") or {}),
        primary_timeframe=str(dataset.get("primary_timeframe") or "M1"),
        payout=float(dataset.get("payout") or 85),
        strategy_mode=str(dataset.get("strategy_mode") or "conservative"),
    )
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)


if __name__ == "__main__":
    main()
