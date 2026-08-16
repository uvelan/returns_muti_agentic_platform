"""MongoDB-backed operational repositories with optimistic concurrency."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Final, cast

from fastapi import HTTPException, Request
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, ReplaceOne, ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import DuplicateKeyError, OperationFailure

from return_platform.ai.gateway.models import AIUsageAttemptView, AIUsageSummaryView
from return_platform.ai.pricing import AIPricingStatus
from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.config_loader import resolve_active_schema
from return_platform.dynamic_knowledge.release_store import SchemaReleaseStore
from return_platform.dynamic_knowledge.source_binding_store import SourceBindingStore
from return_platform.dynamic_knowledge.source_bindings import (
    SourceBindingCatalogue,
    catalogue_from,
)
from return_platform.operations.case_repository import CaseRepository

# Re-exported deliberately, under the redundant-alias form `no_implicit_reexport`
# requires. `ConcurrencyConflictError` moved to `operations.errors` so that this
# module and the aggregate mixins it composes can raise one class without
# importing one another; nine modules and the concurrency tests import it from
# here, and that path is kept rather than rewritten.
from return_platform.operations.errors import (
    ConcurrencyConflictError as ConcurrencyConflictError,
)
from return_platform.operations.integrations.outbox import (
    INTEGRATION_OUTBOX_COLLECTION,
    ensure_integration_outbox_indexes,
)
from return_platform.operations.models import (
    AIGatewaySettingsView,
    AIRequestStatus,
    AITraceView,
    ReturnCreateRequest,
    ReturnSessionView,
    ReturnStatus,
    SeedStatusView,
    SupportCaseStatus,
    SupportCaseView,
    SupportOperationRequest,
    TimelineEvent,
    normalize_utc_datetime,
    utc_now,
)
from return_platform.operations.order_lines.reservations import (
    ensure_order_line_reservation_indexes,
)
from return_platform.operations.seed_manifest import (
    SOURCE_CUSTOMERS_DATASET,
    SOURCE_PRODUCTS_DATASET,
    SOURCE_SALES_DATASET,
    SOURCE_SHIPMENTS_DATASET,
    effective_seed_counts,
    manifest_digest,
    materialize_domain_seed,
    materialize_seed,
    scenario_counts,
)
from return_platform.operations.support_events import ensure_support_event_indexes
from return_platform.resources import RuntimeResources

RETURNS: Final = "operational_returns"
EVENTS: Final = "operational_events"
SUPPORT_CASES: Final = "support_cases"
AI_TRACES: Final = "ai_gateway_traces"
AI_SETTINGS: Final = "ai_gateway_settings"
AI_RATE_LIMITS: Final = "ai_gateway_rate_limits"
AI_ATTEMPTS: Final = "ai_gateway_attempt_metrics"
WORKER_HEARTBEATS: Final = "worker_heartbeats"
SEED_METADATA: Final = "seed_metadata"
CASES: Final = "cases"
CASE_FACTS: Final = "case_facts"
RETURN_RECORDS: Final = "return_records"
RETURN_ITEMS: Final = "operational_return_items"
HANDLING_UNITS: Final = "handling_units"
PICKUP_SITES: Final = "pickup_sites"
PICKUP_REQUESTS: Final = "pickup_requests"
BRANCH_STAGING_RECORDS: Final = "branch_staging_records"
DOCUMENT_ARTIFACTS: Final = "document_artifacts"
SHIPPING_INSTRUCTIONS: Final = "shipping_instructions"
SHIPMENT_EVENTS: Final = "shipment_events"
OMC_COMMAND_RECORDS: Final = "omc_command_records"
AGENT_DECISIONS: Final = "agent_decisions"
VENDOR_RETURN_LINKS: Final = "vendor_return_links"
#: Re-exported from the module that owns the collection rather than restated, so
#: the handle below and the indexes built on it can never name two collections.
INTEGRATION_OUTBOX: Final = INTEGRATION_OUTBOX_COLLECTION
RETURN_CONFIGURATION_SNAPSHOTS: Final = "return_configuration_snapshots"
SOURCE_ORDERS: Final = "orders"
SOURCE_CUSTOMERS: Final = "customers"
SOURCE_PRODUCTS: Final = "products"
_EVENT_DEDUPLICATION_INDEX: Final = "stream_deduplication_unique"
_EVENT_DEDUPLICATION_KEYS: Final = (
    ("streamId", ASCENDING),
    ("deduplicationKey", ASCENDING),
)
_EVENT_DEDUPLICATION_FILTER: Final = {"deduplicationKey": {"$type": "string"}}

#: The upstream datasets this repository reads and seeds, named as configuration
#: names them. Where each one physically lives is resolved through the source
#: binding catalogue, so renaming a collection is a configuration edit and never
#: a code change. This tuple used to be `DOMAIN_SOURCE_COLLECTIONS` and held
#: `salesInv`, `customerOutboundCDM`, `shipmentInfo`, `lkpSearchProduct` -- the
#: physical names -- which is precisely what made the rename a code change.
DOMAIN_SOURCE_DATASETS: Final = (
    SOURCE_SALES_DATASET,
    SOURCE_CUSTOMERS_DATASET,
    SOURCE_SHIPMENTS_DATASET,
    SOURCE_PRODUCTS_DATASET,
)

#: The lookup indexes the deterministic seed needs on each domain dataset, as
#: (key, index name, unique). Declared per dataset rather than written out
#: against a collection name so that the same rebinding that moves the reads
#: moves the indexes with them -- an index built on the collection a dataset no
#: longer resolves to is worse than no index, because the query it was meant to
#: serve still collection-scans while the operator can see an index exists.
_DOMAIN_SOURCE_INDEXES: Final[dict[str, tuple[tuple[str, str, bool], ...]]] = {
    SOURCE_SALES_DATASET: (
        ("salesHdrEventData.orderId", "sales_order_number_unique", True),
        ("salesHdr.salesHdrData.custId", "sales_customer_lookup", False),
        ("salesLines.lineData.productId", "sales_product_lookup", False),
        ("salesLines.lineData.sku", "sales_sku_lookup", False),
    ),
    SOURCE_CUSTOMERS_DATASET: (
        ("customerId", "customer_id_unique", True),
        ("phoneNumber", "customer_phone_lookup", False),
        ("email", "customer_email_lookup", False),
    ),
    SOURCE_SHIPMENTS_DATASET: (("shipmentInfoEventData.trkNum", "tracking_number_unique", True),),
    SOURCE_PRODUCTS_DATASET: (
        ("productId", "product_id_unique", True),
        ("sku", "product_sku_lookup", False),
    ),
}


class SourceDatasetUnresolvedError(RuntimeError):
    """Configuration does not say where a dataset this code reads actually is.

    Raised rather than defaulted. Falling back to the name the collection had
    when this module was written would make a misconfigured platform read stale
    documents from the collection a rename was meant to retire, and report
    success -- the one failure mode the binding catalogue exists to prevent.
    """


class OperationalRepository(CaseRepository):
    """Repository for product-facing projections and immutable event evidence.

    The case aggregate lives in `CaseRepository` and is inherited rather than
    delegated to, so `repository.get_case(...)` and its dozen siblings read
    exactly as they did when the methods were declared in this file.
    """

    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        settings: Settings,
        source_client: AsyncMongoClient[dict[str, object]] | None = None,
        bindings: SourceBindingCatalogue | None = None,
    ) -> None:
        self._client = client
        self._source_client = source_client or client
        self._settings = settings
        self._db = client[settings.mongo_database]
        self._source_db = self._source_client[settings.source_mongo_database]
        # Resolved on first use rather than here, because building it reads the
        # published release and the stored overrides and this constructor is
        # synchronous and runs on every request. A supplied catalogue skips that
        # -- a caller that has already resolved one should not resolve a second.
        self._bindings = bindings
        self.returns = self._db[RETURNS]
        self.events = self._db[EVENTS]
        self.support_cases = self._db[SUPPORT_CASES]
        self.ai_traces = self._db[AI_TRACES]
        self.ai_settings = self._db[AI_SETTINGS]
        self.ai_rate_limits = self._db[AI_RATE_LIMITS]
        self.ai_attempts = self._db[AI_ATTEMPTS]
        self.worker_heartbeats = self._db[WORKER_HEARTBEATS]
        self.seed_metadata = self._db[SEED_METADATA]
        self.cases = self._db[CASES]
        self.case_facts = self._db[CASE_FACTS]
        self.return_records = self._db[RETURN_RECORDS]
        self.return_items = self._db[RETURN_ITEMS]
        self.handling_units = self._db[HANDLING_UNITS]
        self.pickup_sites = self._db[PICKUP_SITES]
        self.pickup_requests = self._db[PICKUP_REQUESTS]
        self.branch_staging_records = self._db[BRANCH_STAGING_RECORDS]
        self.document_artifacts = self._db[DOCUMENT_ARTIFACTS]
        self.shipping_instructions = self._db[SHIPPING_INSTRUCTIONS]
        self.shipment_events = self._db[SHIPMENT_EVENTS]
        self.omc_command_records = self._db[OMC_COMMAND_RECORDS]
        self.agent_decisions = self._db[AGENT_DECISIONS]
        self.vendor_return_links = self._db[VENDOR_RETURN_LINKS]
        self.integration_outbox = self._db[INTEGRATION_OUTBOX]
        self.return_configuration_snapshots = self._db[RETURN_CONFIGURATION_SNAPSHOTS]

    @property
    def platform_client(self) -> AsyncMongoClient[dict[str, object]]:
        """Expose the shared client without leaking collection internals."""
        return self._client

    @property
    def source_client(self) -> AsyncMongoClient[dict[str, object]]:
        """Expose the read/source client for governed cross-store services."""
        return self._source_client

    async def source_bindings(self) -> SourceBindingCatalogue:
        """Where configuration currently says each dataset lives.

        The same resolution the release compiler and the bindings API perform,
        and deliberately not a second one: a published release if there is one,
        the shipped schema file otherwise, with the stored overrides layered on
        top. Cached for the lifetime of this repository -- which is one request
        on the API paths and one process on the worker paths -- so a rebinding
        is picked up on the next request rather than mid-way through one.
        """
        if self._bindings is None:
            releases = SchemaReleaseStore(self._client, self._settings.mongo_database)
            baseline = await resolve_active_schema(
                self._settings.dynamic_knowledge_schema_path, releases
            )
            overrides = await SourceBindingStore(self._client, self._settings.mongo_database).list()
            self._bindings = catalogue_from(baseline, overrides)
        return self._bindings

    async def source_dataset(self, dataset: str) -> AsyncCollection[dict[str, object]]:
        """The collection a dataset resolves to, on the upstream source client.

        Only `object_ref["name"]` is taken from the resolved asset. The database
        stays `settings.source_mongo_database`: the shipped schema declares
        `return_source` for these four, which equals that setting only by
        default, and `targeted_sync.platform_store_source_ids` already treats an
        operator who renamed the setting as the authority. Honouring a declared
        database here would silently disagree with that.
        """
        asset = (await self.source_bindings()).resolve(dataset)
        if asset is None:
            raise SourceDatasetUnresolvedError(
                f"no configured source binding resolves dataset {dataset!r}"
            )
        collection_name = asset.object_ref.get("name")
        if not collection_name:
            raise SourceDatasetUnresolvedError(
                f"dataset {dataset!r} resolves to source asset "
                f"{asset.source_asset_id!r}, whose object_ref names no collection"
            )
        return self._source_db[collection_name]

    async def ensure_indexes(self) -> None:
        await self.returns.create_index([("createdAt", DESCENDING)])
        await self.returns.create_index([("status", ASCENDING), ("updatedAt", ASCENDING)])
        await self.returns.create_index([("supportStatus", ASCENDING), ("updatedAt", ASCENDING)])
        await self.returns.create_index(
            [("trilogieOrderNumber", ASCENDING), ("createdAt", DESCENDING)]
        )
        await self.returns.create_index(
            [("sourceWebOrderNumber", ASCENDING), ("createdAt", DESCENDING)]
        )
        # Partial, not sparse: `supportWorkItemId` is written as explicit
        # `None` on every return that has not reached Support, so `sparse`
        # indexed the entire collection and saved nothing.
        await self._replace_index(
            self.returns,
            "supportWorkItemId",
            partialFilterExpression={"supportWorkItemId": {"$type": "string"}},
        )
        # Partial rather than sparse for the same reason as the rest: it is
        # correct whether the field is absent or written as null, and on a
        # *unique* index the difference is a duplicate-key failure rather than
        # wasted space.
        await self._replace_index(
            self.returns,
            "idempotencyKey",
            unique=True,
            partialFilterExpression={"idempotencyKey": {"$type": "string"}},
        )
        await self.events.create_index(
            [("streamId", ASCENDING), ("sequence", ASCENDING)], unique=True
        )
        await self._ensure_event_deduplication_index()
        await self.events.create_index([("publishedAt", ASCENDING), ("occurredAt", ASCENDING)])
        await self.support_cases.create_index(
            [("status", ASCENDING), ("priorityRank", ASCENDING), ("slaDueAt", ASCENDING)]
        )
        # Unique *and* partial. This was unique + sparse over a field written
        # as explicit `None`, which is the dangerous combination rather than
        # merely a wasteful one: `sparse` omits a document only when the field
        # is absent, so every session-less support case indexed `null`, and the
        # second one raised DuplicateKeyError. A case raised from a case id
        # rather than a session -- the ordinary path since Channel B learned to
        # open a case thread -- could therefore be created exactly once.
        await self._replace_index(
            self.support_cases,
            "sessionId",
            unique=True,
            partialFilterExpression={"sessionId": {"$type": "string"}},
        )
        await self.ai_traces.create_index([("createdAt", DESCENDING)])
        await self.ai_traces.create_index([("sessionId", ASCENDING), ("createdAt", DESCENDING)])
        await self.ai_attempts.create_index([("createdAt", DESCENDING)])
        await self.ai_attempts.create_index([("traceId", ASCENDING), ("attemptNumber", ASCENDING)])
        await self.ai_attempts.create_index([("taskId", ASCENDING), ("createdAt", DESCENDING)])
        await self.ai_attempts.create_index(
            [("provider", ASCENDING), ("model", ASCENDING), ("createdAt", DESCENDING)]
        )
        await self.worker_heartbeats.create_index("expiresAt", expireAfterSeconds=0)
        await self.ai_rate_limits.create_index("expiresAt", expireAfterSeconds=0)
        await self.cases.create_index("caseId", unique=True)
        # The associate's case list: equality on the two owner fields, then the
        # sort field, so it is served from the index rather than sorted in memory.
        await self.cases.create_index(
            [("tenantId", ASCENDING), ("principalId", ASCENDING), ("updatedAt", DESCENDING)]
        )
        # Both channel pointers are lookup keys, not just stored values: the
        # whole point of the case is that a support outcome can find its way
        # back to the associate's conversation without a client-side join.
        #
        # Partial rather than sparse on all three. A case is created before it
        # has a work item or a workflow, and those fields are written as
        # explicit nulls -- which `sparse` does *not* skip. Sparse omits a
        # document only when the field is absent, so a second case with a null
        # pointer collides with the first. The partial filter indexes only
        # documents where the pointer is really set, which is the rule intended:
        # one conversation, one work item and one workflow each map to at most
        # one case.
        for pointer in ("channelBWorkItemId", "workflowId"):
            await self.cases.create_index(
                pointer,
                unique=True,
                partialFilterExpression={pointer: {"$type": "string"}},
            )
        # Not unique. One conversation confirming two *different* orders is two
        # returns, and the confirmation key below is what actually bounds
        # duplication. `get_case_by_conversation` therefore returns the most
        # recent rather than assuming there is only one.
        await self.cases.create_index(
            [("channelAConversationId", ASCENDING), ("createdAt", DESCENDING)],
            partialFilterExpression={"channelAConversationId": {"$type": "string"}},
        )
        # The real idempotency boundary: tenant | conversation | order | lines.
        # A retried confirmation turn resolves to the existing case; a different
        # order or a different line set is a different intent and gets its own.
        await self.cases.create_index(
            "confirmationKey",
            unique=True,
            partialFilterExpression={"confirmationKey": {"$type": "string"}},
        )
        # Partial, not sparse. `create_case` writes `sessionId: None`
        # explicitly, and `sparse` only omits a document where the field is
        # *absent* -- so every case without a session was indexed anyway, which
        # is the whole population until one is linked.
        await self._replace_index(
            self.cases,
            "sessionId",
            partialFilterExpression={"sessionId": {"$type": "string"}},
        )
        await self.case_facts.create_index("factId", unique=True)
        # Serves both the projection (newest per name) and the audit read
        # (everything about one case, in order).
        await self.case_facts.create_index(
            [("caseId", ASCENDING), ("factName", ASCENDING), ("recordedAt", DESCENDING)]
        )
        await self.return_records.create_index("returnRecordId", unique=True)
        await self.return_records.create_index([("caseId", ASCENDING), ("createdAt", ASCENDING)])
        # Partial, not sparse. A record exists from the moment the case decides
        # to raise it and gets its RMA later from Support, so several records on
        # one case legitimately sit with a null reference at once -- and a
        # *compound* sparse index does not help, because it only omits a
        # document when every indexed field is missing. `caseId` is always
        # present, so the document is indexed with a null reference and the
        # second null collides. The partial filter indexes only records that
        # actually have an RMA, which is the rule intended: one RMA cannot be
        # recorded twice against one case.
        await self.return_records.create_index(
            [("caseId", ASCENDING), ("returnReference", ASCENDING)],
            unique=True,
            partialFilterExpression={"returnReference": {"$type": "string"}},
        )
        await self.return_items.create_index("returnItemId", unique=True)
        # Items gained a case and a return-record association: one RMA covers N
        # items, so the item must say which RMA it belongs to.
        # Nullable by design -- an item is named before Support says which RMA
        # covers it -- and written as explicit null, so sparse indexed the
        # whole collection.
        await self._replace_index(
            self.return_items,
            "returnRecordId",
            partialFilterExpression={"returnRecordId": {"$type": "string"}},
        )
        # Partial, where it used to be plainly unique. The rule it encodes --
        # one item per (session, line) -- only means anything for an item that
        # belongs to a session, and case-scoped items have no session. Left
        # unconditional, every case-scoped item indexed as
        # `{sessionId: null, orderLineId: "L1"}` and the second case returning
        # line L1 collided with the first.
        await self.return_items.create_index(
            [("sessionId", ASCENDING), ("orderLineId", ASCENDING)],
            unique=True,
            partialFilterExpression={"sessionId": {"$type": "string"}},
        )
        # The same rule for the case-scoped half: one item per (case, line).
        await self.return_items.create_index(
            [("caseId", ASCENDING), ("orderLineId", ASCENDING)],
            unique=True,
            partialFilterExpression={"caseId": {"$type": "string"}},
        )
        await self.handling_units.create_index("handlingUnitId", unique=True)
        await self.handling_units.create_index(
            [("sessionId", ASCENDING), ("sequence", ASCENDING)], unique=True
        )
        await self.handling_units.create_index(
            "trackingNumber",
            unique=True,
            partialFilterExpression={"trackingNumber": {"$type": "string"}},
        )
        await self.pickup_sites.create_index("pickupSiteId", unique=True)
        await self.pickup_sites.create_index("sessionId", unique=True)
        await self.pickup_requests.create_index("pickupRequestId", unique=True)
        await self.pickup_requests.create_index("sessionId", unique=True)
        await self.branch_staging_records.create_index("stagingRecordId", unique=True)
        await self.branch_staging_records.create_index(
            [("sessionId", ASCENDING), ("handlingUnitId", ASCENDING)], unique=True
        )
        await self.document_artifacts.create_index("artifactId", unique=True)
        await self.document_artifacts.create_index(
            [("sessionId", ASCENDING), ("createdAt", DESCENDING)]
        )
        await self.document_artifacts.create_index(
            [("storageProvider", ASCENDING), ("storageKey", ASCENDING)], unique=True
        )
        await self.shipping_instructions.create_index("shippingInstructionId", unique=True)
        await self.shipping_instructions.create_index(
            [("sessionId", ASCENDING), ("issuedAt", DESCENDING)]
        )
        await self.shipment_events.create_index(
            [("sourceSystem", ASCENDING), ("sourceEventId", ASCENDING)], unique=True
        )
        await self.omc_command_records.create_index("commandId", unique=True)
        await self.omc_command_records.create_index("idempotencyKey", unique=True)
        await self.agent_decisions.create_index(
            [("sessionId", ASCENDING), ("createdAt", DESCENDING)]
        )
        await self.vendor_return_links.create_index("vendorReturnLinkId", unique=True)
        await self.vendor_return_links.create_index(
            [("sessionId", ASCENDING), ("omcRgaId", ASCENDING)], unique=True
        )
        # The outbox's five indexes, including the two this method used to omit:
        # `leaseUntil` (the dispatcher's lease predicate) and `(createdAt DESC)`
        # (the operator listing's sort). Defined next to the dispatcher that
        # depends on them and called from here so index creation stays in one
        # place -- the same arrangement as the Support-event index below. This
        # is the call the orchestrator reaches through `ensure_indexes`, and it
        # was the owner building the smallest subset.
        await ensure_integration_outbox_indexes(self._db)
        # `(caseId, supportEventId)` unique. The identity of a Support mutation,
        # and the only thing that makes a resent reply a no-op rather than a
        # second RMA. Defined next to the store that depends on it and called
        # from here so index creation stays in one place.
        await ensure_support_event_indexes(self._db)
        # One `ACTIVE` hold per (case, line), plus the availability read and the
        # expiry sweep's predicates. The unique partial index is what makes "a
        # case editing its own reservation" a well-defined operation rather than
        # an accumulation of holds nobody can reconcile. Defined beside the
        # lifecycle that depends on it and called from here, like the two above.
        await ensure_order_line_reservation_indexes(self._db)
        await self.return_configuration_snapshots.create_index("sha256", unique=True)
        await self.return_configuration_snapshots.create_index(
            [("assumptionSetVersion", ASCENDING), ("activatedAt", DESCENDING)]
        )

    async def persist_return_configuration_snapshot(
        self,
        *,
        path: str,
        sha256: str,
        schema_version: str,
        assumption_set_version: str,
        configuration: dict[str, Any],
        behavior_domains: dict[str, Any],
    ) -> None:
        """Persist one immutable, digest-addressed production configuration snapshot."""
        now = utc_now()
        await self.return_configuration_snapshots.update_one(
            {"sha256": sha256},
            {
                "$setOnInsert": {
                    "_id": sha256,
                    "sha256": sha256,
                    "path": path,
                    "schemaVersion": schema_version,
                    "assumptionSetVersion": assumption_set_version,
                    "configuration": configuration,
                    "behaviorDomains": behavior_domains,
                    "activatedAt": now,
                    "createdAt": now,
                },
                "$set": {"lastObservedAt": now},
            },
            upsert=True,
        )

    async def _replace_index(
        self,
        collection: Any,
        keys: Any,
        **options: Any,
    ) -> None:
        """Create an index, replacing one that differs only in its options.

        `create_index` is idempotent for an identical definition and raises for
        a conflicting one, which is the right default -- but it makes changing
        an index's options impossible against a database that already has the
        old one, and every sparse-to-partial correction below is exactly that
        change. Without this, `ensure_indexes` raises at startup on any
        deployment that ran a previous build.

        Only the conflicting index is dropped, and only after the conflict is
        reported, so this cannot quietly remove an index someone else defined.
        A concurrent initializer winning the race (code 27, index not found) is
        the migration succeeding, not failing.
        """
        try:
            await collection.create_index(keys, **options)
            return
        except OperationFailure as exc:
            # 85 IndexOptionsConflict, 86 IndexKeySpecsConflict.
            if exc.code not in (85, 86):
                raise

        existing = await collection.index_information()
        wanted = keys if isinstance(keys, list) else [(keys, ASCENDING)]
        for name, definition in existing.items():
            if name == "_id_" or list(definition.get("key", ())) != list(wanted):
                continue
            try:
                await collection.drop_index(name)
            except OperationFailure as exc:
                if exc.code != 27:
                    raise
        await collection.create_index(keys, **options)

    async def _ensure_event_deduplication_index(self) -> None:
        indexes = await self.events.index_information()
        expected_keys = list(_EVENT_DEDUPLICATION_KEYS)
        for index_name, definition in indexes.items():
            if list(definition.get("key", ())) != expected_keys:
                continue
            is_current = (
                index_name == _EVENT_DEDUPLICATION_INDEX
                and definition.get("unique") is True
                and definition.get("partialFilterExpression") == _EVENT_DEDUPLICATION_FILTER
                and not bool(definition.get("sparse", False))
            )
            if is_current:
                return
            try:
                await self.events.drop_index(index_name)
            except OperationFailure as exc:
                # Multiple API/worker processes initialize the same collections.
                # Another process may remove the legacy index after this process
                # inspected it; MongoDB code 27 means the desired migration won.
                if exc.code != 27:
                    raise

        await self.events.create_index(
            list(_EVENT_DEDUPLICATION_KEYS),
            name=_EVENT_DEDUPLICATION_INDEX,
            unique=True,
            partialFilterExpression=_EVENT_DEDUPLICATION_FILTER,
        )

    @staticmethod
    def _return_view(document: dict[str, Any]) -> ReturnSessionView:
        payload = {
            key: value for key, value in document.items() if key in ReturnSessionView.model_fields
        }
        payload["id"] = str(document["_id"])
        return ReturnSessionView.model_validate(payload)

    @staticmethod
    def _event_view(document: dict[str, Any]) -> TimelineEvent:
        payload = {
            key: value for key, value in document.items() if key in TimelineEvent.model_fields
        }
        payload["id"] = str(document["_id"])
        return TimelineEvent.model_validate(payload)

    @staticmethod
    def _support_view(document: dict[str, Any]) -> SupportCaseView:
        payload = {
            key: value for key, value in document.items() if key in SupportCaseView.model_fields
        }
        payload["id"] = str(document["_id"])
        status = str(document.get("status", ""))
        due_at = document.get("slaDueAt")
        payload["slaDueAt"] = due_at or document.get("createdAt")
        payload["slaBreached"] = bool(
            status in {SupportCaseStatus.OPEN.value, SupportCaseStatus.ASSIGNED.value}
            and isinstance(due_at, datetime)
            and utc_now() > normalize_utc_datetime(due_at)
        )
        return SupportCaseView.model_validate(payload)

    @staticmethod
    def _trace_view(document: dict[str, Any]) -> AITraceView:
        payload = {key: value for key, value in document.items() if key in AITraceView.model_fields}
        payload["id"] = str(document["_id"])
        return AITraceView.model_validate(payload)

    @staticmethod
    def _attempt_view(document: dict[str, Any]) -> AIUsageAttemptView:
        payload = {
            key: value for key, value in document.items() if key in AIUsageAttemptView.model_fields
        }
        payload["id"] = str(document.get("_id") or document.get("id"))
        return AIUsageAttemptView.model_validate(payload)

    async def create_return(
        self,
        payload: ReturnCreateRequest,
        *,
        correlation_id: str,
        actor_id: str,
    ) -> ReturnSessionView:
        now = utc_now()
        session_id = str(uuid.uuid4())
        document: dict[str, Any] = {
            "_id": session_id,
            "correlationId": correlation_id,
            "workflowId": None,
            "workflowMode": payload.workflowMode,
            "customerReference": payload.customerReference,
            "orderReference": payload.orderReference,
            "itemReferences": payload.itemReferences,
            "productReferences": payload.productReferences or list(payload.itemReferences),
            "processingWarehouseReference": payload.processingWarehouseReference,
            "productType": payload.productType,
            "reasonCode": payload.reasonCode,
            "returnQuantity": payload.returnQuantity,
            "packageCount": payload.packageCount,
            "shippingPathExpectation": payload.shippingPathExpectation,
            "orderSource": payload.orderSource,
            "sourceWebOrderNumber": payload.sourceWebOrderNumber,
            "trilogieOrderNumber": payload.trilogieOrderNumber,
            "productPresence": payload.productPresence,
            "branchReference": payload.branchReference,
            "associateReference": payload.associateReference,
            "pickupAssessment": payload.pickupAssessment,
            "assumptionSetVersion": payload.assumptionSetVersion,
            "notes": payload.notes,
            "channel": payload.channel,
            "status": ReturnStatus.QUEUED.value,
            "currentStage": "INTAKE",
            "progressPercentage": 0,
            "eligibilityDecision": None,
            "returnReference": None,
            "supportTicketReference": None,
            "supportWorkItemId": None,
            "supportStatus": None,
            "omcReturnVersion": None,
            "approvedReturnMethod": None,
            "shippingInstructionReference": None,
            "customerResolutionStatus": "PENDING",
            "physicalReturnStatus": "NOT_STARTED",
            "warehouseStatus": "NOT_REQUIRED_OR_PENDING",
            "vendorRecoveryStatus": "NOT_REQUIRED_OR_PENDING",
            "caseClosureStatus": "OPEN",
            "trackingReference": None,
            "bayReference": None,
            "feedbackReference": None,
            "supportCaseId": None,
            "aiRequestId": None,
            "failureCode": None,
            "failureMessage": None,
            "version": 0,
            "lastEventSequence": 0,
            "orchestrationState": "QUEUED",
            "orchestrationOwner": None,
            "orchestrationLeaseUntil": None,
            "idempotencyKey": payload.idempotencyKey,
            "createdBy": actor_id,
            "createdAt": now,
            "updatedAt": now,
        }
        try:
            await self.returns.insert_one(document)
        except DuplicateKeyError:
            if payload.idempotencyKey is None:
                raise
            existing = await self.returns.find_one({"idempotencyKey": payload.idempotencyKey})
            if existing is None:
                raise
            return self._return_view(cast(dict[str, Any], existing))
        await self.append_event(
            session_id,
            event_type="RETURN_REQUEST_ACCEPTED",
            actor_type="USER",
            actor_id=actor_id,
            payload={
                "orderReference": payload.orderReference,
                "itemCount": len(payload.itemReferences),
                "reasonCode": payload.reasonCode,
                "returnQuantity": payload.returnQuantity,
                "packageCount": payload.packageCount,
                "shippingPathExpectation": payload.shippingPathExpectation,
                "orderSource": payload.orderSource,
                "productPresence": payload.productPresence,
                "assumptionSetVersion": payload.assumptionSetVersion,
            },
        )
        stored = await self.returns.find_one({"_id": session_id})
        assert stored is not None
        return self._return_view(cast(dict[str, Any], stored))

    async def persist_agent_decision(
        self,
        *,
        aggregate_id: str,
        session_id: str | None,
        decision: dict[str, Any],
        decision_key: str,
        actor_id: str,
    ) -> None:
        now = utc_now()
        document_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{aggregate_id}:{decision.get('agent')}:{decision_key}",
            )
        )
        await self.agent_decisions.update_one(
            {"_id": document_id},
            {
                "$setOnInsert": {
                    "_id": document_id,
                    "aggregateId": aggregate_id,
                    "sessionId": session_id,
                    "decisionKey": decision_key,
                    "agent": decision.get("agent"),
                    "decisionType": decision.get("decisionType"),
                    "decision": decision,
                    "createdBy": actor_id,
                    "createdAt": now,
                },
                "$set": {"updatedAt": now},
            },
            upsert=True,
        )

    async def list_agent_decisions(self, session_id: str) -> list[dict[str, Any]]:
        cursor = self.agent_decisions.find({"sessionId": session_id}).sort("createdAt", ASCENDING)
        return [
            {key: value for key, value in cast(dict[str, Any], document).items() if key != "_id"}
            async for document in cursor
        ]

    async def persist_return_intake_records(
        self,
        *,
        session_id: str,
        order_line_id: str,
        product_id: str,
        reason_code: str,
        requested_quantity: int,
        approved_method: str,
        product_presence: str,
        package_count: int,
        pickup_assessment: dict[str, Any] | None,
        attachment_ids: list[str],
        actor_id: str,
    ) -> None:
        """Persist idempotent item, handling-unit, and pickup projections."""
        now = utc_now()
        return_item_id = f"{session_id}:{order_line_id}"
        await self.return_items.update_one(
            {"returnItemId": return_item_id},
            {
                "$setOnInsert": {
                    "_id": str(uuid.uuid4()),
                    "returnItemId": return_item_id,
                    "sessionId": session_id,
                    "orderLineId": order_line_id,
                    "productId": product_id,
                    "requestedQuantity": requested_quantity,
                    "approvedQuantity": None,
                    "reasonCode": reason_code,
                    "attachmentIds": list(attachment_ids),
                    "disposition": "PENDING",
                    "version": 0,
                    "createdBy": actor_id,
                    "createdAt": now,
                },
                "$set": {"updatedAt": now},
            },
            upsert=True,
        )
        handling_type = "PALLET" if approved_method in {"BRANCH_LTL", "OFFSITE_LTL"} else "PACKAGE"
        for sequence in range(1, package_count + 1):
            handling_unit_id = f"{session_id}:HU:{sequence}"
            await self.handling_units.update_one(
                {"handlingUnitId": handling_unit_id},
                {
                    "$setOnInsert": {
                        "_id": str(uuid.uuid4()),
                        "handlingUnitId": handling_unit_id,
                        "sessionId": session_id,
                        "sequence": sequence,
                        "handlingUnitType": handling_type,
                        "returnItemAllocations": [
                            {
                                "returnItemId": return_item_id,
                                "quantity": (requested_quantity if package_count == 1 else None),
                            }
                        ],
                        "physicalStatus": "PLANNED",
                        "shippingInstructionId": None,
                        "trackingNumber": None,
                        "bolReference": None,
                        "licensePlateIds": [],
                        "version": 0,
                        "createdBy": actor_id,
                        "createdAt": now,
                    },
                    "$set": {"updatedAt": now},
                },
                upsert=True,
            )
        if product_presence.startswith("OFFSITE_"):
            assessment = pickup_assessment or {}
            pickup_site_id = f"{session_id}:PICKUP_SITE"
            pickup_request_id = f"{session_id}:PICKUP"
            await self.pickup_sites.update_one(
                {"pickupSiteId": pickup_site_id},
                {
                    "$setOnInsert": {
                        "_id": str(uuid.uuid4()),
                        "pickupSiteId": pickup_site_id,
                        "sessionId": session_id,
                        "locationType": product_presence,
                        **assessment,
                        "validatedBy": actor_id,
                        "validatedAt": now,
                        "version": 0,
                        "createdAt": now,
                    },
                    "$set": {"updatedAt": now},
                },
                upsert=True,
            )
            await self.pickup_requests.update_one(
                {"pickupRequestId": pickup_request_id},
                {
                    "$setOnInsert": {
                        "_id": str(uuid.uuid4()),
                        "pickupRequestId": pickup_request_id,
                        "sessionId": session_id,
                        "pickupSiteId": pickup_site_id,
                        "status": "ASSESSMENT_COMPLETE",
                        "handlingUnitIds": [
                            f"{session_id}:HU:{sequence}"
                            for sequence in range(1, package_count + 1)
                        ],
                        "equipmentRequirements": [],
                        "carrier": None,
                        "serviceLevel": None,
                        "scheduledWindowStart": None,
                        "scheduledWindowEnd": None,
                        "bolReference": None,
                        "version": 0,
                        "createdBy": actor_id,
                        "createdAt": now,
                    },
                    "$set": {"updatedAt": now},
                },
                upsert=True,
            )

    async def list_return_items(self, session_id: str) -> list[dict[str, Any]]:
        cursor = self.return_items.find({"sessionId": session_id}).sort("createdAt", ASCENDING)
        return [cast(dict[str, Any], document) async for document in cursor]

    async def update_return_item(
        self,
        return_item_id: str,
        updates: dict[str, Any],
        *,
        expected_version: int,
    ) -> dict[str, Any]:
        """Move one item's fields, and the case revision with them (plan sect. 6.5).

        `operational_return_items` holds both shapes: an item keyed to a return
        *session* (the legacy path) and an item keyed to a *case*. Only the
        second is on a `CaseDetail` projection -- `selectedItems` reads it, and
        `returnRecordId` is what attributes an item to an RMA -- so the bump is
        conditional on the updated document actually carrying a `caseId`. A
        session item has no case to invalidate, and bumping for it would need a
        case id that does not exist.

        The case id comes off the updated document rather than off a parameter,
        exactly as `CaseRepository.update_return_record` takes it: the callers
        hold an item id and nothing else, and two sources for one truth is two
        things that can disagree.

        Written through `_in_transaction` and `bump_case_revision` -- the
        mechanism `case_repository.py` already established -- rather than a
        second one. This method lives here and not there only because
        `OperationalRepository` shadows the mixin's item collection with the
        session-scoped view, which is why it was missed when the four writers
        over there were fixed.

        A version mismatch raises from *inside* the transaction, so the bump
        rolls back with the failed update: the loser of a compare-and-set
        changed nothing and must move no revision.
        """
        now = utc_now()

        async def _write(session: AsyncClientSession) -> dict[str, Any]:
            document = await self.return_items.find_one_and_update(
                {"returnItemId": return_item_id, "version": expected_version},
                {"$set": {**updates, "updatedAt": now}, "$inc": {"version": 1}},
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if document is None:
                exists = await self.return_items.find_one(
                    {"returnItemId": return_item_id}, {"_id": 1}, session=session
                )
                if exists is None:
                    raise KeyError(return_item_id)
                raise ConcurrencyConflictError(return_item_id)
            case_id = document.get("caseId")
            if isinstance(case_id, str) and case_id:
                await self.bump_case_revision(case_id, session=session, when=now)
            return cast(dict[str, Any], document)

        return await self._in_transaction(_write)

    async def assign_return_item_to_record(
        self, return_item_id: str, *, return_record_id: str, expected_version: int
    ) -> dict[str, Any]:
        """Attach an item to the RMA that covers it, once Support has said which.

        The live one of the pair: `return_case_activities._assign_items_to_record`
        calls it on every Support outcome that maps order lines to an RMA, and
        the attribution it writes is on the projection. It inherits the revision
        bump from `update_return_item` rather than repeating it, so the two
        cannot come to hold the invariant differently.
        """
        return await self.update_return_item(
            return_item_id, {"returnRecordId": return_record_id}, expected_version=expected_version
        )

    async def list_handling_units(self, session_id: str) -> list[dict[str, Any]]:
        cursor = self.handling_units.find({"sessionId": session_id}).sort("sequence", ASCENDING)
        return [cast(dict[str, Any], document) async for document in cursor]

    async def get_pickup_projection(self, session_id: str) -> dict[str, Any] | None:
        site = await self.pickup_sites.find_one({"sessionId": session_id})
        pickup_request = await self.pickup_requests.find_one({"sessionId": session_id})
        if site is None and pickup_request is None:
            return None
        return {
            "site": (
                None
                if site is None
                else {key: value for key, value in site.items() if key != "_id"}
            ),
            "request": (
                None
                if pickup_request is None
                else {key: value for key, value in pickup_request.items() if key != "_id"}
            ),
        }

    async def get_handling_unit(self, handling_unit_id: str) -> dict[str, Any] | None:
        document = await self.handling_units.find_one({"handlingUnitId": handling_unit_id})
        return None if document is None else cast(dict[str, Any], document)

    async def update_handling_unit(
        self,
        handling_unit_id: str,
        updates: dict[str, Any],
        *,
        expected_version: int,
    ) -> dict[str, Any]:
        document = await self.handling_units.find_one_and_update(
            {"handlingUnitId": handling_unit_id, "version": expected_version},
            {"$set": {**updates, "updatedAt": utc_now()}, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            exists = await self.handling_units.find_one(
                {"handlingUnitId": handling_unit_id}, {"_id": 1}
            )
            if exists is None:
                raise KeyError(handling_unit_id)
            raise ConcurrencyConflictError(handling_unit_id)
        return cast(dict[str, Any], document)

    async def upsert_branch_staging_record(
        self,
        *,
        session_id: str,
        handling_unit_id: str,
        branch_id: str,
        staging_location: str,
        return_number_tag_applied: bool,
        manufacturer_box_directly_marked: bool,
        inventory_added_to_branch: bool,
        actor_id: str,
    ) -> dict[str, Any]:
        now = utc_now()
        staging_record_id = f"{session_id}:{handling_unit_id}:STAGING"
        document = await self.branch_staging_records.find_one_and_update(
            {"sessionId": session_id, "handlingUnitId": handling_unit_id},
            {
                "$setOnInsert": {
                    "_id": str(uuid.uuid4()),
                    "stagingRecordId": staging_record_id,
                    "sessionId": session_id,
                    "handlingUnitId": handling_unit_id,
                    "createdAt": now,
                    "version": 0,
                },
                "$set": {
                    "branchId": branch_id,
                    "stagingLocation": staging_location,
                    "returnNumberTagApplied": return_number_tag_applied,
                    "manufacturerBoxDirectlyMarked": manufacturer_box_directly_marked,
                    "inventoryAddedToBranch": inventory_added_to_branch,
                    "confirmedBy": actor_id,
                    "confirmedAt": now,
                    "updatedAt": now,
                },
                "$inc": {"version": 1},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        assert document is not None
        return cast(dict[str, Any], document)

    async def list_branch_staging_records(self, session_id: str) -> list[dict[str, Any]]:
        cursor = self.branch_staging_records.find({"sessionId": session_id}).sort(
            "confirmedAt", ASCENDING
        )
        return [cast(dict[str, Any], document) async for document in cursor]

    async def get_pickup_request(self, session_id: str) -> dict[str, Any] | None:
        document = await self.pickup_requests.find_one({"sessionId": session_id})
        return None if document is None else cast(dict[str, Any], document)

    async def update_pickup_request(
        self,
        session_id: str,
        updates: dict[str, Any],
        *,
        expected_version: int,
    ) -> dict[str, Any]:
        document = await self.pickup_requests.find_one_and_update(
            {"sessionId": session_id, "version": expected_version},
            {"$set": {**updates, "updatedAt": utc_now()}, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            exists = await self.pickup_requests.find_one({"sessionId": session_id}, {"_id": 1})
            if exists is None:
                raise KeyError(session_id)
            raise ConcurrencyConflictError(session_id)
        return cast(dict[str, Any], document)

    async def register_document_artifact(
        self,
        *,
        session_id: str,
        artifact_id: str,
        artifact_type: str,
        storage_provider: str,
        storage_key: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
        classification: str,
        actor_id: str,
    ) -> dict[str, Any]:
        now = utc_now()
        document = {
            "_id": artifact_id,
            "artifactId": artifact_id,
            "sessionId": session_id,
            "artifactType": artifact_type,
            "storageProvider": storage_provider,
            "storageKey": storage_key,
            "contentType": content_type,
            "sizeBytes": size_bytes,
            "sha256": sha256,
            "classification": classification,
            "processingStatus": "REGISTERED",
            "createdBy": actor_id,
            "createdAt": now,
        }
        try:
            await self.document_artifacts.insert_one(document)
        except DuplicateKeyError:
            existing = await self.document_artifacts.find_one({"artifactId": artifact_id})
            if existing is None:
                raise
            return cast(dict[str, Any], existing)
        return document

    async def list_document_artifacts(self, session_id: str) -> list[dict[str, Any]]:
        cursor = self.document_artifacts.find({"sessionId": session_id}).sort(
            "createdAt", ASCENDING
        )
        return [cast(dict[str, Any], document) async for document in cursor]

    async def record_shipping_instruction(
        self,
        *,
        session_id: str,
        instruction_id: str,
        instruction_type: str,
        source_system: str,
        issued_by: str,
        handling_unit_ids: list[str] | None = None,
        carrier: str | None = None,
        tracking_numbers: list[str] | None = None,
        bol_reference: str | None = None,
        evidence_reference: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        document = {
            "_id": instruction_id,
            "shippingInstructionId": instruction_id,
            "sessionId": session_id,
            "handlingUnitIds": handling_unit_ids or [],
            "instructionType": instruction_type,
            "carrier": carrier,
            "trackingNumbers": tracking_numbers or [],
            "bolReference": bol_reference,
            "evidenceReference": evidence_reference,
            "sourceSystem": source_system,
            "authoritativeReadbackStatus": "CONFIRMED",
            "issuedBy": issued_by,
            "issuedAt": now,
            "createdAt": now,
            "updatedAt": now,
        }
        await self.shipping_instructions.update_one(
            {"shippingInstructionId": instruction_id},
            {"$setOnInsert": document},
            upsert=True,
        )
        stored = await self.shipping_instructions.find_one(
            {"shippingInstructionId": instruction_id}
        )
        assert stored is not None
        return cast(dict[str, Any], stored)

    async def list_shipping_instructions(self, session_id: str) -> list[dict[str, Any]]:
        cursor = self.shipping_instructions.find({"sessionId": session_id}).sort(
            "issuedAt", ASCENDING
        )
        return [cast(dict[str, Any], document) async for document in cursor]

    async def record_shipment_event(
        self,
        *,
        session_id: str,
        source_system: str,
        source_event_id: str,
        event_code: str,
        event_time: datetime,
        handling_unit_id: str | None = None,
        tracking_number: str | None = None,
        bol_reference: str | None = None,
        carrier: str | None = None,
        location: str | None = None,
        payload_digest: str | None = None,
    ) -> dict[str, Any]:
        shipment_event_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"{source_system}:{source_event_id}")
        )
        now = utc_now()
        document = {
            "_id": shipment_event_id,
            "shipmentEventId": shipment_event_id,
            "sessionId": session_id,
            "handlingUnitId": handling_unit_id,
            "trackingNumber": tracking_number,
            "bolReference": bol_reference,
            "carrier": carrier,
            "eventCode": event_code,
            "eventTime": event_time,
            "receivedAt": now,
            "location": location,
            "sourceSystem": source_system,
            "sourceEventId": source_event_id,
            "payloadDigest": payload_digest,
        }
        await self.shipment_events.update_one(
            {"sourceSystem": source_system, "sourceEventId": source_event_id},
            {"$setOnInsert": document},
            upsert=True,
        )
        stored = await self.shipment_events.find_one(
            {"sourceSystem": source_system, "sourceEventId": source_event_id}
        )
        assert stored is not None
        return cast(dict[str, Any], stored)

    async def list_shipment_events(self, session_id: str) -> list[dict[str, Any]]:
        cursor = self.shipment_events.find({"sessionId": session_id}).sort("eventTime", ASCENDING)
        return [cast(dict[str, Any], document) async for document in cursor]

    async def record_omc_command(
        self,
        *,
        command_id: str,
        idempotency_key: str,
        session_id: str,
        support_work_item_id: str,
        operation: str,
        request_digest: str,
        request_payload: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        now = utc_now()
        document = {
            "_id": command_id,
            "commandId": command_id,
            "idempotencyKey": idempotency_key,
            "sessionId": session_id,
            "supportWorkItemId": support_work_item_id,
            "operation": operation,
            "requestDigest": request_digest,
            "requestPayload": request_payload,
            "status": "PENDING",
            "attemptCount": 0,
            "authoritativeReturnReference": None,
            "authoritativeVersion": None,
            "responsePayload": None,
            "readbackDigest": None,
            "errorCode": None,
            "errorMessage": None,
            "createdBy": actor_id,
            "createdAt": now,
            "updatedAt": now,
        }
        try:
            await self.omc_command_records.insert_one(document)
        except DuplicateKeyError as error:
            existing = await self.omc_command_records.find_one({"idempotencyKey": idempotency_key})
            if existing is None:
                raise
            if existing.get("requestDigest") != request_digest:
                raise ConcurrencyConflictError("OMC command idempotency conflict") from error
            return cast(dict[str, Any], existing)
        return document

    async def confirm_omc_command(
        self,
        *,
        session_id: str,
        operation: str,
        authoritative_return_reference: str,
        authoritative_version: str,
        readback_digest: str,
        response_payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        now = utc_now()
        document = await self.omc_command_records.find_one_and_update(
            {"sessionId": session_id, "operation": operation},
            {
                "$set": {
                    "status": "CONFIRMED",
                    "authoritativeReturnReference": authoritative_return_reference,
                    "authoritativeVersion": authoritative_version,
                    "readbackDigest": readback_digest,
                    "responsePayload": response_payload,
                    "confirmedAt": now,
                    "updatedAt": now,
                }
            },
            sort=[("createdAt", DESCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        return None if document is None else cast(dict[str, Any], document)

    async def list_omc_commands(self, session_id: str) -> list[dict[str, Any]]:
        cursor = self.omc_command_records.find({"sessionId": session_id}).sort(
            "createdAt", ASCENDING
        )
        return [cast(dict[str, Any], document) async for document in cursor]

    async def enqueue_integration_command(
        self,
        *,
        topic: str,
        aggregate_type: str,
        aggregate_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now()
        command_id = str(uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key))
        document = {
            "_id": command_id,
            "topic": topic,
            "aggregateType": aggregate_type,
            "aggregateId": aggregate_id,
            "idempotencyKey": idempotency_key,
            "payload": payload,
            "status": "PENDING",
            "attemptCount": 0,
            "nextAttemptAt": now,
            "createdAt": now,
            "updatedAt": now,
        }
        await self.integration_outbox.update_one(
            {"idempotencyKey": idempotency_key},
            {"$setOnInsert": document},
            upsert=True,
        )
        stored = await self.integration_outbox.find_one({"idempotencyKey": idempotency_key})
        assert stored is not None
        return cast(dict[str, Any], stored)

    async def list_integration_commands(self, aggregate_id: str) -> list[dict[str, Any]]:
        cursor = self.integration_outbox.find({"aggregateId": aggregate_id}).sort(
            "createdAt", ASCENDING
        )
        return [cast(dict[str, Any], document) async for document in cursor]

    async def upsert_vendor_return_link(
        self,
        *,
        session_id: str,
        omc_rga_id: str,
        omc_rga_number: str | None,
        omc_cart_item_ids: list[str],
        po_numbers: list[str],
        vendor_id: str | None,
        status: str,
        credit_memo_ids: list[str] | None,
        actor_id: str,
    ) -> dict[str, Any]:
        now = utc_now()
        link_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{session_id}:{omc_rga_id}"))
        document = await self.vendor_return_links.find_one_and_update(
            {"sessionId": session_id, "omcRgaId": omc_rga_id},
            {
                "$setOnInsert": {
                    "_id": link_id,
                    "vendorReturnLinkId": link_id,
                    "sessionId": session_id,
                    "omcRgaId": omc_rga_id,
                    "createdBy": actor_id,
                    "createdAt": now,
                },
                "$set": {
                    "omcRgaNumber": omc_rga_number,
                    "omcCartItemIds": omc_cart_item_ids,
                    "poNumbers": po_numbers,
                    "vendorId": vendor_id,
                    "status": status,
                    "creditMemoIds": credit_memo_ids or [],
                    "updatedAt": now,
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        assert document is not None
        return cast(dict[str, Any], document)

    async def list_vendor_return_links(self, session_id: str) -> list[dict[str, Any]]:
        cursor = self.vendor_return_links.find({"sessionId": session_id}).sort(
            "createdAt", ASCENDING
        )
        return [cast(dict[str, Any], document) async for document in cursor]

    async def get_return(self, session_id: str) -> ReturnSessionView | None:
        document = await self.returns.find_one({"_id": session_id})
        return None if document is None else self._return_view(cast(dict[str, Any], document))

    async def list_returns(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ReturnSessionView]:
        query: dict[str, Any] = {} if status is None else {"status": status}
        cursor = self.returns.find(query).sort("createdAt", DESCENDING).limit(limit)
        return [self._return_view(cast(dict[str, Any], document)) async for document in cursor]

    async def update_return(
        self,
        session_id: str,
        updates: dict[str, Any],
        *,
        expected_version: int | None = None,
        session: Any = None,
    ) -> ReturnSessionView:
        query: dict[str, Any] = {"_id": session_id}
        if expected_version is not None:
            query["version"] = expected_version
        update = {"$set": {**updates, "updatedAt": utc_now()}, "$inc": {"version": 1}}
        document = await self.returns.find_one_and_update(
            query,
            update,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        if document is None:
            exists = await self.returns.find_one({"_id": session_id}, {"_id": 1}, session=session)
            if exists is None:
                raise KeyError(session_id)
            raise ConcurrencyConflictError(session_id)
        return self._return_view(cast(dict[str, Any], document))

    async def claim_next_return(
        self, worker_id: str, lease_seconds: int = 30
    ) -> ReturnSessionView | None:
        now = utc_now()
        lease_until = now + timedelta(seconds=lease_seconds)
        document = await self.returns.find_one_and_update(
            {
                "status": {
                    "$nin": [
                        ReturnStatus.COMPLETED.value,
                        ReturnStatus.CANCELLED.value,
                        ReturnStatus.FAILED.value,
                    ]
                },
                "orchestrationState": {"$in": ["QUEUED", "RUNNING"]},
                "$and": [
                    {
                        "$or": [
                            {"workflowMode": "LEGACY_V1"},
                            {"workflowMode": {"$exists": False}},
                        ]
                    },
                    {
                        "$or": [
                            {"orchestrationLeaseUntil": None},
                            {"orchestrationLeaseUntil": {"$lt": now}},
                            {"orchestrationOwner": worker_id},
                        ]
                    },
                ],
            },
            {
                "$set": {
                    "orchestrationState": "RUNNING",
                    "orchestrationOwner": worker_id,
                    "orchestrationLeaseUntil": lease_until,
                    "updatedAt": now,
                }
            },
            sort=[("updatedAt", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        return None if document is None else self._return_view(cast(dict[str, Any], document))

    async def release_return(self, session_id: str, state: str) -> None:
        await self.returns.update_one(
            {"_id": session_id},
            {
                "$set": {
                    "orchestrationState": state,
                    "orchestrationOwner": None,
                    "orchestrationLeaseUntil": None,
                    "updatedAt": utc_now(),
                },
                "$inc": {"version": 1},
            },
        )

    async def release_discovery_lock(self, session_id: str, *, reason: str) -> None:
        """Release only the active discovery lock bound to this return session."""
        now = utc_now()
        await self._db["discovery_locks"].update_many(
            {"returnSessionId": session_id, "status": "ACTIVE"},
            {
                "$set": {
                    "status": "RELEASED",
                    "releasedAt": now,
                    "releaseReason": reason,
                    "expiresAt": now,
                }
            },
        )

    async def append_event(
        self,
        stream_id: str,
        *,
        event_type: str,
        actor_type: str,
        actor_id: str,
        payload: dict[str, Any],
        deduplication_key: str | None = None,
        session: Any = None,
    ) -> TimelineEvent:
        if deduplication_key is not None:
            existing = await self.events.find_one(
                {"streamId": stream_id, "deduplicationKey": deduplication_key},
                session=session,
            )
            if existing is not None:
                return self._event_view(cast(dict[str, Any], existing))
        now = utc_now()
        owner = await self.returns.find_one_and_update(
            {"_id": stream_id},
            {"$inc": {"lastEventSequence": 1}},
            projection={"lastEventSequence": 1},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        if owner is None:
            raise KeyError(stream_id)
        sequence = int(str(owner["lastEventSequence"]))
        event_id = f"{stream_id}:{sequence}"
        document: dict[str, Any] = {
            "_id": event_id,
            "streamId": stream_id,
            "sequence": sequence,
            "eventType": event_type,
            "actorType": actor_type,
            "actorId": actor_id,
            "payload": payload,
            "occurredAt": now,
            "publishedAt": None,
        }
        if deduplication_key is not None:
            document["deduplicationKey"] = deduplication_key
        try:
            await self.events.insert_one(document, session=session)
        except DuplicateKeyError:
            if deduplication_key is None:
                raise
            existing = await self.events.find_one(
                {"streamId": stream_id, "deduplicationKey": deduplication_key},
                session=session,
            )
            if existing is None:
                raise
            return self._event_view(cast(dict[str, Any], existing))
        return self._event_view(document)

    async def list_events(
        self,
        stream_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1_000,
    ) -> list[TimelineEvent]:
        cursor = (
            self.events.find({"streamId": stream_id, "sequence": {"$gt": after_sequence}})
            .sort("sequence", ASCENDING)
            .limit(limit)
        )
        return [self._event_view(cast(dict[str, Any], document)) async for document in cursor]

    async def list_unpublished_events(self, limit: int = 100) -> list[TimelineEvent]:
        cursor = self.events.find({"publishedAt": None}).sort("occurredAt", ASCENDING).limit(limit)
        return [self._event_view(cast(dict[str, Any], document)) async for document in cursor]

    async def mark_event_published(self, event_id: str) -> None:
        await self.events.update_one(
            {"_id": event_id, "publishedAt": None}, {"$set": {"publishedAt": utc_now()}}
        )

    async def consume_ai_quota(self, bucket: str) -> bool:
        now = utc_now()
        minute = now.replace(second=0, microsecond=0)
        quota_id = f"{bucket}:{minute.isoformat()}"
        try:
            document = await self.ai_rate_limits.find_one_and_update(
                {"_id": quota_id, "count": {"$lt": self._settings.ai_requests_per_minute}},
                {
                    "$inc": {"count": 1},
                    "$setOnInsert": {
                        "bucket": bucket,
                        "windowStartedAt": minute,
                        "expiresAt": minute + timedelta(minutes=2),
                    },
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            return False
        return document is not None

    async def create_ai_trace(
        self,
        *,
        session_id: str | None,
        status: AIRequestStatus,
        prompt_version: str,
        redacted_input: dict[str, Any],
        system_prompt: str,
        request_digest: str,
        original_request_digest: str | None = None,
        task_id: str = "RETURN_ELIGIBILITY_V1",
        configured_tier: str = "LIGHTWEIGHT",
        safety_status: str = "SAFE",
        safety_signals: list[str] | None = None,
    ) -> AITraceView:
        now = utc_now()
        trace_id = str(uuid.uuid4())
        document: dict[str, Any] = {
            "_id": trace_id,
            "sessionId": session_id,
            "status": status.value,
            "taskId": task_id,
            "configuredTier": configured_tier,
            "selectedTier": None,
            "provider": None,
            "model": None,
            "credentialId": None,
            "routeId": None,
            "promptVersion": prompt_version,
            "redactedInput": redacted_input,
            "systemPrompt": system_prompt,
            "requestDigest": request_digest,
            "responseText": None,
            "decision": None,
            "explanation": None,
            "confidenceMillionths": None,
            "latencyMs": None,
            "rateLimitWaitMs": 0,
            "inputTokens": None,
            "cachedInputTokens": None,
            "outputTokens": None,
            "totalTokens": None,
            # Not yet costed rather than costed at zero -- the trace is created
            # before the call is made, so there is nothing to price.
            "estimatedCostMicros": None,
            "pricingCurrency": None,
            "pricingStatus": AIPricingStatus.UNKNOWN.value,
            "pricingVersion": None,
            "responseDigest": None,
            "attempts": 0,
            "fallbackUsed": False,
            "safetyStatus": safety_status,
            "safetySignals": list(safety_signals or []),
            "selectionReason": None,
            "errorCode": None,
            "interceptedBy": None,
            "interceptionReason": None,
            "originalRequestDigest": original_request_digest,
            "version": 0,
            "createdAt": now,
            "updatedAt": now,
        }
        await self.ai_traces.insert_one(document)
        return self._trace_view(document)

    async def get_ai_trace(self, trace_id: str) -> AITraceView | None:
        document = await self.ai_traces.find_one({"_id": trace_id})
        return None if document is None else self._trace_view(cast(dict[str, Any], document))

    async def list_ai_traces(
        self, *, status: str | None = None, limit: int = 200
    ) -> list[AITraceView]:
        query: dict[str, Any] = {} if status is None else {"status": status}
        cursor = self.ai_traces.find(query).sort("createdAt", DESCENDING).limit(limit)
        return [self._trace_view(cast(dict[str, Any], document)) async for document in cursor]

    async def update_ai_trace(
        self,
        trace_id: str,
        updates: dict[str, Any],
        *,
        expected_version: int | None = None,
    ) -> AITraceView:
        query: dict[str, Any] = {"_id": trace_id}
        if expected_version is not None:
            query["version"] = expected_version
        document = await self.ai_traces.find_one_and_update(
            query,
            {"$set": {**updates, "updatedAt": utc_now()}, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            exists = await self.ai_traces.find_one({"_id": trace_id}, {"_id": 1})
            if exists is None:
                raise KeyError(trace_id)
            raise ConcurrencyConflictError(trace_id)
        return self._trace_view(cast(dict[str, Any], document))

    async def insert_ai_attempt_metric(self, document: dict[str, Any]) -> AIUsageAttemptView:
        payload = dict(document)
        payload.setdefault("_id", payload.get("id") or str(uuid.uuid4()))
        payload.setdefault("id", str(payload["_id"]))
        payload.setdefault("createdAt", utc_now())
        await self.ai_attempts.insert_one(payload)
        return self._attempt_view(payload)

    async def list_ai_attempt_metrics(
        self,
        *,
        trace_id: str | None = None,
        task_id: str | None = None,
        limit: int = 500,
    ) -> list[AIUsageAttemptView]:
        query: dict[str, Any] = {}
        if trace_id is not None:
            query["traceId"] = trace_id
        if task_id is not None:
            query["taskId"] = task_id
        cursor = self.ai_attempts.find(query).sort("createdAt", DESCENDING).limit(limit)
        return [self._attempt_view(cast(dict[str, Any], item)) async for item in cursor]

    async def summarize_ai_attempt_metrics(self) -> AIUsageSummaryView:
        attempts = await self.list_ai_attempt_metrics(limit=10_000)
        by_provider: dict[str, int] = {}
        by_model: dict[str, int] = {}
        by_task: dict[str, int] = {}
        by_tier: dict[str, int] = {}
        # Priced attempts are summed; unpriced ones are counted. Adding them at
        # zero is what made "estimated cost" a number that quietly disagreed
        # with the invoice, and mixing currencies into one integer would do the
        # same thing again, so a second currency makes the total unreportable
        # rather than wrong.
        priced_total = 0
        unpriced = 0
        currencies: set[str] = set()
        for item in attempts:
            if (
                item.pricingStatus is AIPricingStatus.PRICED
                and item.estimatedCostMicros is not None
            ):
                priced_total += item.estimatedCostMicros
                if item.pricingCurrency:
                    currencies.add(item.pricingCurrency)
            else:
                unpriced += 1
            provider = item.provider or "NONE"
            model = item.model or "NONE"
            by_provider[provider] = by_provider.get(provider, 0) + 1
            by_model[model] = by_model.get(model, 0) + 1
            by_task[item.taskId] = by_task.get(item.taskId, 0) + 1
            tier = (
                item.selectedTier.value
                if item.selectedTier is not None
                else item.configuredTier.value
            )
            by_tier[tier] = by_tier.get(tier, 0) + 1
        return AIUsageSummaryView(
            attempts=len(attempts),
            successes=sum(item.status == "SUCCESS" for item in attempts),
            failures=sum(item.status == "FAILED" for item in attempts),
            fallbacks=sum(item.fallbackUsed for item in attempts),
            blockedBySafety=sum(item.status == "SAFETY_BLOCKED" for item in attempts),
            inputTokens=sum(item.inputTokens for item in attempts),
            cachedInputTokens=sum(item.cachedInputTokens or 0 for item in attempts),
            outputTokens=sum(item.outputTokens for item in attempts),
            totalTokens=sum(item.totalTokens for item in attempts),
            estimatedCostMicros=priced_total,
            # One currency or none. Two would mean the integer above is the sum
            # of unlike quantities, and reporting it as a single figure would be
            # a bigger lie than the zero this step removed.
            pricingCurrency=next(iter(currencies)) if len(currencies) == 1 else None,
            unpricedAttempts=unpriced,
            byProvider=by_provider,
            byModel=by_model,
            byTask=by_task,
            byTier=by_tier,
        )

    async def create_support_case(
        self,
        *,
        session_id: str,
        case_type: str,
        priority: str,
        reason: str,
    ) -> SupportCaseView:
        now = utc_now()
        case_id = str(uuid.uuid4())
        priority_rank = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}.get(priority, 2)
        sla_hours = {"CRITICAL": 1, "HIGH": 4, "NORMAL": 24, "LOW": 72}.get(priority, 24)
        document: dict[str, Any] = {
            "_id": case_id,
            "sessionId": session_id,
            "caseType": case_type,
            "status": SupportCaseStatus.OPEN.value,
            "priority": priority,
            "priorityRank": priority_rank,
            "reason": reason,
            "slaDueAt": now + timedelta(hours=sla_hours),
            "assignedTo": None,
            "resolution": None,
            "decision": None,
            "version": 0,
            "createdAt": now,
            "updatedAt": now,
        }
        try:
            await self.support_cases.insert_one(document)
        except DuplicateKeyError:
            existing = await self.support_cases.find_one({"sessionId": session_id})
            if existing is None:
                raise
            existing_status = str(existing.get("status", ""))
            if existing_status in {SupportCaseStatus.OPEN.value, SupportCaseStatus.ASSIGNED.value}:
                return self._support_view(cast(dict[str, Any], existing))
            reopened = await self.support_cases.find_one_and_update(
                {"_id": existing["_id"], "version": existing.get("version", 0)},
                {
                    "$set": {
                        "caseType": case_type,
                        "status": SupportCaseStatus.OPEN.value,
                        "priority": priority,
                        "priorityRank": priority_rank,
                        "reason": reason,
                        "slaDueAt": now + timedelta(hours=sla_hours),
                        "assignedTo": None,
                        "resolution": None,
                        "decision": None,
                        "updatedAt": now,
                    },
                    "$inc": {"version": 1},
                },
                return_document=ReturnDocument.AFTER,
            )
            if reopened is None:
                raise ConcurrencyConflictError(session_id) from None
            await self.update_return(session_id, {"supportCaseId": str(reopened["_id"])})
            return self._support_view(cast(dict[str, Any], reopened))
        await self.update_return(session_id, {"supportCaseId": case_id})
        return self._support_view(document)

    async def get_support_case(self, case_id: str) -> SupportCaseView | None:
        document = await self.support_cases.find_one({"_id": case_id})
        return None if document is None else self._support_view(cast(dict[str, Any], document))

    async def get_support_case_for_session(self, session_id: str) -> SupportCaseView | None:
        document = await self.support_cases.find_one({"sessionId": session_id})
        return None if document is None else self._support_view(cast(dict[str, Any], document))

    async def list_support_cases(
        self, status: str | None = None, limit: int = 200
    ) -> list[SupportCaseView]:
        query: dict[str, Any] = {} if status is None else {"status": status}
        cursor = (
            self.support_cases.find(query)
            .sort([("priorityRank", ASCENDING), ("slaDueAt", ASCENDING)])
            .limit(limit)
        )
        return [self._support_view(cast(dict[str, Any], document)) async for document in cursor]

    async def update_support_case(
        self,
        case_id: str,
        updates: dict[str, Any],
        *,
        expected_version: int,
    ) -> SupportCaseView:
        document = await self.support_cases.find_one_and_update(
            {"_id": case_id, "version": expected_version},
            {"$set": {**updates, "updatedAt": utc_now()}, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            exists = await self.support_cases.find_one({"_id": case_id}, {"_id": 1})
            if exists is None:
                raise KeyError(case_id)
            raise ConcurrencyConflictError(case_id)
        return self._support_view(cast(dict[str, Any], document))

    async def operate_support_case(
        self,
        case_id: str,
        payload: SupportOperationRequest,
        *,
        actor_id: str,
    ) -> SupportCaseView:
        """Apply a support command atomically across case, return, trace, event, and audit."""

        async def transaction(session: Any) -> dict[str, Any]:
            case_document = await self.support_cases.find_one(
                {"_id": case_id, "version": payload.expectedVersion},
                session=session,
            )
            if case_document is None:
                exists = await self.support_cases.find_one(
                    {"_id": case_id}, {"_id": 1}, session=session
                )
                if exists is None:
                    raise KeyError(case_id)
                raise ConcurrencyConflictError(case_id)

            operation = payload.operation
            session_id = str(case_document["sessionId"])
            now = utc_now()
            case_updates: dict[str, Any]
            if case_document.get("status") not in {
                SupportCaseStatus.OPEN.value,
                SupportCaseStatus.ASSIGNED.value,
            }:
                raise ValueError("Only open or assigned support cases can be operated.")

            if operation == "ASSIGN":
                if not payload.assignee:
                    raise ValueError("ASSIGN requires assignee")
                case_updates = {
                    "status": SupportCaseStatus.ASSIGNED.value,
                    "assignedTo": payload.assignee,
                }
            elif operation in {"APPROVE", "REJECT"}:
                return_document = await self.returns.find_one({"_id": session_id}, session=session)
                if return_document is None or not return_document.get("aiRequestId"):
                    raise ValueError("Case has no AI request to resolve")
                trace_id = str(return_document["aiRequestId"])
                trace_result = await self.ai_traces.update_one(
                    {
                        "_id": trace_id,
                        "status": {"$ne": AIRequestStatus.MANUAL_OVERRIDE.value},
                    },
                    {
                        "$set": {
                            "status": AIRequestStatus.MANUAL_OVERRIDE.value,
                            "decision": operation,
                            "explanation": payload.reason,
                            "confidenceMillionths": 1_000_000,
                            "provider": "MANUAL",
                            "model": "support-override-v1",
                            "interceptedBy": actor_id,
                            "interceptionReason": payload.reason,
                            "updatedAt": now,
                        },
                        "$inc": {"version": 1},
                    },
                    session=session,
                )
                if trace_result.matched_count != 1:
                    raise ValueError("AI request is missing")
                await self.returns.update_one(
                    {"_id": session_id},
                    {
                        "$set": {
                            "status": ReturnStatus.RUNNING.value,
                            "orchestrationState": "QUEUED",
                            "orchestrationOwner": None,
                            "orchestrationLeaseUntil": None,
                            "updatedAt": now,
                        },
                        "$inc": {"version": 1},
                    },
                    session=session,
                )
                case_updates = {
                    "status": SupportCaseStatus.RESOLVED.value,
                    "resolution": payload.reason,
                    "decision": operation,
                    "assignedTo": case_document.get("assignedTo") or actor_id,
                }
            elif operation in {"RETRY", "RESUME"}:
                await self.returns.update_one(
                    {"_id": session_id},
                    {
                        "$set": {
                            "status": ReturnStatus.QUEUED.value,
                            "orchestrationState": "QUEUED",
                            "orchestrationOwner": None,
                            "orchestrationLeaseUntil": None,
                            "failureCode": None,
                            "failureMessage": None,
                            "updatedAt": now,
                        },
                        "$inc": {"version": 1},
                    },
                    session=session,
                )
                case_updates = {
                    "status": SupportCaseStatus.RESOLVED.value,
                    "resolution": payload.reason,
                }
            elif operation == "CANCEL":
                await self.returns.update_one(
                    {"_id": session_id},
                    {
                        "$set": {
                            "status": ReturnStatus.CANCELLED.value,
                            "orchestrationState": "CANCELLED",
                            "updatedAt": now,
                        },
                        "$inc": {"version": 1},
                    },
                    session=session,
                )
                case_updates = {
                    "status": SupportCaseStatus.CANCELLED.value,
                    "resolution": payload.reason,
                }
            else:
                raise ValueError("Unsupported operation")

            updated_case = await self.support_cases.find_one_and_update(
                {"_id": case_id, "version": payload.expectedVersion},
                {"$set": {**case_updates, "updatedAt": now}, "$inc": {"version": 1}},
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if updated_case is None:
                raise ConcurrencyConflictError(case_id)

            owner = await self.returns.find_one_and_update(
                {"_id": session_id},
                {"$inc": {"lastEventSequence": 1}},
                projection={"lastEventSequence": 1},
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if owner is None:
                raise KeyError(session_id)
            sequence = int(str(owner["lastEventSequence"]))
            await self.events.insert_one(
                {
                    "_id": f"{session_id}:{sequence}",
                    "streamId": session_id,
                    "sequence": sequence,
                    "eventType": f"SUPPORT_{operation}",
                    "actorType": "SUPPORT",
                    "actorId": actor_id,
                    "payload": {"caseId": case_id, "reason": payload.reason},
                    "occurredAt": now,
                    "publishedAt": None,
                },
                session=session,
            )
            await self._db["audit"].insert_one(
                {
                    "_id": str(uuid.uuid4()),
                    "action": f"SUPPORT_{operation}",
                    "actor": actor_id,
                    "target": case_id,
                    "timestamp": now,
                    "details": {"sessionId": session_id, "reason": payload.reason},
                },
                session=session,
            )
            return cast(dict[str, Any], updated_case)

        async with self._client.start_session() as mongo_session:
            updated = await mongo_session.with_transaction(transaction)
        return self._support_view(updated)

    async def get_ai_settings(self) -> AIGatewaySettingsView:
        document = await self.ai_settings.find_one({"_id": "global"})
        runtime_provider_order = self._settings.ai_provider_order.split(",")
        if document is None:
            now = utc_now()
            document = {
                "_id": "global",
                "interceptMode": self._settings.ai_interception_default,
                "providerOrder": runtime_provider_order,
                "version": 0,
                "updatedAt": now,
                "updatedBy": "system",
            }
            try:
                await self.ai_settings.insert_one(document)
            except DuplicateKeyError:
                document = await self.ai_settings.find_one({"_id": "global"})
                assert document is not None
        elif document.get("providerOrder") == ["NONE"] and runtime_provider_order != ["NONE"]:
            migrated = await self.ai_settings.find_one_and_update(
                {"_id": "global", "providerOrder": ["NONE"]},
                {
                    "$set": {
                        "providerOrder": runtime_provider_order,
                        "updatedAt": utc_now(),
                        "updatedBy": "runtime-configuration-migration",
                    },
                    "$inc": {"version": 1},
                },
                return_document=ReturnDocument.AFTER,
            )
            if migrated is not None:
                document = migrated
        return AIGatewaySettingsView.model_validate(
            {key: value for key, value in document.items() if key != "_id"}
        )

    async def update_ai_settings(
        self,
        *,
        intercept_mode: bool,
        provider_order: list[str],
        expected_version: int,
        actor_id: str,
    ) -> AIGatewaySettingsView:
        document = await self.ai_settings.find_one_and_update(
            {"_id": "global", "version": expected_version},
            {
                "$set": {
                    "interceptMode": intercept_mode,
                    "providerOrder": provider_order,
                    "updatedAt": utc_now(),
                    "updatedBy": actor_id,
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise ConcurrencyConflictError("global")
        return AIGatewaySettingsView.model_validate(
            {key: value for key, value in document.items() if key != "_id"}
        )

    async def append_audit(
        self,
        *,
        action: str,
        actor: str,
        target: str,
        details: dict[str, Any],
    ) -> None:
        await self._db["audit"].insert_one(
            {
                "_id": str(uuid.uuid4()),
                "action": action,
                "actor": actor,
                "target": target,
                "timestamp": utc_now(),
                "details": details,
            }
        )

    async def heartbeat(self, worker_name: str, instance_id: str, *, ttl_seconds: int) -> None:
        now = utc_now()
        await self.worker_heartbeats.update_one(
            {"_id": worker_name},
            {
                "$set": {
                    "instanceId": instance_id,
                    "lastSeenAt": now,
                    "expiresAt": now + timedelta(seconds=ttl_seconds * 3),
                }
            },
            upsert=True,
        )

    async def get_heartbeat(self, worker_name: str) -> dict[str, Any] | None:
        document = await self.worker_heartbeats.find_one({"_id": worker_name})
        return None if document is None else cast(dict[str, Any], document)

    async def source_order(self, order_reference: str) -> dict[str, Any] | None:
        sales = await self.source_dataset(SOURCE_SALES_DATASET)
        sales_inventory = await sales.find_one({"salesHdrEventData.orderId": order_reference})
        if sales_inventory is not None:
            raw = cast(dict[str, Any], sales_inventory)
            header_event = raw.get("salesHdrEventData")
            header = raw.get("salesHdr")
            header_event = header_event if isinstance(header_event, dict) else {}
            header = header if isinstance(header, dict) else {}
            header_data = header.get("salesHdrData")
            header_data = header_data if isinstance(header_data, dict) else {}
            items: list[dict[str, Any]] = []
            for sales_line in raw.get("salesLines", []):
                if not isinstance(sales_line, dict):
                    continue
                line_data = sales_line.get("lineData")
                if not isinstance(line_data, dict):
                    continue
                line_reference = str(
                    line_data.get("orderLineId") or f"{order_reference}:LINE:{len(items) + 1}"
                )
                items.append(
                    {
                        "itemReference": line_reference,
                        "productReference": str(
                            line_data.get("productId") or line_data.get("sku") or ""
                        ),
                        "productType": str(line_data.get("productType") or "STANDARD"),
                        "description": str(line_data.get("productDesc") or ""),
                        "orderedQuantity": int(line_data.get("orderQty") or 0),
                        "shippedQuantity": int(line_data.get("shipQty") or 0),
                    }
                )
            return {
                "_id": order_reference,
                "orderReference": order_reference,
                "customerReference": str(header_data.get("custId") or ""),
                "customerName": str(header_data.get("custName") or ""),
                "status": str(header_event.get("orderStatus") or "UNKNOWN"),
                "sellingWarehouseReference": str(header_event.get("sellWhseId") or ""),
                "shipFromWarehouseReference": str(header_event.get("shipFromWhseId") or ""),
                "deliveredAt": raw.get("deliveredAt"),
                "items": items,
                "sourceAssetId": "SOURCE_MONGODB_SALES_INV",
                "sourceDocumentReference": str(raw.get("_id") or order_reference),
            }

        # Transitional fallback for existing sandbox fixtures. New flows must
        # seed the configured sales dataset.
        document = await self._source_db[SOURCE_ORDERS].find_one({"_id": order_reference})
        return None if document is None else cast(dict[str, Any], document)

    async def seed_status(self) -> SeedStatusView:
        seed_version = self._settings.seed_version
        metadata = await self.seed_metadata.find_one({"_id": seed_version})
        raw_record_limit = metadata.get("recordLimit") if metadata is not None else None
        record_limit = raw_record_limit if isinstance(raw_record_limit, int) else None
        expected_counts_by_asset = effective_seed_counts(record_limit)
        expected_digest = manifest_digest(
            seed_version,
            self._settings.validation_fingerprint_key.get_secret_value(),
            record_limit,
        )
        seeded_query = {"seedVersion": seed_version, "seedDigest": expected_digest}
        # A dataset configuration cannot place is reported, not raised. This is
        # the readiness card: a schema that names none of the four is exactly
        # when an operator needs the diagnostics page to render and say so, and
        # a 500 here would take the whole card list down. The seed *write* paths
        # keep raising, because there the cost of guessing is data.
        domain_counts: dict[str, int] = {}
        unresolved: list[str] = []
        for dataset in DOMAIN_SOURCE_DATASETS:
            try:
                collection = await self.source_dataset(dataset)
            except SourceDatasetUnresolvedError as error:
                domain_counts[dataset] = 0
                unresolved.append(f"{error}.")
                continue
            domain_counts[dataset] = await collection.count_documents(seeded_query)
        counts = {
            "sourceCustomers": await self._source_db[SOURCE_CUSTOMERS].count_documents({}),
            "sourceOrders": await self._source_db[SOURCE_ORDERS].count_documents({}),
            "sourceProducts": await self._source_db[SOURCE_PRODUCTS].count_documents({}),
            "seededCustomers": await self._source_db[SOURCE_CUSTOMERS].count_documents(
                seeded_query
            ),
            "seededOrders": await self._source_db[SOURCE_ORDERS].count_documents(seeded_query),
            "seededProducts": await self._source_db[SOURCE_PRODUCTS].count_documents(seeded_query),
            # Reported per dataset, not per collection. The readiness card names
            # what an operator can act on: "source_sales expected 1000, found 0"
            # stays true through a rebinding, where "salesInv ..." would name a
            # collection the platform had already been told to stop reading.
            **domain_counts,
            "returns": await self.returns.count_documents({}),
            "completedReturns": await self.returns.count_documents(
                {"status": ReturnStatus.COMPLETED.value}
            ),
            "supportCases": await self.support_cases.count_documents({}),
            "aiTraces": await self.ai_traces.count_documents({}),
        }
        counts["customers"] = counts["seededCustomers"]
        counts["orders"] = counts["seededOrders"]
        counts["products"] = counts["seededProducts"]
        counts["shipments"] = counts[SOURCE_SHIPMENTS_DATASET]
        expected_counts = {
            "seededCustomers": expected_counts_by_asset["customers"],
            "seededOrders": expected_counts_by_asset["orders"],
            "seededProducts": expected_counts_by_asset["products"],
            SOURCE_SALES_DATASET: expected_counts_by_asset["orders"],
            SOURCE_CUSTOMERS_DATASET: expected_counts_by_asset["customers"],
            SOURCE_SHIPMENTS_DATASET: expected_counts_by_asset["orders"],
            SOURCE_PRODUCTS_DATASET: expected_counts_by_asset["products"],
            "returns": 0,
            "supportCases": 0,
        }
        errors = unresolved + [
            f"{name} expected {expected}, found {counts[name]}."
            for name, expected in expected_counts.items()
            if counts[name] != expected
        ]
        metadata_digest = str(metadata.get("digest", "")) if metadata is not None else ""
        if metadata_digest != expected_digest:
            errors.append(
                "Seed metadata digest is absent or does not match the canonical manifest."
            )
        applied_at = metadata.get("appliedAt") if metadata is not None else None
        applied_by = metadata.get("appliedBy") if metadata is not None else None
        return SeedStatusView(
            version=seed_version,
            digest=metadata_digest,
            appliedAt=cast(datetime | None, applied_at),
            appliedBy=cast(str | None, applied_by),
            ready=not errors,
            counts=counts,
            scenarioCounts=scenario_counts(),
            validationErrors=errors,
            requestedRecordLimit=record_limit,
        )

    async def apply_seed(
        self,
        *,
        actor_id: str,
        record_limit: int,
        cancel_check: Callable[[], None],
        progress: Callable[[int, str], Awaitable[None]],
    ) -> SeedStatusView:
        if self._settings.environment not in {"development", "test"}:
            raise PermissionError(
                "Deterministic source seed apply is restricted to development and test."
            )
        now = utc_now()
        seed_version = self._settings.seed_version
        evidence_hmac_key = self._settings.validation_fingerprint_key.get_secret_value()
        digest = manifest_digest(seed_version, evidence_hmac_key, record_limit)
        customers, products, orders = materialize_seed(
            seed_version,
            now,
            evidence_hmac_key,
            record_limit,
        )
        domain_records = materialize_domain_seed(
            seed_version,
            now,
            evidence_hmac_key,
            record_limit,
        )
        domain_targets = [
            (await self.source_dataset(dataset), records)
            for dataset, records in domain_records.items()
        ]
        for collection, documents in (
            (self._source_db[SOURCE_CUSTOMERS], customers),
            (self._source_db[SOURCE_PRODUCTS], products),
            (self._source_db[SOURCE_ORDERS], orders),
            *domain_targets,
        ):
            cancel_check()
            await collection.delete_many({"seedVersion": seed_version})
            for offset in range(0, len(documents), 1_000):
                cancel_check()
                operations = [
                    ReplaceOne({"_id": document["_id"]}, document, upsert=True)
                    for document in documents[offset : offset + 1_000]
                ]
                if operations:
                    await collection.bulk_write(operations, ordered=False)
                    await progress(
                        len(operations),
                        f"Writing {collection.name}",
                    )

        # Seed readiness begins before a demo return or support case is created.
        await self.returns.delete_many({"seedVersion": seed_version})
        await self.events.delete_many({"seedVersion": seed_version})
        await self.support_cases.delete_many({"seedVersion": seed_version})
        for dataset, index_specs in _DOMAIN_SOURCE_INDEXES.items():
            collection = await self.source_dataset(dataset)
            for key, index_name, unique in index_specs:
                await collection.create_index(key, unique=unique, name=index_name)
        await self.seed_metadata.replace_one(
            {"_id": seed_version},
            {
                "_id": seed_version,
                "digest": digest,
                "appliedAt": now,
                "appliedBy": actor_id,
                "scenarioCounts": scenario_counts(),
                "recordLimit": record_limit,
            },
            upsert=True,
        )
        return await self.seed_status()

    async def delete_seed_data(self) -> None:
        """Delete only records owned by the active deterministic seed version."""
        seed_version = self._settings.seed_version
        source_cleanup = {"seedVersion": seed_version}
        await self._source_db[SOURCE_CUSTOMERS].delete_many(source_cleanup)
        await self._source_db[SOURCE_PRODUCTS].delete_many(source_cleanup)
        await self._source_db[SOURCE_ORDERS].delete_many(source_cleanup)
        for dataset in DOMAIN_SOURCE_DATASETS:
            await (await self.source_dataset(dataset)).delete_many(source_cleanup)
        await self.seed_metadata.delete_many({"_id": seed_version})
        await self.returns.delete_many(source_cleanup)
        await self.events.delete_many(source_cleanup)
        await self.support_cases.delete_many(source_cleanup)

    async def reset_demo_data(self) -> None:
        seed_version = self._settings.seed_version
        source_cleanup = {"seedVersion": seed_version}
        await self._source_db[SOURCE_CUSTOMERS].delete_many(source_cleanup)
        await self._source_db[SOURCE_PRODUCTS].delete_many(source_cleanup)
        await self._source_db[SOURCE_ORDERS].delete_many(source_cleanup)
        for dataset in DOMAIN_SOURCE_DATASETS:
            await (await self.source_dataset(dataset)).delete_many(source_cleanup)
        await self.seed_metadata.delete_many({"_id": seed_version})
        await self.returns.delete_many({})
        await self.events.delete_many({})
        await self.support_cases.delete_many({})
        await self.ai_traces.delete_many({})
        await self.ai_rate_limits.delete_many({})
        await self.worker_heartbeats.delete_many({})
        await self._db["return_sessions"].delete_many({})
        await self._db["return_session_audit_events"].delete_many({})
        await self._db["return_session_outbox_events"].delete_many({})
        await self._db["return_session_agent_decisions"].delete_many({})
        await self._db["associate_conversations"].delete_many({})
        await self._db["discovery_locks"].delete_many({})
        await self._db["feedback_learning_records"].delete_many({})


def resolve_operational_repository(request: Request) -> OperationalRepository:
    resources = getattr(request.app.state, "resources", None)
    settings = getattr(request.app.state, "settings", None)
    if (
        not isinstance(resources, RuntimeResources)
        or resources.mongo is None
        or not isinstance(settings, Settings)
    ):
        raise HTTPException(status_code=503, detail="Platform MongoDB is unavailable")
    return OperationalRepository(resources.mongo, settings, resources.source_mongo)
