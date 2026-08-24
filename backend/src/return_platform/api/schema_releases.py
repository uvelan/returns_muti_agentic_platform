"""Published graph-schema releases, and the migration between two of them.

The analyzer can publish a release and the store can make one active. Between
those two acts sat nothing: activation was a pointer flip, and the operator
performing it had no way to ask what it would do to the graph they were already
serving answers from.

This is that surface. `GET .../migration-plan` computes the plan against
whatever is active right now and returns it without writing anything, so
reviewing a change needs read rights and leaves no trace. `POST .../activate`
records the same plan and then flips, so the understanding a decision was made
under outlives the decision.

Deliberately not under `/api/config/releases`, which is the *configuration*
domain's own release surface for a different artifact. Two things called a
release is confusing enough without one router serving both.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, ValidationError

from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.fingerprint import sha256_digest
from return_platform.dynamic_knowledge.release_migration import MigrationPlan
from return_platform.dynamic_knowledge.release_store import (
    ReleaseAlreadyPublished,
    SchemaReleaseStore,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.security.authorization import require_admin_roles, require_read_roles
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/schema-releases", tags=["Graph Schema Releases"])


class ReleaseRowView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configurationReleaseId: str
    configurationChecksum: str | None = None
    publishedBy: str | None = None
    publishedAt: str | None = None
    # Whether this is the one the runtime reads. The list is the only place an
    # operator can see published and live side by side, and the distinction is
    # the whole reason publishing does not activate.
    active: bool


class ReleaseListView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    releases: list[ReleaseRowView]
    activeReleaseId: str | None


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


def _store(request: Request) -> SchemaReleaseStore:
    resources = getattr(request.app.state, "resources", None)
    settings = getattr(request.app.state, "settings", None)
    mongo = getattr(resources, "mongo", None)
    if mongo is None or settings is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "RELEASE_STORE_UNAVAILABLE",
                "message": "Platform MongoDB is unavailable, so releases cannot be read.",
            },
        )
    return SchemaReleaseStore(mongo, settings.mongo_database)


def _iso(value: Any) -> str | None:
    return None if value is None else str(value)


class SchemaDocumentView(BaseModel):
    """The active schema as an editable document, plus what it is.

    The document is the schema exactly as the runtime resolved it, so an
    operator editing it is editing the thing that is running rather than a
    rendering of it.
    """

    model_config = ConfigDict(extra="forbid")

    configurationReleaseId: str
    configurationChecksum: str
    schemaVersion: str
    #: The whole schema. Large, and deliberately not paginated or summarised:
    #: an editor that showed a subset would silently drop whatever it did not
    #: render when the document was written back.
    document: dict[str, Any]
    #: True when this came from the file rather than from a published release,
    #: which is the state an installation is in before anything is seeded.
    fromFile: bool


class SchemaDocumentEdit(BaseModel):
    """An edited schema, on its way to becoming a release."""

    model_config = ConfigDict(extra="forbid")

    document: dict[str, Any]
    #: Recomputed server-side, never taken from the client. The checksum exists
    #: to prove the content was not tampered with in transit or at rest, and a
    #: client-supplied one proves nothing at all.
    #:
    #: The *submitted* value is still required, and is compared against the
    #: release the edit started from: two operators editing the schema at once
    #: would otherwise have the second silently discard the first's work.
    baseChecksum: str
    activate: bool = True


@router.get("/active/document", response_model=APIResponse[SchemaDocumentView])
async def get_active_document(
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[SchemaDocumentView]:
    """The schema the runtime is actually running, as an editable document."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:  # pragma: no cover - the app always carries settings
        raise HTTPException(status_code=503, detail="Configuration is unavailable")
    store = _store(request)
    published = await store.active()
    schema = (
        published
        if published is not None
        else load_active_schema(settings.dynamic_knowledge_schema_path)
    )
    document = schema.model_dump(mode="json")
    return APIResponse(
        data=SchemaDocumentView(
            configurationReleaseId=schema.configuration_release_id,
            configurationChecksum=schema.configuration_checksum,
            schemaVersion=schema.schema_version,
            document=document,
            fromFile=published is None,
        ),
        meta=_meta(request),
    )


