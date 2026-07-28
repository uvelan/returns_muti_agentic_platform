from uuid import UUID

from return_platform.data_platform.operational_generation.apply_models import (
    ExecutionRun,
    StepReceipt,
)


class ExecutionRepository:
    def __init__(self) -> None:
        self._runs: dict[UUID, ExecutionRun] = {}
        self._receipts: dict[UUID, StepReceipt] = {}

    def save_run(self, run: ExecutionRun) -> None:
        self._runs[run.run_id] = run

    def get_run(self, run_id: UUID) -> ExecutionRun | None:
        return self._runs.get(run_id)

    def get_run_by_plan(self, plan_id: UUID) -> ExecutionRun | None:
        for run in self._runs.values():
            if run.plan_id == plan_id:
                return run
        return None

    def save_receipt(self, receipt: StepReceipt) -> None:
        self._receipts[receipt.receipt_id] = receipt

    def get_receipts(self, run_id: UUID) -> list[StepReceipt]:
        return [r for r in self._receipts.values() if r.run_id == run_id]
