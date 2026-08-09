"""Phase 15's "APIs return references only", enforced on the way out.

Configuration documents legitimately carry secret *references* --
`vault://secret/production/ai/google/credentials/key-0#api_key` -- and the
canonical API is expected to return those: an operator needs to see which secret
a source binds to. What must never leave is a resolved value.

**Why a response-side scrub rather than trusting the domain.** The domain models
already refuse to hold resolved secrets, and `settings.py` uses `SecretStr`. That
is the primary defence and this does not replace it. But the canonical API serves
documents assembled from several sources -- a release's per-domain payloads, an
active snapshot built from defaults, integration bindings -- and "no path through
any of those ever carries a value" is a claim about every current *and future*
contributor to the payload. Scrubbing at the boundary makes it a property of the
response instead, which is the thing actually being promised.

The scrub is deliberately conservative about what it treats as secret-shaped:
a false positive costs an operator a masked field they can still see the
reference for, a false negative leaks a credential.
"""

from __future__ import annotations

import re
from typing import Any

#: What a legitimate reference looks like. Matching one means the value is a
#: pointer, not a secret, and passes through untouched.
_REFERENCE = re.compile(r"^vault://", re.IGNORECASE)

#: Key names whose *value* is a secret when it is not a reference. Substring
#: matched and case-insensitive: `apiKey`, `api_key`, `googleApiKey` and
#: `PASSWORD` must all be caught, and enumerating exact spellings across four
#: naming conventions is how one gets missed.
_SECRET_KEY_FRAGMENTS = (
    "password",
    "secret",
    "token",
    "credential",
    "api_key",
    "apikey",
    "private_key",
    "privatekey",
    "passphrase",
    "connection_string",
    "connectionstring",
    "dsn",
)

MASKED = "***REDACTED***"


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS)


def redact_secret_values(payload: Any) -> Any:
    """Return `payload` with resolved secret values replaced by a marker.

    References are preserved: an operator must be able to see *which* secret a
    binding points at, and the reference is not itself sensitive. Structure,
    key names and every non-secret value are unchanged, so this is safe to apply
    to a whole response rather than to hand-picked fields -- which is the point,
    since hand-picking is what eventually misses one.
    """
    if isinstance(payload, dict):
        return {
            key: (
                MASKED
                if _is_secret_key(str(key)) and _is_resolved_secret(value)
                else redact_secret_values(value)
            )
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [redact_secret_values(item) for item in payload]
    if isinstance(payload, tuple):
        return tuple(redact_secret_values(item) for item in payload)
    return payload


def _is_resolved_secret(value: Any) -> bool:
    """A non-empty string that is not a reference.

    Non-strings are left alone: a `null`, a boolean `passwordRequired`, or a
    nested object under a secret-ish key are structure rather than credential,
    and masking them would destroy information without protecting anything.
    """
    if not isinstance(value, str) or not value:
        return False
    return _REFERENCE.match(value) is None
