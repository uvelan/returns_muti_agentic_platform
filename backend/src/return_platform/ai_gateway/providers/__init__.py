"""Deprecated: re-exports `return_platform.ai.providers`."""

from return_platform.ai.providers import (
    AIProvider,
    AnthropicProvider,
    GeminiProvider,
    ManualFileProvider,
    NvidiaProvider,
    OllamaProvider,
    OpenAIResponsesProvider,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    SimulatorProvider,
    build_providers,
)

__all__ = [
    "AIProvider",
    "AnthropicProvider",
    "GeminiProvider",
    "ManualFileProvider",
    "NvidiaProvider",
    "OllamaProvider",
    "OpenAIResponsesProvider",
    "ProviderError",
    "ProviderRequest",
    "ProviderResponse",
    "SimulatorProvider",
    "build_providers",
]
