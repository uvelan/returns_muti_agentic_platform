from return_platform.data_platform.operational_generation.adapter_factory import create_adapter
from return_platform.data_platform.operational_generation.write_models import TransactionGroup


async def execute_transaction_group(transaction_group: TransactionGroup) -> None:
    adapter = create_adapter(transaction_group.target_channel)
    if not await adapter.is_ready():
        raise RuntimeError(f"Adapter {adapter.adapter_key} is not ready.")

    await adapter.execute(transaction_group.operations)


async def compensate_transaction_group(transaction_group: TransactionGroup) -> None:
    adapter = create_adapter(transaction_group.target_channel)
    if not await adapter.is_ready():
        raise RuntimeError(f"Adapter {adapter.adapter_key} is not ready for compensation.")

    await adapter.compensate(transaction_group.operations)
