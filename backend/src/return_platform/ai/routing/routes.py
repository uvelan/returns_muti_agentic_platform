"""Construction of the immutable route set a configuration permits.

A route is the fully-resolved (provider, model, credential, tier) tuple the gateway
can dispatch on. Building them is pure: it reads settings and produces a tuple.
Everything mutable about a route at runtime -- circuit state, minute counters,
in-flight concurrency -- lives in `selection.py`, so a configuration reload can
rebuild routes without touching health state.
"""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass

from pydantic import SecretStr

from return_platform.ai.interception.store import InterceptionStore
from return_platform.ai.providers.anthropic import AnthropicProvider
from return_platform.ai.providers.contracts import AIProvider
from return_platform.ai.providers.durable_interception import DurableInterceptionProvider
from return_platform.ai.providers.google import GeminiProvider
from return_platform.ai.providers.manual import ManualFileProvider
from return_platform.ai.providers.nvidia import NvidiaProvider
from return_platform.ai.providers.ollama import OllamaProvider
from return_platform.ai.providers.openai import OpenAIResponsesProvider
from return_platform.ai.providers.replay import ReplayProvider, ReplayStore
from return_platform.ai.providers.simulator import SimulatorProvider
from return_platform.ai.routing.tasks import ModelTier
from return_platform.configuration.settings import Settings

logger = logging.getLogger("return_platform.ai.routing.routes")


@dataclass(frozen=True, slots=True)
class AIRoute:
    route_id: str
    provider_name: str
    model: str
    credential_id: str
    credential_fingerprint: str | None
    tier: ModelTier
    provider: AIProvider
    provider_priority: int
    model_priority: int
    credential_priority: int
    allowed_task_keys: frozenset[str] = frozenset()


def _fingerprint(secret: SecretStr | None) -> str | None:
    if secret is None:
        return None
    raw = secret.get_secret_value().encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def _provider(
    provider_name: str,
    settings: Settings,
    *,
    key: SecretStr | None,
    model: str,
    interception_store: InterceptionStore | None = None,
) -> AIProvider:
    if provider_name == "GOOGLE":
        return GeminiProvider(
            settings.model_copy(update={"google_api_key": key, "google_model": model})
        )
    if provider_name == "NVIDIA":
        return NvidiaProvider(
            settings.model_copy(update={"nvidia_api_key": key, "nvidia_model": model})
        )
    if provider_name == "OPENAI":
        return OpenAIResponsesProvider(
            settings.model_copy(update={"openai_api_key": key, "openai_model": model})
        )
    if provider_name == "ANTHROPIC":
        return AnthropicProvider(
            settings.model_copy(update={"anthropic_api_key": key, "anthropic_model": model})
        )
    if provider_name == "OLLAMA":
        return OllamaProvider(settings.model_copy(update={"ollama_model": model}))
    if provider_name == "SIMULATOR":
        return SimulatorProvider(settings)
    if provider_name == "MANUAL":
        return _manual_provider(settings, interception_store)
    raise ValueError(f"Unsupported AI provider: {provider_name}")


def _with_replay(
    adapter: AIProvider, settings: Settings, replay_store: ReplayStore | None
) -> AIProvider:
    """Wrap a provider so an identical request is answered from a recording.

    Applied to every provider rather than to a chosen one: a suite is only
    reproducible if *nothing* in it reaches a network, and a single unwrapped
    route is enough to make a run cost money and drift from the last one.

    A configured mode with no store is a no-op rather than an error. The store
    is a platform-Mongo dependency, and refusing to build routes without one
    would make `ai_replay_mode` unusable in exactly the bare-process cases it
    is most wanted in.
    """
    mode = settings.ai_replay_mode.upper()
    if mode == "OFF" or replay_store is None:
        return adapter
    return ReplayProvider(adapter, replay_store, strict=mode == "STRICT")


def _manual_provider(
    settings: Settings, interception_store: InterceptionStore | None
) -> AIProvider:
    """Where a MANUAL handoff waits, per `ai_manual_handoff`.

    `UI` is refused rather than silently downgraded when no interception store
    is wired: an operator watching the AI Control Center for a prompt that is
    actually sitting in a file on someone's disk would wait forever, and a
    silent fallback is exactly how that happens. `AUTO` keeps the old
    behaviour -- durable when the process has a store, filesystem otherwise --
    for the bare `pytest` and scripting cases MANUAL mostly exists for.
    """
    choice = settings.ai_manual_handoff.upper()
    if choice == "FILE":
        return ManualFileProvider(settings)
    if choice == "UI":
        if interception_store is None:
            raise ValueError(
                "ai_manual_handoff=UI needs an interception store, and this process has none; "
                "use AUTO to fall back to the filesystem, or FILE to ask for it explicitly"
            )
        return DurableInterceptionProvider(settings, interception_store)
    if interception_store is not None:
        return DurableInterceptionProvider(settings, interception_store)
    return ManualFileProvider(settings)


