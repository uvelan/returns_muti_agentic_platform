from .protocol import ExecutionAdapter

_ADAPTERS: dict[str, type[ExecutionAdapter]] = {}


def register_adapter(key: str, adapter_cls: type[ExecutionAdapter]) -> None:
    _ADAPTERS[key] = adapter_cls


def get_adapter_class(key: str) -> type[ExecutionAdapter]:
    if key not in _ADAPTERS:
        raise ValueError(f"Unknown adapter: {key}")
    return _ADAPTERS[key]
