"""The canonical `/api/config` surface, and the rule it has to enforce.

Phase 15: "Secrets are stored in Vault and APIs return references only." A
configuration document legitimately carries references -- an operator must be
able to see *which* secret a source binds to -- and must never carry a resolved
value.

The scrub runs on the whole response rather than on hand-picked fields, and that
is the point: hand-picking is what eventually misses one. These tests pin the
distinction between a reference (passes through) and a value (masked), because
getting it backwards would either leak credentials or blind every operator
screen.
"""

from __future__ import annotations

from typing import Any

from return_platform.configuration.api.secrets import MASKED, redact_secret_values

REFERENCE = "vault://secret/production/ai/google/credentials/key-0#api_key"


# --- references survive ------------------------------------------------------


def test_a_vault_reference_is_not_masked() -> None:
    """Masking references would make every binding screen useless while
    protecting nothing -- the reference is a pointer, not a credential."""
    payload = {"apiKey": REFERENCE}
    assert redact_secret_values(payload) == {"apiKey": REFERENCE}


def test_a_resolved_value_under_a_secret_key_is_masked() -> None:
    assert redact_secret_values({"apiKey": "AIzaSyREAL-KEY"}) == {"apiKey": MASKED}


def test_masking_is_case_and_convention_insensitive() -> None:
    """`apiKey`, `api_key`, `API_KEY` and `googleApiKey` all have to be caught.
    Enumerating exact spellings across four naming conventions is how one gets
    missed."""
    payload = {
        "apiKey": "a",
        "api_key": "b",
        "API_KEY": "c",
        "googleApiKey": "d",
        "password": "e",
        "connectionString": "f",
        "privateKey": "g",
        "refreshToken": "h",
    }
    assert set(redact_secret_values(payload).values()) == {MASKED}


def test_non_secret_fields_are_untouched() -> None:
    payload = {"provider": "GOOGLE", "model": "gemini-2.5-pro", "enabled": True, "retries": 3}
    assert redact_secret_values(payload) == payload


# --- shape is preserved ------------------------------------------------------


def test_nested_structures_are_scrubbed_all_the_way_down() -> None:
    """A release document nests domain payloads several levels deep; a scrub
    that only looked at the top level would be decorative."""
    payload: dict[str, Any] = {
        "releaseId": "r-1",
        "domains": {
            "ai_gateway": {
                "providers": [
                    {"name": "GOOGLE", "credentials": "SECRET-VALUE"},
                    {"name": "NVIDIA", "credentials": REFERENCE},
                ]
            }
        },
    }

    scrubbed = redact_secret_values(payload)

    providers = scrubbed["domains"]["ai_gateway"]["providers"]
    assert providers[0]["credentials"] == MASKED
    assert providers[1]["credentials"] == REFERENCE
    # Structure and non-secret values survive intact.
    assert providers[0]["name"] == "GOOGLE"
    assert scrubbed["releaseId"] == "r-1"


def test_non_string_values_under_secret_keys_are_left_alone() -> None:
    """A null, a boolean flag, or a nested object under a secret-ish key is
    structure rather than credential. Masking those would destroy information
    without protecting anything."""
    payload = {
        "password": None,
        "secretRequired": True,
        "credentials": {"reference": REFERENCE, "rotationDays": 30},
    }

    scrubbed = redact_secret_values(payload)

    assert scrubbed["password"] is None
    assert scrubbed["secretRequired"] is True
    assert scrubbed["credentials"] == {"reference": REFERENCE, "rotationDays": 30}


def test_lists_of_scalars_survive() -> None:
    payload = {"allowedProviders": ["GOOGLE", "NVIDIA"], "tokenBudgets": [1024, 2048]}
    assert redact_secret_values(payload) == payload


def test_an_empty_string_is_not_treated_as_a_secret() -> None:
    """Masking an empty value would tell an operator a credential is configured
    when none is -- worse than showing the empty field."""
    assert redact_secret_values({"apiKey": ""}) == {"apiKey": ""}


# --- the route surface -------------------------------------------------------


def test_the_canonical_router_is_versionless_and_read_only() -> None:
    """Versionless matches `/api/graph-schema`: a release, not a URL, pins
    configuration. Read-only is deliberate -- see the note in router.py about
    the two competing release lifecycles."""
    from return_platform.configuration.api.router import router

    assert router.prefix == "/api/config"
    methods = {method for route in router.routes for method in getattr(route, "methods", set())}
    assert methods <= {"GET", "HEAD"}, f"canonical config API is read-only for now, found {methods}"


def test_every_canonical_response_goes_through_the_scrub() -> None:
    """The handlers build responses through one helper precisely so a new
    endpoint cannot forget the scrub. If someone adds a handler that constructs
    `APIResponse` directly, this catches it."""
    import ast
    from pathlib import Path

    source = Path(
        Path(__file__).resolve().parents[2]
        / "src"
        / "return_platform"
        / "configuration"
        / "api"
        / "router.py"
    ).read_text(encoding="utf-8")

    direct = [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "APIResponse"
    ]

    # Exactly one: the `_ok` helper itself.
    assert len(direct) == 1, (
        "a handler constructs APIResponse directly instead of going through _ok(), "
        f"bypassing the secret scrub (lines {direct})"
    )
