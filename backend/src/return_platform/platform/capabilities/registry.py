"""In-memory capability registry -- the only production implementation this scale needs."""

from __future__ import annotations

from typing import TypeVar, cast

from return_platform.platform.capabilities.contracts import (
    CapabilityName,
    CapabilityPublication,
)
from return_platform.platform.capabilities.errors import (
    CapabilityNotPublished,
    CapabilityTypeMismatch,
    DuplicateCapability,
)

T = TypeVar("T")


class InMemoryCapabilityRegistry:
    """Structurally satisfies CapabilityRegistry. See platform/capabilities/README.md."""

    def __init__(self) -> None:
        self._bindings: dict[tuple[CapabilityName, type], object] = {}
        self._providers: dict[tuple[CapabilityName, type], str] = {}

    def publish(
        self,
        capability: CapabilityName,
        contract: type[T],
        provider_module_id: str,
        instance: T,
    ) -> None:
        key = (capability, contract)
        if key in self._bindings:
            raise DuplicateCapability(
                f"{capability!r} is already published for contract {contract.__name__!r}"
            )
        if not isinstance(instance, contract):
            raise CapabilityTypeMismatch(
                f"{instance!r} does not satisfy {contract.__name__!r} for {capability!r}"
            )
        self._bindings[key] = instance
        self._providers[key] = provider_module_id

    def resolve(self, capability: CapabilityName, contract: type[T]) -> T:
        try:
            return cast(T, self._bindings[(capability, contract)])
        except KeyError as exc:
            raise CapabilityNotPublished(
                f"no provider has published {contract.__name__!r} for {capability!r}"
            ) from exc

    def resolve_optional(self, capability: CapabilityName, contract: type[T]) -> T | None:
        value = self._bindings.get((capability, contract))
        return cast(T, value) if value is not None else None

    def list(self) -> tuple[CapabilityPublication, ...]:
        return tuple(
            CapabilityPublication(
                capability=capability,
                contract_name=contract.__name__,
                provider_module_id=provider_module_id,
            )
            for (capability, contract), provider_module_id in self._providers.items()
        )