def _provider_credentials(settings: Settings, provider_name: str) -> tuple[SecretStr | None, ...]:
    if provider_name == "GOOGLE":
        return tuple(settings.resolved_google_api_keys)
    if provider_name == "NVIDIA":
        return tuple(settings.resolved_nvidia_api_keys)
    if provider_name == "OPENAI":
        return tuple(settings.resolved_openai_api_keys)
    if provider_name == "ANTHROPIC":
        return tuple(settings.resolved_anthropic_api_keys)
    if provider_name in {"OLLAMA", "SIMULATOR", "MANUAL"}:
        return (None,)
    return ()


def _provider_models(
    settings: Settings,
    provider_name: str,
    tier: ModelTier,
) -> tuple[str, ...]:
    if provider_name == "GOOGLE":
        return (
            tuple(settings.google_lightweight_models)
            if tier is ModelTier.LIGHTWEIGHT
            else tuple(settings.resolved_google_standard_models)
        )
    if provider_name == "NVIDIA":
        return (
            tuple(settings.nvidia_lightweight_models)
            if tier is ModelTier.LIGHTWEIGHT
            else tuple(settings.resolved_nvidia_standard_models)
        )
    if provider_name == "OPENAI":
        return (
            tuple(settings.openai_lightweight_models)
            if tier is ModelTier.LIGHTWEIGHT
            else tuple(settings.resolved_openai_standard_models)
        )
    if provider_name == "ANTHROPIC":
        return (
            tuple(settings.anthropic_lightweight_models)
            if tier is ModelTier.LIGHTWEIGHT
            else tuple(settings.resolved_anthropic_standard_models)
        )
    if provider_name == "OLLAMA":
        return (
            tuple(settings.ollama_lightweight_models)
            if tier is ModelTier.LIGHTWEIGHT
            else tuple(settings.resolved_ollama_standard_models)
        )
    if provider_name == "SIMULATOR":
        return ("deterministic-eligibility-simulator-v1",) if tier is ModelTier.LIGHTWEIGHT else ()
    if provider_name == "MANUAL":
        return (ManualFileProvider.model,)
    return ()


def _validated_route_bindings(
    settings: Settings,
) -> dict[tuple[str, int, str], frozenset[str]]:
    bindings: dict[tuple[str, int, str], set[str]] = defaultdict(set)
    for raw in settings.ai_validated_route_bindings:
        provider_name, credential_index_text, model, task_key = raw.split("|", maxsplit=3)
        bindings[(provider_name, int(credential_index_text), model)].add(task_key)
    return {key: frozenset(value) for key, value in bindings.items()}


def build_routes(
    settings: Settings,
    *,
    interception_store: InterceptionStore | None = None,
    replay_store: ReplayStore | None = None,
) -> tuple[AIRoute, ...]:
    provider_order = tuple(p.strip() for p in settings.ai_provider_order.split(","))
    logger.debug("ai_route_build_started", extra={"provider_order": provider_order})
    validated_bindings = _validated_route_bindings(settings)
    routes: list[AIRoute] = []
    for provider_priority, provider_name in enumerate(provider_order):
        credentials = _provider_credentials(settings, provider_name)
        for tier in ModelTier:
            models = _provider_models(settings, provider_name, tier)
            logger.debug(
                "ai_route_build_provider_tier",
                extra={
                    "provider": provider_name,
                    "tier": tier.value,
                    "model_count": len(models),
                    "credential_count": len(credentials),
                },
            )
            for model_priority, model in enumerate(models):
                for credential_priority, credential in enumerate(credentials):
                    binding_key = (provider_name, credential_priority, model)
                    credential_id = (
                        f"{provider_name.lower()}-local"
                        if credential is None
                        else f"{provider_name.lower()}-key-{credential_priority + 1}"
                    )
                    adapter = _provider(
                        provider_name,
                        settings,
                        key=credential,
                        model=model,
                        interception_store=interception_store,
                    )
                    adapter = _with_replay(adapter, settings, replay_store)
                    route_id = f"{provider_name.lower()}/{model}/{credential_id}"
                    routes.append(
                        AIRoute(
                            route_id=route_id,
                            provider_name=provider_name,
                            model=model,
                            credential_id=credential_id,
                            credential_fingerprint=_fingerprint(credential),
                            tier=tier,
                            provider=adapter,
                            provider_priority=provider_priority,
                            model_priority=model_priority,
                            credential_priority=credential_priority,
                            allowed_task_keys=validated_bindings.get(binding_key, frozenset()),
                        )
                    )
    logger.debug("ai_route_build_completed", extra={"route_count": len(routes)})
    return tuple(routes)
