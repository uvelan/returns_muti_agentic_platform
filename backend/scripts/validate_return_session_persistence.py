"""Run a safe live MongoDB transaction validation for ReturnSession persistence."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pymongo import AsyncMongoClient

from return_platform.canonical.operations import (
    AuditEvent,
    ConfigurationVersionBinding,
    ContextSnapshot,
    ReturnSession,
    WorkflowStage,
)
from return_platform.workflows.persistence import (
    ReturnSessionOutboxEvent,
    ReturnSessionPersistenceStatus,
    ReturnSessionRepository,
    ReturnSessionTransition,
)

_DATABASE = "return_session_live_validation"
_SESSION_ID = UUID("8ad79a12-da57-4f05-ae96-f89e730f66ba")
_CORRELATION_ID = UUID("47f0d221-f510-4525-95b6-9f32613aa634")
_COMMAND_ID = UUID("c93c88b8-95b6-4017-b2cf-45f1c478c02e")
_CREATED_AT = datetime(2026, 7, 22, 9, 30, tzinfo=UTC)


async def _validate() -> None:
    dsn = os.environ.get("PLATFORM_MONGO_DSN")
    if dsn is None:
        raise RuntimeError("PLATFORM_MONGO_DSN is required")
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(dsn)
    repository = ReturnSessionRepository.from_client(
        client,
        database=_DATABASE,
        sessions_collection="return_sessions",
        audits_collection="audit_events",
        outbox_collection="outbox_events",
        decisions_collection="agent_decisions",
        operation_timeout_seconds=30.0,
    )
    database = client[_DATABASE]
    await client.drop_database(_DATABASE)
    await database.create_collection("return_sessions")
    await database.create_collection("audit_events")
    await database.create_collection("outbox_events")
    await database.create_collection("agent_decisions")
    try:
        session = ReturnSession(
            session_id=_SESSION_ID,
            current_stage=WorkflowStage.INTAKE,
            status="RUNNING",
            workflow_id="live-validation-workflow",
            workflow_run_id="live-validation-run",
            configuration_versions=(
                ConfigurationVersionBinding(component="workflow", version="return-v1"),
            ),
            created_at=_CREATED_AT,
            updated_at=_CREATED_AT,
        )
        create_audit = AuditEvent(
            audit_event_id=UUID(int=101),
            session_id=_SESSION_ID,
            correlation_id=_CORRELATION_ID,
            actor_type="VALIDATION",
            actor_id="return-session-live-validation",
            operation="CREATE_RETURN_SESSION",
            entity_type="ReturnSession",
            entity_key=f"RETURN_SESSION:{_SESSION_ID}",
            after_summary="Stage INTAKE revision 0",
            outcome="SUCCESS",
            occurred_at=_CREATED_AT,
        )
        create_outbox = ReturnSessionOutboxEvent(
            event_id=UUID(int=102),
            session_id=_SESSION_ID,
            command_id=None,
            event_type="RETURN_SESSION_CREATED",
            revision=0,
            current_stage=WorkflowStage.INTAKE,
            status="RUNNING",
            occurred_at=_CREATED_AT,
        )
        created = await repository.initialize(session, create_audit, create_outbox)
        replayed = await repository.initialize(session, create_audit, create_outbox)
        transitioned_at = _CREATED_AT + timedelta(minutes=1)
        transition = ReturnSessionTransition(
            command_id=_COMMAND_ID,
            expected_revision=0,
            completed_stage=WorkflowStage.INTAKE,
            resulting_stage=WorkflowStage.ORDER_DISCOVERY,
            context_snapshot=ContextSnapshot.from_mapping(
                schema_version="intake-v1",
                payload={
                    "channel": "SYSTEM",
                    "request_reference": "LIVE-VALIDATION-REQUEST",
                },
            ),
            updated_at=transitioned_at,
            audit_event=AuditEvent(
                audit_event_id=UUID(int=103),
                session_id=_SESSION_ID,
                correlation_id=_CORRELATION_ID,
                actor_type="VALIDATION",
                actor_id="return-session-live-validation",
                operation="TRANSITION_RETURN_SESSION_STAGE",
                entity_type="ReturnSession",
                entity_key=f"RETURN_SESSION:{_SESSION_ID}",
                before_summary="Stage INTAKE revision 0",
                after_summary="Stage ORDER_DISCOVERY revision 1",
                outcome="SUCCESS",
                occurred_at=transitioned_at,
            ),
            outbox_event=ReturnSessionOutboxEvent(
                event_id=UUID(int=104),
                session_id=_SESSION_ID,
                command_id=_COMMAND_ID,
                event_type="RETURN_SESSION_STAGE_TRANSITIONED",
                revision=1,
                current_stage=WorkflowStage.ORDER_DISCOVERY,
                status="RUNNING",
                occurred_at=transitioned_at,
            ),
        )
        transitioned = await repository.transition(_SESSION_ID, transition)
        transition_replay = await repository.transition(_SESSION_ID, transition)
        counts = (
            await database["return_sessions"].count_documents({}),
            await database["audit_events"].count_documents({}),
            await database["outbox_events"].count_documents({}),
        )
        expected = (
            ReturnSessionPersistenceStatus.CREATED,
            ReturnSessionPersistenceStatus.ALREADY_PRESENT,
            ReturnSessionPersistenceStatus.TRANSITIONED,
            ReturnSessionPersistenceStatus.ALREADY_APPLIED,
        )
        observed = (
            created.status,
            replayed.status,
            transitioned.status,
            transition_replay.status,
        )
        if observed != expected or counts != (1, 2, 2):
            raise RuntimeError("ReturnSession live validation failed")
        print("ReturnSession live MongoDB transaction validation: PASS")
        print("Documents: sessions=1 audit_events=2 outbox_events=2")
        print("Idempotent create and transition replay: PASS")
    finally:
        await client.drop_database(_DATABASE)
        await client.close()


if __name__ == "__main__":
    asyncio.run(_validate())