@router.put("/active/document", response_model=APIResponse[SchemaDocumentView])
async def put_active_document(
    payload: SchemaDocumentEdit,
    request: Request,
    actor_id: str = Depends(require_admin_roles),
) -> APIResponse[SchemaDocumentView]:
    """Publish an edited schema as a new release, and point the runtime at it.

    Editing the schema used to mean editing a YAML file on the server and
    restarting, which is not something an operator can do and not something
    that leaves a record of who changed what.

    Three things happen here that a hand edit does not get:

      * **The checksum is recomputed.** `load_active_schema` refuses a document
        whose checksum does not match its content, so a hand edit that forgot
        to reseal it produced a platform that would not start.
      * **The edit is validated as a schema** before it is published, so a
        malformed document is a 422 rather than a release nothing can load.
      * **Concurrent edits are caught.** `baseChecksum` names the release the
        edit started from; if that is no longer active, someone else published
        in between and this would silently discard their work.
    """
    settings = getattr(request.app.state, "settings", None)
    if settings is None:  # pragma: no cover - the app always carries settings
        raise HTTPException(status_code=503, detail="Configuration is unavailable")
    store = _store(request)

    current = await store.active()
    current_checksum = (
        current.configuration_checksum
        if current is not None
        else load_active_schema(settings.dynamic_knowledge_schema_path).configuration_checksum
    )
    if payload.baseChecksum != current_checksum:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "SCHEMA_CHANGED_UNDER_EDIT",
                "message": (
                    "The schema changed since this edit began. Reload the document "
                    "and reapply the change."
                ),
                "activeChecksum": current_checksum,
            },
        )

    submitted = dict(payload.document)
    # Derived, not accepted: see `SchemaDocumentEdit.baseChecksum`.
    submitted.pop("configuration_checksum", None)
    # The id is part of what gets digested, so it has to be settled before the
    # checksum is taken. Derived from the submitted content so the same edit
    # always lands on the same release rather than a new one per attempt.
    release_id = f"edit-{sha256_digest(submitted)[:12]}"
    submitted["configuration_release_id"] = release_id

    try:
        # Validated with a placeholder, because the checksum cannot be computed
        # until the document has been through the model: pydantic fills
        # defaults and normalises, and digesting the raw submission would
        # produce a checksum that does not match the document actually stored.
        # `load_active_schema` compares those two, so the mismatch would not be
        # a cosmetic difference -- it would be a release that refuses to load.
        edited = ActiveSchema.model_validate(submitted | {"configuration_checksum": "0" * 64})
    except ValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "SCHEMA_INVALID",
                "message": "The edited schema is not a valid schema.",
                "errors": error.errors(include_url=False)[:20],
            },
        ) from error

    # The digest is taken over the document the *model* produces, not over the
    # one that arrived. Parsing fills defaults and normalises, so the submitted
    # form and the stored form are not the same document -- and
    # `load_active_schema` checks the checksum against the stored one.
    #
    # This is only safe because `model_dump` is deterministic: the set-typed
    # fields serialize sorted (see `SortedStrings`). They used to dump in
    # set-iteration order, and a checksum taken over one dump did not match the
    # next, which made a published release fail to load depending on which
    # order its sets happened to come out in.
    canonical = edited.model_dump(mode="json")
    canonical.pop("configuration_checksum", None)
    checksum = sha256_digest(canonical)

    # Compared as canonical content rather than as checksums. The active
    # release's checksum was computed over whatever form it was published in,
    # and comparing that to a freshly canonicalised one would report every
    # document as changed.
    active_schema = (
        current
        if current is not None
        else load_active_schema(settings.dynamic_knowledge_schema_path)
    )
    # Compared as parsed documents rather than as checksums: the active
    # release's checksum was computed over whatever form it was published in,
    # and a stored release predating deterministic set serialization would read
    # as changed on every comparison.
    active_canonical = active_schema.model_dump(mode="json")
    active_canonical.pop("configuration_checksum", None)
    active_canonical.pop("configuration_release_id", None)
    compared = dict(canonical)
    compared.pop("configuration_release_id", None)
    if compared == active_canonical:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "SCHEMA_UNCHANGED",
                "message": "The submitted schema is identical to the active one.",
            },
        )

    document = canonical | {"configuration_checksum": checksum}
    # Re-validated so what is published is the document that was digested,
    # rather than a model whose checksum field still holds the placeholder.
    edited = ActiveSchema.model_validate(document)

    try:
        await store.publish(edited, published_by=actor_id)
    except ReleaseAlreadyPublished:
        # This exact content was published before. Activating it below is still
        # what the operator asked for.
        pass
    if payload.activate:
        await store.activate(release_id)

    return APIResponse(
        data=SchemaDocumentView(
            configurationReleaseId=release_id,
            configurationChecksum=checksum,
            schemaVersion=edited.schema_version,
            # The document that was digested, handed back verbatim rather than
            # re-dumped. A re-dump is another chance for set ordering to move,
            # and this value is what a client will checksum.
            document=document,
            fromFile=False,
        ),
        meta=_meta(request),
    )


@router.get("", response_model=APIResponse[ReleaseListView])
async def list_releases(
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[ReleaseListView]:
    """Every published release, newest first, and which one is live."""
    store = _store(request)
    active = await store.active()
    active_id = None if active is None else active.configuration_release_id
    rows = [
        ReleaseRowView(
            configurationReleaseId=str(row.get("configurationReleaseId", "")),
            configurationChecksum=_iso(row.get("configurationChecksum")),
            publishedBy=_iso(row.get("publishedBy")),
            publishedAt=_iso(row.get("publishedAt")),
            active=row.get("configurationReleaseId") == active_id,
        )
        for row in await store.list_published()
    ]
    return APIResponse(
        data=ReleaseListView(releases=rows, activeReleaseId=active_id), meta=_meta(request)
    )


@router.get("/{release_id}/migration-plan", response_model=APIResponse[MigrationPlan])
async def get_migration_plan(
    release_id: str,
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[MigrationPlan]:
    """What activating this release would do, computed against what is live now.

    A preview, recomputed on every call rather than served from the recorded
    plan: the active pointer moves, and a plan for a pair that is no longer the
    pair you are in is a confidently wrong answer.
    """
    try:
        plan = await _store(request).preview_activation(release_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "UNKNOWN_RELEASE", "message": str(exc)},
        ) from exc
    return APIResponse(data=plan, meta=_meta(request))


@router.post("/{release_id}/activate", response_model=APIResponse[MigrationPlan])
async def activate_release(
    release_id: str,
    request: Request,
    # Admin, matching source bindings: this decides which schema every agent
    # turn reasons over, and it is not an act an operator with rights over one
    # return should be able to make.
    _actor_id: str = Depends(require_admin_roles),
) -> APIResponse[MigrationPlan]:
    """Make a release live, and record the migration it commits the graph to.

    Returns the plan rather than an acknowledgement. Whether a rebuild is now
    owed is the consequence of the act, and an operator who has to go and ask
    somewhere else will not.
    """
    try:
        plan = await _store(request).activate(release_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "UNKNOWN_RELEASE", "message": str(exc)},
        ) from exc
    return APIResponse(data=plan, meta=_meta(request))
