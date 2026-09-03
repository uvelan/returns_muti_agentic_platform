"""A retired model must be reported as MODEL_UNAVAILABLE, not RESPONSE_INVALID.

`raise_for_provider_status` had no 410 branch, so NVIDIA's end-of-life response
fell through to `raise_for_status()` and became RESPONSE_INVALID. That is the
wrong diagnosis twice over: it reads as "the model sent us garbage", and it is
not a route-level unavailability, so the router never opened the model circuit
and kept retrying a model that no longer exists. Four models went end-of-life in
a single day on 2026-09-03, so the mapping is asserted here on the body NVIDIA
actually sends.
"""

from __future__ import annotations

import httpx
import pytest

from return_platform.ai.providers.contracts import ProviderError
from return_platform.ai.providers.http import raise_for_provider_status

END_OF_LIFE_BODY = {
    "type": "about:blank",
    "title": "Gone",
    "status": 410,
    "detail": (
        "The model 'openai/gpt-oss-120b' has reached its end of life on "
        "2026-09-03T08:00:00Z and is no longer available."
    ),
}


def test_a_retired_model_opens_the_model_circuit_rather_than_looking_malformed() -> None:
    response = httpx.Response(
        410,
        json=END_OF_LIFE_BODY,
        request=httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions"),
    )
    with pytest.raises(ProviderError) as raised:
        raise_for_provider_status(response)
    assert raised.value.code == "MODEL_UNAVAILABLE"


def test_an_unknown_model_still_maps_to_the_same_code() -> None:
    response = httpx.Response(
        404,
        request=httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions"),
    )
    with pytest.raises(ProviderError) as raised:
        raise_for_provider_status(response)
    assert raised.value.code == "MODEL_UNAVAILABLE"


def test_an_ordinary_bad_request_is_still_a_malformed_exchange() -> None:
    response = httpx.Response(
        400,
        request=httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions"),
    )
    with pytest.raises(ProviderError) as raised:
        raise_for_provider_status(response)
    assert raised.value.code == "RESPONSE_INVALID"
