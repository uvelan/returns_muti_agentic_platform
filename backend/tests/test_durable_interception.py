"""Durable interception, and the two properties that make it safe.

Phase 14 replaces `ManualFileProvider`'s `.manual_llm/` handoff -- JSON written
relative to the process CWD, polled out of a sibling directory. That mechanism
loses every in-flight request on restart, is invisible to a second replica,
requires filesystem access to answer, and records nothing about who answered.

Beyond durability the plan names two rules, and both are asserted here:

* **"Never misattribute human output to a model provider."** A trace that
  recorded a human's text as Gemini's would corrupt any evaluation set built
  from it, silently and permanently.
* **"Manual response must pass the same response contract/safety validation as
  a model response."** A human typing into an operator console is at least as
  able to produce something malformed as a model is, so a shortcut past
  validation would put the one unchecked payload in the system on the most
  privileged path.

The store's sealing and its compare-and-set are proved against real Mongo in
`test_durable_interception_real_infra.py`; a dict double cannot demonstrate
either.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from return_platform.ai.interception.records import (
    Interception,
    InterceptionStatus,
    ResumeCommand,
    is_terminal,
)
from return_platform.ai.providers.contracts import ProviderError, ProviderRequest
from return_platform.ai.providers.durable_interception import DurableInterceptionProvider
from return_platform.ai.providers.manual import ManualFileProvider
from return_platform.configuration.settings import Settings

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def _settings(environment: str = "test") -> Settings:
    return Settings.model_construct(environment=environment)


def _request() -> ProviderRequest:
    return ProviderRequest(
        system_prompt="You are a careful assistant.",
        user_payload={"mode": "DECIDE", "contextJson": "{}"},
        temperature=0.0,
        max_output_tokens=1024,
    )


class _Store:
    """In-memory InterceptionStore with the same status semantics."""

    def __init__(self) -> None:
        self.records: dict[str, Interception] = {}
        self.payloads: dict[str, dict[str, object]] = {}

    async def open(
        self,
        *,
        interception_id: str,
        task_id: str,
        request_payload: dict[str, object],
        resume: ResumeCommand,
        expires_at: datetime,
    ) -> Interception:
        record = Interception(
            interception_id=interception_id,
            task_id=task_id,
            status=InterceptionStatus.PENDING,
            resume=resume,
            created_at=NOW,
            expires_at=expires_at,
        )
        self.records[interception_id] = record
        self.payloads[interception_id] = dict(request_payload)
        return record

    async def get(self, interception_id: str) -> Interception | None:
        return self.records.get(interception_id)

    async def request_payload(self, interception_id: str) -> dict[str, object] | None:
        return self.payloads.get(interception_id)

    async def answer(
        self, *, interception_id: str, response_text: str, answered_by: str
    ) -> Interception:
        record = self.records[interception_id]
        updated = Interception(
            interception_id=record.interception_id,
            task_id=record.task_id,
            status=InterceptionStatus.ANSWERED,
            resume=record.resume,
            created_at=record.created_at,
            expires_at=record.expires_at,
            answered_at=NOW,
            answered_by=answered_by,
            response_text=response_text,
        )
        self.records[interception_id] = updated
        self.payloads[interception_id]["responseText"] = response_text
        return updated

    async def cancel(self, *, interception_id: str, status: InterceptionStatus) -> None:
        record = self.records[interception_id]
        if record.status is not InterceptionStatus.PENDING:
            return
        self.records[interception_id] = Interception(
            interception_id=record.interception_id,
            task_id=record.task_id,
            status=status,
            resume=record.resume,
            created_at=record.created_at,
            expires_at=record.expires_at,
        )


async def _answer_first_pending(store: _Store, text: str, *, by: str = "operator-1") -> str:
    for _ in range(200):
        pending = [i for i, r in store.records.items() if r.status is InterceptionStatus.PENDING]
        if pending:
            await store.answer(interception_id=pending[0], response_text=text, answered_by=by)
            return pending[0]
        await asyncio.sleep(0.01)
    raise AssertionError("no interception was ever opened")


# --- the handoff ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_held_request_is_answered_from_the_store() -> None:
    store = _Store()
    provider = DurableInterceptionProvider(
        _settings(), store, task_id="ORDER_AGENT_REASONING_V1", poll_seconds=0.01
    )

    answering = asyncio.create_task(_answer_first_pending(store, '{"ok": true}'))
    response = await provider.generate(_request())
    interception_id = await answering

    assert response.text == '{"ok": true}'
    assert store.records[interception_id].status is InterceptionStatus.ANSWERED


@pytest.mark.asyncio
async def test_the_resume_command_is_written_with_the_request_not_after_it() -> None:
    """The plan requires the interception and its resume command to be
    persisted atomically. They are one document, so a crash between "held the
    request" and "recorded how to resume it" is not representable."""
    store = _Store()
    resume = ResumeCommand(run_id="run-1", thread_id="thread-1", workflow_id="wf-1")
    provider = DurableInterceptionProvider(
        _settings(), store, task_id="T", resume=resume, poll_seconds=0.01, timeout_seconds=0.05
    )

    with pytest.raises(ProviderError):
        await provider.generate(_request())

    (record,) = store.records.values()
    assert record.resume == resume


@pytest.mark.asyncio
async def test_the_full_prompt_is_held_so_a_human_can_answer_it() -> None:
    store = _Store()
    provider = DurableInterceptionProvider(
        _settings(), store, poll_seconds=0.01, timeout_seconds=0.05
    )

    with pytest.raises(ProviderError):
        await provider.generate(_request())

    (payload,) = store.payloads.values()
    assert payload["systemPrompt"] == "You are a careful assistant."
    assert payload["userPayload"] == {"mode": "DECIDE", "contextJson": "{}"}


