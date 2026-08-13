"""Where each dataset the schema names is read from, and how to change it.

The other half of the shape/binding split. The graph's shape is edited in the
analyzer and approved by a human; a binding is an infrastructure fact, changes
when infrastructure changes, and until now could only be changed by editing the
schema file and redeploying -- so pointing an entity at a restored collection or
a migrated database was a schema change with an approval attached.

Reads show the resolved catalogue, not just the overrides. An operator asking
"where does `salesInv` come from" wants the answer, and being handed an empty
list because nobody has overridden anything yet is not one.

Writes are overrides, and only overrides: a binding that agrees with
configuration is not stored, so this collection answers "what has someone
deliberately changed" rather than shadowing the file. `DELETE` returns a
dataset to whatever configuration says.

Changing a binding does not change what a *release* is running. The active
release captured its sources when it was compiled, and a rebinding reaches the
graph the next time a schema is published -- which is deliberate: an
infrastructure edit must not silently re-point a release someone approved.

Direct source reads are the other case, and they follow a rebinding on the next
request. `OperationalRepository` resolves this same catalogue to find the four
upstream datasets it seeds and reads; nothing there was ever compiled, so there
is no approved artifact to protect, and the alternative is a repository that
keeps reading a collection an operator has already told the platform to stop
using. The two halves therefore differ on purpose, and can differ in practice
between a rebinding and the next publish.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.schema import SourceAssetDefinition
from return_platform.dynamic_knowledge.source_binding_store import SourceBindingStore
from return_platform.dynamic_knowledge.source_bindings import SourceBinding, catalogue_from
from return_platform.security.authorization import require_admin_roles, require_read_roles
from return_platform.security.principal import Principal
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/source-bindings", tags=["Source Bindings"])


class SourceBindingView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: str
    sourceAssetId: str
    connectorType: str
    connectionRef: str
    objectRef: dict[str, str]
    incrementalCursorField: str | None = None
    # Whether someone changed this, or it is what the configured schema says.
    # The distinction is the first thing anyone debugging a sync asks.
    overridden: bool


class RebindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceAssetId: str
    connectorType: str
    connectionRef: str
    objectRef: dict[str, str]
    incrementalCursorField: str | None = None


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


def _store(request: Request) -> SourceBindingStore:
    resources = getattr(request.app.state, "resources", None)
    settings = getattr(request.app.state, "settings", None)
    mongo = getattr(resources, "mongo", None)
    if mongo is None or settings is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "BINDING_STORE_UNAVAILABLE",
                "message": "Platform MongoDB is unavailable, so bindings cannot be read.",
            },
        )
    return SourceBindingStore(mongo, settings.mongo_database)


def _baseline_path(request: Request) -> Any:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:  # pragma: no cover - the app always carries settings
        raise HTTPException(status_code=503, detail="Configuration is unavailable")
    return settings.dynamic_knowledge_schema_path


def _view(binding: SourceBinding, *, overridden: bool) -> SourceBindingView:
    asset = binding.asset
    return SourceBindingView(
        dataset=binding.dataset,
        sourceAssetId=asset.source_asset_id,
        connectorType=asset.connector_type.value,
        connectionRef=asset.connection_ref,
        objectRef=dict(asset.object_ref),
        incrementalCursorField=asset.incremental_cursor_field,
        overridden=overridden,
    )


@router.get("", response_model=APIResponse[list[SourceBindingView]])
async def list_bindings(
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[SourceBindingView]]:
    """Every dataset the platform can resolve, and where each one points."""
    store = _store(request)
    overrides = await store.list()
    overridden = {binding.dataset for binding in overrides}
    catalogue = catalogue_from(load_active_schema(_baseline_path(request)), overrides)
    return APIResponse(
        data=[
            _view(binding, overridden=binding.dataset in overridden) for binding in catalogue.all()
        ],
        meta=_meta(request),
    )


@router.put("/{dataset}", response_model=APIResponse[SourceBindingView])
async def rebind(
    dataset: str,
    payload: RebindRequest,
    request: Request,
    # Admin, not general write. Repointing a dataset decides where the platform
    # reads production data from, and it is not an act an operator with rights
    # over one return should be able to make.
    _actor_id: str = Depends(require_admin_roles),
) -> APIResponse[SourceBindingView]:
    """Point a dataset somewhere else.

    Validated as a real `SourceAssetDefinition` before it is stored, so a
    malformed connection is refused here rather than at the next publish --
    where it would surface as a compilation failure that reads like a schema
    problem.
    """
    principal = cast(Principal, request.state.principal)
    try:
        asset = SourceAssetDefinition.model_validate(
            {
                "source_asset_id": payload.sourceAssetId,
                "connector_type": payload.connectorType,
                "connection_ref": payload.connectionRef,
                "object_ref": payload.objectRef,
                "incremental_cursor_field": payload.incrementalCursorField,
            }
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_SOURCE_ASSET", "message": str(exc)},
        ) from exc

    binding = SourceBinding(dataset=dataset, asset=asset)
    await _store(request).rebind(binding, changed_by=principal.subject)
    return APIResponse(data=_view(binding, overridden=True), meta=_meta(request))


@router.delete("/{dataset}", response_model=APIResponse[dict[str, bool]])
async def clear_override(
    dataset: str,
    request: Request,
    _actor_id: str = Depends(require_admin_roles),
) -> APIResponse[dict[str, bool]]:
    """Return a dataset to whatever the configured schema says."""
    removed = await _store(request).clear(dataset)
    return APIResponse(data={"removed": removed}, meta=_meta(request))
