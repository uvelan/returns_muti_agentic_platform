"""Where request validation resolves runtime configuration from (CFG-03).

Two fields of `ReturnCreateRequest` are owned by the operator, not by the
contract, and both were pinned in Python:

* `shippingPathExpectation` carried a fourteen-value `pattern=` while
  `return_policy.normalized_return_methods` already declared the vocabulary in
  `config/returns/production.yaml`. An operator adding a return method through
  the Control Centre got a release, an activated snapshot -- and a 422 from the
  API, because the regex could not have heard of it.
* `assumptionSetVersion` defaulted to the literal `FERGUSON-RETURN-ASSUMPTIONS-1.0`
  while `configuration.assumption_set_version` is the value the whole platform
  actually runs on. A caller omitting it stamped the session with a version the
  process might not be serving, and that string is persisted.

**The decision this module is: request validation resolves configuration from
the active snapshot on `app.state`, in the handler, after model validation.**

The alternatives and why not:

* A pydantic `pattern=` or a plain `field_validator` is evaluated with no
  request and no application state. Nothing it can read is runtime-owned, which
  is the whole defect.
* Pydantic's validation *context* would reach a validator, but FastAPI passes no
  context when it validates a request body, so there is no seam to put one in
  without replacing body parsing.
* `OperationalRepository.create_return` is the single writer and was tempting
  for that reason. It takes `Settings`, not the return configuration, and giving
  a data-access class a configuration dependency to serve a validation concern
  widens it for the wrong reason -- and moves a 422 to after a datastore has been
  resolved, so an unconfigured value would read as an outage while Mongo is down.

`app.state.return_configuration` is the right source specifically because
`main.py`'s correlation middleware calls `RuntimeConfigurationActivator.refresh()`
before every handler: the snapshot a handler reads is the one the process is
serving at that moment, not one captured at import or at startup. That is what
makes "activate a release, add a method, submit it" work without a restart.

Both routes call `apply_active_return_policy`, and
`tests/api/test_return_creation_resolves_runtime_policy.py` asserts they both do
-- one of them silently keeping the old behaviour is the realistic regression.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.operations.models import ReturnCreateRequest

__all__ = ["apply_active_return_policy"]


def apply_active_return_policy(
    request: Request, payload: ReturnCreateRequest
) -> ReturnCreateRequest:
    """Check the payload against the running configuration and stamp it with it.

    Returns a payload whose `assumptionSetVersion` is the one this process is
    actually serving. Raises 422 for a return method the operator has not
    configured, and 503 when no snapshot is loaded -- a process with no
    configuration cannot say whether a method is valid, and answering 422 there
    would tell a caller their perfectly good request was malformed.
    """
    loaded = getattr(request.app.state, "return_configuration", None)
    if not isinstance(loaded, LoadedReturnConfiguration):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "RETURN_CONFIGURATION_UNAVAILABLE",
                "message": (
                    "No return configuration is loaded, so a return method cannot be validated."
                ),
            },
        )
    policy = loaded.configuration.return_policy
    if payload.shippingPathExpectation not in policy.normalized_return_methods:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "UNCONFIGURED_RETURN_METHOD",
                "message": (
                    f"shippingPathExpectation must be one of "
                    f"{sorted(policy.normalized_return_methods)}"
                ),
                "retryable": False,
            },
        )
    if payload.assumptionSetVersion is not None:
        return payload
    # Stamped rather than defaulted. The assumption set is a property of the
    # release the platform is running, and a caller who does not name one is
    # saying "whatever is in force", not "the version that was in force when
    # this line of Python was written".
    return payload.model_copy(
        update={"assumptionSetVersion": loaded.configuration.assumption_set_version}
    )