# --- misattribution ---------------------------------------------------------


def test_a_human_answer_is_never_attributed_to_a_model_provider() -> None:
    """The identity is the whole point: an evaluation set built from traces must
    not silently contain text a person wrote."""
    assert DurableInterceptionProvider.name == "MANUAL"
    assert DurableInterceptionProvider.model == "manual-human-v1"
    # The replacement reports exactly what the provider it replaces reported, so
    # existing traces and route configuration keep the same meaning.
    assert DurableInterceptionProvider.name == ManualFileProvider.name
    assert DurableInterceptionProvider.model == ManualFileProvider.model


@pytest.mark.asyncio
async def test_the_answering_human_is_recorded() -> None:
    """ "A human answered" is not enough for an audit -- *which* human is what
    makes the record accountable."""
    store = _Store()
    provider = DurableInterceptionProvider(_settings(), store, poll_seconds=0.01)

    answering = asyncio.create_task(_answer_first_pending(store, "{}", by="alex@example.com"))
    await provider.generate(_request())
    interception_id = await answering

    assert store.records[interception_id].answered_by == "alex@example.com"


# --- the response is not trusted more than a model's ------------------------


@pytest.mark.asyncio
async def test_a_human_answer_is_returned_unvalidated_for_the_shared_path_to_check() -> None:
    """The provider must not parse, sanitise, or bless the text. It returns a
    plain ProviderResponse so the invoker's schema parse and `inspect_output`
    see a human answer exactly as they see a model's -- if the provider
    validated here, that would be a second, weaker validation path."""
    store = _Store()
    provider = DurableInterceptionProvider(_settings(), store, poll_seconds=0.01)

    malformed = "not json at all { <script>"
    answering = asyncio.create_task(_answer_first_pending(store, malformed))
    response = await provider.generate(_request())
    await answering

    # Returned verbatim: rejecting it is the shared path's job, not the
    # provider's, and doing it here would mean two different validations.
    assert response.text == malformed
    assert response.provider == "MANUAL"


@pytest.mark.asyncio
async def test_an_empty_answer_is_a_provider_error_not_an_empty_success() -> None:
    """An empty string would parse as "the model returned nothing" and could be
    mistaken for a legitimate empty result downstream."""
    store = _Store()
    provider = DurableInterceptionProvider(
        _settings(), store, poll_seconds=0.01, timeout_seconds=2.0
    )

    answering = asyncio.create_task(_answer_first_pending(store, ""))
    with pytest.raises(ProviderError) as caught:
        await provider.generate(_request())
    await answering
    assert caught.value.code == "INTERCEPTION_EMPTY"


# --- lifecycle --------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cannot_use_a_human_in_the_loop_provider() -> None:
    provider = DurableInterceptionProvider(_settings("production"), _Store())
    assert provider.configured is False
    with pytest.raises(ProviderError) as caught:
        await provider.generate(_request())
    assert caught.value.code == "POLICY_BLOCKED"


@pytest.mark.asyncio
async def test_a_timed_out_request_closes_its_record_rather_than_leaving_it_pending() -> None:
    """A PENDING record whose caller has given up shows an operator work that
    will be discarded the moment they finish it."""
    store = _Store()
    provider = DurableInterceptionProvider(
        _settings(), store, poll_seconds=0.01, timeout_seconds=0.05
    )

    with pytest.raises(ProviderError) as caught:
        await provider.generate(_request())
    assert caught.value.code == "TIMEOUT"

    (record,) = store.records.values()
    assert record.status is InterceptionStatus.EXPIRED


@pytest.mark.asyncio
async def test_a_cancelled_interception_fails_the_call_instead_of_hanging() -> None:
    store = _Store()
    provider = DurableInterceptionProvider(
        _settings(), store, poll_seconds=0.01, timeout_seconds=5.0
    )

    async def _cancel_it() -> None:
        for _ in range(200):
            pending = [
                i for i, r in store.records.items() if r.status is InterceptionStatus.PENDING
            ]
            if pending:
                await store.cancel(interception_id=pending[0], status=InterceptionStatus.CANCELLED)
                return
            await asyncio.sleep(0.01)

    cancelling = asyncio.create_task(_cancel_it())
    with pytest.raises(ProviderError) as caught:
        await provider.generate(_request())
    await cancelling
    assert caught.value.code == "INTERCEPTION_CLOSED"


def test_expired_and_cancelled_are_both_terminal_but_distinct() -> None:
    """Collapsing them would hide a staffing problem -- nobody answered in time
    -- behind what reads as ordinary churn."""
    assert is_terminal(InterceptionStatus.EXPIRED)
    assert is_terminal(InterceptionStatus.CANCELLED)
    assert is_terminal(InterceptionStatus.ANSWERED)
    assert not is_terminal(InterceptionStatus.PENDING)
    assert InterceptionStatus.EXPIRED is not InterceptionStatus.CANCELLED


def test_the_record_has_nowhere_to_put_chain_of_thought() -> None:
    """Phase 14: "No hidden chain-of-thought stored or exposed." Enforced by the
    record's shape rather than by reviewer vigilance -- a frozen slots dataclass
    cannot grow a `reasoning` field by accident."""
    fields = set(Interception.__slots__)
    assert not {"reasoning", "thoughts", "deliberation", "chain_of_thought"} & fields
    interception = Interception(
        interception_id="i-1",
        task_id="T",
        status=InterceptionStatus.PENDING,
        resume=ResumeCommand(run_id="r", thread_id="t"),
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    with pytest.raises((AttributeError, TypeError)):
        interception.reasoning = "hidden"  # type: ignore[attr-defined]
