"""AI provider adapters, one provider per module."""

from return_platform.ai.providers.anthropic import AnthropicProvider
from return_platform.ai.providers.contracts import (
    AIProvider,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
from return_platform.ai.providers.google import GeminiProvider
from return_platform.ai.providers.manual import ManualFileProvider
from return_platform.ai.providers.nvidia import NvidiaProvider
from return_platform.ai.providers.ollama import OllamaProvider
from return_platform.ai.providers.openai import OpenAIResponsesProvider
from return_platform.ai.providers.registry import build_providers
from return_platform.ai.providers.simulator import SimulatorProvider

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
