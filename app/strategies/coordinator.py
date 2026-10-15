from app.storage.repository import create_run, finish_run
from app.strategies.base import Strategy, StrategyContext, StrategySignal


class StrategyCoordinator:
    def __init__(self, strategies: list[Strategy]):
        keys = [strategy.key for strategy in strategies]

        if len(keys) != len(set(keys)):
            raise ValueError("Las claves de las estrategias deben ser únicas.")

        self.strategies = strategies

    def run(
        self,
        context: StrategyContext,
        mode: str = "testnet",
    ) -> dict[str, list[StrategySignal]]:
        run_id = create_run(
            mode=mode,
            details=f"Coordinador: {len(self.strategies)} estrategias",
        )

        results: dict[str, list[StrategySignal]] = {}

        try:
            for strategy in self.strategies:
                results[strategy.key] = strategy.generate_signals(context)

            finish_run(run_id, status="completed")
            return results

        except Exception as exc:
            finish_run(
                run_id,
                status="failed",
                details=f"Error: {type(exc).__name__}: {exc}",
            )
            raise