"""NVIDIA NIM hosted API adapter."""

from return_platform.ai_gateway.providers.http import secret_value
from return_platform.ai_gateway.providers.openai_compatible import OpenAICompatibleProvider
from return_platform.configuration.settings import Settings


class NvidiaProvider(OpenAICompatibleProvider):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            name="NVIDIA",
            api_key=secret_value(settings.nvidia_api_key),
            base_url=settings.nvidia_base_url,
            model=settings.nvidia_model,
            timeout_seconds=settings.ai_timeout_seconds,
        )
