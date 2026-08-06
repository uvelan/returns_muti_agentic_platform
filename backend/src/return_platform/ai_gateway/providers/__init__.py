"""AI provider adapters, one provider per module."""

from return_platform.ai_gateway.providers.anthropic import AnthropicProvider
from return_platform.ai_gateway.providers.contracts import (
    AIProvider,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
from return_platform.ai_gateway.providers.factory import build_providers
from return_platform.ai_gateway.providers.google import GeminiProvider
from return_platform.ai_gateway.providers.manual import ManualFileProvider
from return_platform.ai_gateway.providers.nvidia import NvidiaProvider
from return_platform.ai_gateway.providers.ollama import OllamaProvider
from return_platform.ai_gateway.providers.openai import OpenAIResponsesProvider
from return_platform.ai_gateway.providers.simulator import SimulatorProvider

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
