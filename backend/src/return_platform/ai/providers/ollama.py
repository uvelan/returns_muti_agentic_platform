"""Local Ollama OpenAI-compatible adapter."""

from return_platform.ai.providers.openai_compatible import OpenAICompatibleProvider
from return_platform.configuration.settings import Settings


class OllamaProvider(OpenAICompatibleProvider):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            name="OLLAMA",
            api_key=None,
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout_seconds=settings.ai_timeout_seconds,
        )
