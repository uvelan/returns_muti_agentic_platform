from collections.abc import Sequence

from return_platform.data_platform.operational_generation.adapters.protocol import ExecutionAdapter
from return_platform.data_platform.operational_generation.write_models import Operation


def validate_connection(adapter: ExecutionAdapter, operations: Sequence[Operation]) -> None:
    for op in operations:
        if op.type not in adapter.supported_operation_types:
            raise ValueError(
                f"Operation type {op.type} not supported by adapter {adapter.adapter_key}"
            )
        if op.asset_id not in adapter.supported_asset_ids:
            raise ValueError(f"Asset {op.asset_id} not supported by adapter {adapter.adapter_key}")
