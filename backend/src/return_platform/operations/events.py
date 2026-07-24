"""Durable outbox publication and resumable SSE delivery."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import Request

from return_platform.operations.models import TimelineEvent
from return_platform.operations.repository import OperationalRepository
from return_platform.resources import AsyncValkeyClient


def stream_key(stream_id: str) -> str:
    return f"return-platform:events:{stream_id}"


def encode_event(event: TimelineEvent) -> dict[str, str]:
    return {
        "event_id": event.id,
        "stream_id": event.streamId,
        "sequence": str(event.sequence),
        "event_type": event.eventType,
        "actor_type": event.actorType,
        "actor_id": event.actorId,
        "payload": json.dumps(event.payload, separators=(",", ":"), sort_keys=True),
        "occurred_at": event.occurredAt.isoformat(),
    }


async def publish_event(
    valkey: AsyncValkeyClient | None,
    repository: OperationalRepository,
    event: TimelineEvent,
    *,
    maxlen: int,
) -> bool:
    if valkey is None:
        return False
    try:
        await valkey.xadd(
            stream_key(event.streamId),
            encode_event(event),
            id=f"{event.sequence}-0",
            maxlen=maxlen,
            approximate=True,
        )
    except Exception:
        return False
    await repository.mark_event_published(event.id)
    return True


async def flush_outbox(
    valkey: AsyncValkeyClient | None,
    repository: OperationalRepository,
    *,
    maxlen: int,
    batch_size: int = 100,
) -> int:
    published = 0
    for event in await repository.list_unpublished_events(batch_size):
        if await publish_event(valkey, repository, event, maxlen=maxlen):
            published += 1
        else:
            break
    return published


def sse_message(event: TimelineEvent) -> bytes:
    payload = event.model_dump(mode="json")
    return (
        f"id: {event.sequence}\n"
        "event: return-event\n"
        f"data: {json.dumps(payload, separators=(',', ':'), sort_keys=True)}\n\n"
    ).encode()


async def event_stream(
    request: Request,
    repository: OperationalRepository,
    valkey: AsyncValkeyClient | None,
    *,
    stream_id: str,
    after_sequence: int,
    replay_limit: int,
    heartbeat_seconds: float,
) -> AsyncIterator[bytes]:
    """Replay from MongoDB, then tail Valkey with a MongoDB fallback."""
    cursor = after_sequence
    replay = await repository.list_events(stream_id, after_sequence=cursor, limit=replay_limit)
    for event in replay:
        cursor = event.sequence
        yield sse_message(event)

    yield b": connected\n\n"
    while not await request.is_disconnected():
        delivered = False
        if valkey is not None:
            try:
                rows = await valkey.xread(
                    {stream_key(stream_id): f"{cursor}-0"},
                    count=100,
                    block=max(1, int(heartbeat_seconds * 1000)),
                )
                for _key, messages in rows:
                    for _redis_id, fields in messages:
                        sequence = int(fields["sequence"])
                        if sequence <= cursor:
                            continue
                        events = await repository.list_events(
                            stream_id,
                            after_sequence=cursor,
                            limit=min(replay_limit, 100),
                        )
                        for event in events:
                            cursor = event.sequence
                            delivered = True
                            yield sse_message(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                valkey = None

        if valkey is None:
            events = await repository.list_events(
                stream_id,
                after_sequence=cursor,
                limit=min(replay_limit, 100),
            )
            for event in events:
                cursor = event.sequence
                delivered = True
                yield sse_message(event)
            if not delivered:
                await asyncio.sleep(min(heartbeat_seconds, 2.0))

        if not delivered:
            yield b": heartbeat\n\n"
