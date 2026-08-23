"""Call every configured route once, through the pool the platform routes on.

**This used to validate a path nothing routes through.** It built providers from
the *singular* settings -- `google_api_key`, `google_model` -- and a deployment
that configures the plural `google_api_keys` and `google_lightweight_models`
leaves those unset. `GeminiProvider` then raised `AUTH_FAILED` on a `None` key,
and the script reported a credential failure on a deployment whose credentials
were fine. It cost an afternoon of believing the keys were placeholders.

So it builds `build_routes(settings)` now -- the same pool `RoutePoolReasoningModelGateway`
dispatches on -- and reports per route. A model that is retired or unavailable
now shows as one failing route beside its working siblings, rather than as a
whole provider being broken.

Prints provider, model and route id. Never a key, a prompt, or a reply body.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from return_platform.ai.providers.contracts import ProviderRequest
from return_platform.ai.routing.routes import build_routes
from return_platform.configuration.settings import Settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

#: Per-route ceiling. A provider that hangs costs one wait, not the run.
_TIMEOUT_SECONDS = 60


async def validate_ai_gateway() -> int:
    settings = Settings()  # type: ignore[call-arg]
    routes = build_routes(settings)
    if not routes:
        logger.error(
            "No routes were constructed from PLATFORM_AI_PROVIDER_ORDER=%s. "
            "A provider needs both credentials and a model list to produce one.",
            settings.ai_provider_order,
        )
        return 2

    request = ProviderRequest(
        system_prompt="You are a helpful assistant.",
        user_payload={"message": "Reply with exactly the word SUCCESS."},
    )

    results: list[dict[str, object]] = []
    for route in routes:
        entry: dict[str, object] = {
            "routeId": route.route_id,
            "provider": route.provider_name,
            "model": route.model,
            "configured": route.provider.configured,
        }
        try:
            response = await asyncio.wait_for(
                route.provider.generate(request), timeout=_TIMEOUT_SECONDS
            )
            text = str(getattr(response, "text", "") or "")
            entry["outcome"] = "OK"
            # The length, not the body: a model reply is customer-adjacent even
            # when the prompt was ours.
            entry["replyLength"] = len(text)
            entry["saidSuccess"] = "SUCCESS" in text.upper()
        except TimeoutError:
            entry["outcome"] = f"TIMEOUT after {_TIMEOUT_SECONDS}s"
        except Exception as exc:  # noqa: BLE001 - the failure is the finding
            entry["outcome"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        results.append(entry)

    working = [r for r in results if r["outcome"] == "OK"]
    print(json.dumps(results, indent=2, default=str))
    print(f"\n{len(working)} of {len(results)} routes answered.")

    # One working route is what "a live model route exists" means. Reporting
    # failure because a retired model is still listed would be the same mistake
    # this script used to make in the other direction.
    return 0 if working else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(validate_ai_gateway()))
