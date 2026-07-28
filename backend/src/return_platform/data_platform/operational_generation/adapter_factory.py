from .adapters import ExecutionAdapter, get_adapter_class


def create_adapter(key: str) -> ExecutionAdapter:
    adapter_cls = get_adapter_class(key)
    return adapter_cls()
