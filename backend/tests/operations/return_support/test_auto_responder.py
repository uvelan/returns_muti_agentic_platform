"""The auto responder acknowledges, posts to the thread, and records once."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from return_platform.agents.support_response import SupportResponseAgent
from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.operations.return_support.auto_responder import (
    SupportAutoResponder,
    support_event_id_for,
)
from return_platform.operations.return_support.service import (
    SupportMessageView,
    SupportWorkItemStatus,
    SupportWorkItemView,
)

CONFIG = Path(__file__).resolve().parents[3] / "config" / "returns" / "production.yaml"

_NOW = datetime(2026, 8, 25, tzinfo=UTC)


def _work_item(status: SupportWorkItemStatus, *, case_id: str | None = "case-42") -> dict[str, Any]:
    return dict(
        id="wi-1",
        caseId=case_id,
        sessionId=None if case_id else "session-1",
        threadId="thread-1",
        status=status,
        priority="NORMAL",
        queue="RETURNS_SUPPORT",
        subject="Return CQ800002",
        requestSnapshotDigest="0" * 64,
        slaDueAt=_NOW,
        version=0,
        createdAt=_NOW,
        updatedAt=_NOW,
    )


def _handoff_message() -> SupportMessageView:
    return SupportMessageView(
        id="msg-1",
        threadId="thread-1",
        sequence=1,
        senderRole="AGENT",
        senderId="return-workflow-agent",
        messageType="REQUEST",
        messageText="RETURN SUPPORT REQUEST ...",
        businessPayload={
            "schemaVersion": "support-handoff-v1",
            "caseId": "case-42",
            "customer": {"name": "Northgate Plumbing"},
            "order": {
                "reference": "CQ800002",
                "items": [
                    {
                        "lineReference": "10",
                        "productName": "Chrome Faucet",
                        "sku": "F-100",
                        "quantity": 2,
                        "reason": "DAMAGED",
                        "condition": "Damaged",
                    }
                ],
            },
            "returnDetails": {"method": "PREPAID_PARCEL"},
            "bayAssignment": {"bayReference": "BAY-7", "returnLocation": None},
        },
        createdAt=_NOW,
    )


class FakeService:
    def __init__(self, item: dict[str, Any], messages: list[SupportMessageView]) -> None:
        self._item = item
        self._messages = messages
        self.actions: list[str] = []
        self.posted: list[tuple[str, dict[str, Any]]] = []

    async def get_work_item(self, work_item_id: str) -> SupportWorkItemView | None:
        return SupportWorkItemView.model_validate(self._item)

    async def list_messages(self, thread_id: str) -> list[SupportMessageView]:
        return list(self._messages)

    async def apply_action(self, work_item_id, request, *, actor_id):
        self.actions.append(request.action.value)
        self._item["status"] = SupportWorkItemStatus.ACKNOWLEDGED
        self._item["version"] += 1
        return SupportWorkItemView.model_validate(self._item)

    async def add_message(self, work_item_id, request, *, actor_id, actor_role):
        self.posted.append((request.messageType.value, request.businessPayload))
        self._item["version"] += 1
        item = SupportWorkItemView.model_validate(self._item)
        message = SupportMessageView(
            id="msg-agent",
            threadId="thread-1",
            sequence=len(self._messages) + 1,
            senderRole=actor_role,
            senderId=actor_id,
            messageType=request.messageType,
            messageText=request.messageText,
            businessPayload=request.businessPayload,
            createdAt=_NOW,
        )
        self._messages.append(message)
        return item, message


class FakeEventStore:
    def __init__(self) -> None:
        self.recorded: list[dict[str, Any]] = []

    async def record_support_response(self, **kwargs: Any):
        self.recorded.append(kwargs)

        class Receipt:
            support_event_id = kwargs["support_event_id"]
            duplicate = False

        return Receipt()


def _responder(service: FakeService, store: FakeEventStore) -> SupportAutoResponder:
    configuration = load_return_configuration(CONFIG).configuration
    return SupportAutoResponder(
        service=service,  # type: ignore[arg-type]
        event_store=store,  # type: ignore[arg-type]
        agent=SupportResponseAgent(configuration),
    )


@pytest.mark.asyncio
async def test_complete_handoff_is_acknowledged_posted_and_recorded() -> None:
    service = FakeService(_work_item(SupportWorkItemStatus.NEW), [_handoff_message()])
    store = FakeEventStore()
    outcome = await _responder(service, store).respond("wi-1")

    assert outcome.outcome == "RESPONDED"
    assert outcome.returnReference == "RMA-CASE42"
    assert service.actions == ["ACKNOWLEDGE"]
    assert [kind for kind, _ in service.posted] == ["RETURN_CREATION"]
    assert len(store.recorded) == 1
    record = store.recorded[0]["records"][0]
    assert record["return_reference"] == "RMA-CASE42"
    assert record["tracking_reference"] is not None
    assert record["label_reference"] is not None
    assert store.recorded[0]["support_event_id"] == support_event_id_for("wi-1")


@pytest.mark.asyncio
async def test_missing_method_asks_on_the_thread_and_records_nothing() -> None:
    message = _handoff_message()
    message.businessPayload["returnDetails"]["method"] = None
    service = FakeService(_work_item(SupportWorkItemStatus.NEW), [message])
    store = FakeEventStore()
    outcome = await _responder(service, store).respond("wi-1")

    assert outcome.outcome == "CLARIFICATION_REQUESTED"
    assert "return_method" in outcome.missingFields
    assert [kind for kind, _ in service.posted] == ["CLARIFICATION_REQUEST"]
    assert store.recorded == []


@pytest.mark.asyncio
async def test_session_thread_is_left_to_the_human_queue() -> None:
    service = FakeService(_work_item(SupportWorkItemStatus.NEW, case_id=None), [_handoff_message()])
    store = FakeEventStore()
    outcome = await _responder(service, store).respond("wi-1")
    assert outcome.outcome == "SKIPPED_NO_CASE"
    assert service.posted == []
    assert store.recorded == []


@pytest.mark.asyncio
async def test_a_rerun_does_not_post_the_message_twice() -> None:
    service = FakeService(_work_item(SupportWorkItemStatus.NEW), [_handoff_message()])
    store = FakeEventStore()
    responder = _responder(service, store)
    await responder.respond("wi-1")
    await responder.respond("wi-1")

    assert [kind for kind, _ in service.posted] == ["RETURN_CREATION"]
    # The outcome recording is repeated, and that is fine: the store is
    # idempotent on support_event_id, which both runs share.
    assert {entry["support_event_id"] for entry in store.recorded} == {support_event_id_for("wi-1")}


@pytest.mark.asyncio
async def test_progressed_work_is_not_talked_over() -> None:
    service = FakeService(_work_item(SupportWorkItemStatus.RETURN_CREATED), [_handoff_message()])
    store = FakeEventStore()
    outcome = await _responder(service, store).respond("wi-1")
    assert outcome.outcome == "SKIPPED_STATUS"
    assert service.posted == []
    assert store.recorded == []
