class ExecutionMetrics:
    def __init__(self) -> None:
        self.total_runs = 0
        self.successful_runs = 0
        self.failed_runs = 0
        self.rollbacks = 0

    def record_run(self, success: bool) -> None:
        self.total_runs += 1
        if success:
            self.successful_runs += 1
        else:
            self.failed_runs += 1

    def record_rollback(self) -> None:
        self.rollbacks += 1


global_execution_metrics = ExecutionMetrics()
