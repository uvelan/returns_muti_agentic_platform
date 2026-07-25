import asyncio
import logging

import httpx

from return_platform.ai_gateway.providers import ProviderRequest, build_providers
from return_platform.configuration.settings import Settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def validate_ai_gateway():
    logger.info("Starting live AI Gateway validation...")
    settings = Settings()  # type: ignore[call-arg]

    has_google = bool(settings.google_api_key)
    has_nvidia = bool(settings.nvidia_api_key)
    logger.info(f"Keys provided: Google={has_google}, NVIDIA={has_nvidia}")

    providers = build_providers(settings)

    async with httpx.AsyncClient(timeout=30.0):
        request = ProviderRequest(
            system_prompt="You are a helpful assistant.",
            user_payload={"message": "Hello. Please respond with exactly the word SUCCESS."},
        )
        for name, provider in providers.items():
            if name in {"SIMULATOR", "OPENAI", "ANTHROPIC", "OLLAMA"}:
                continue
            try:
                logger.info(f"Executing evaluation against {name}...")
                response = await provider.generate(request)
                logger.info(f"Evaluation succeeded. Provider used: {name}")
                logger.info(f"Raw Response: {response.text}")
                if "SUCCESS" not in response.text:
                    logger.warning(
                        "Response did not contain expected word SUCCESS. Response: %s",
                        response.text,
                    )
            except Exception as e:
                logger.error(f"Live validation failed for {name}: {e}")
                raise


if __name__ == "__main__":
    asyncio.run(validate_ai_gateway())
