"""Provider registry construction."""

from return_platform.ai_gateway.providers.anthropic import AnthropicProvider
from return_platform.ai_gateway.providers.contracts import AIProvider
from return_platform.ai_gateway.providers.google import GeminiProvider
from return_platform.ai_gateway.providers.nvidia import NvidiaProvider
from return_platform.ai_gateway.providers.ollama import OllamaProvider
from return_platform.ai_gateway.providers.openai import OpenAIResponsesProvider
from return_platform.ai_gateway.providers.simulator import SimulatorProvider
from return_platform.configuration.settings import Settings


def build_providers(settings: Settings) -> dict[str, AIProvider]:
    providers: tuple[AIProvider, ...] = (
        GeminiProvider(settings),
        NvidiaProvider(settings),
        OpenAIResponsesProvider(settings),
        AnthropicProvider(settings),
        OllamaProvider(settings),
        SimulatorProvider(settings),
    )
    return {provider.name: provider for provider in providers}
