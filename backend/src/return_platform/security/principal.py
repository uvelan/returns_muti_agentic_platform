import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

from fastapi import Request

from return_platform.configuration.settings import Settings

_ALLOWED_DEVELOPMENT_ENVIRONMENTS = frozenset(
    {
        "development",
        "test",
    }
)


class AuthorizationError(Exception):
    """Raised when principal resolution or authorization is rejected."""


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    roles: frozenset[str]

    def __post_init__(self) -> None:
        normalized_subject = self.subject.strip()

        if not normalized_subject:
            raise ValueError("Principal subject must not be blank.")

        if not self.roles:
            raise ValueError("Principal must contain at least one role.")

        if any(not role.strip() for role in self.roles):
            raise ValueError("Principal roles must not contain blank values.")

        object.__setattr__(self, "subject", normalized_subject)
        object.__setattr__(
            self,
            "roles",
            frozenset(role.strip() for role in self.roles),
        )


type PrincipalProvider = Callable[[Request], Awaitable[Principal]]


def validate_correlation_id(value: str | None) -> str:
    """
    Return a normalized UUID correlation ID.

    Invalid, oversized, or missing values are replaced with a new UUID.
    """
    if value is not None and len(value) <= 64:
        try:
            return str(uuid.UUID(value))
        except ValueError:
            pass

    return str(uuid.uuid4())


async def get_development_principal(request: Request) -> Principal:
    """
    Resolve the development-only console principal.

    This provider must never be used in staging or production.
    """
    settings = cast(Settings, request.app.state.settings)

    if settings.environment not in _ALLOWED_DEVELOPMENT_ENVIRONMENTS:
        raise AuthorizationError("Development principal provider is disabled in this environment.")

    return Principal(
        subject="dev-operator",
        roles=frozenset({"console_admin"}),
    )
