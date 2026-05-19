# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""REST + SSE routes for unified chat API.

POST /v1/chat                          — Send message, persist, start agent
POST /v1/chat/{conversation_id}/respond — Answer HITL prompt, persist, resume agent
GET  /v1/chat/{conversation_id}/events  — SSE event stream (conversation-scoped)
POST /v1/chat/{conversation_id}/cancel  — Cancel active processing
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import UTC
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pydantic import Field

from ..sessions.auth import get_current_user_id
from ..sessions.store import ConversationStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/chat", tags=["chat"])

# Module-level singletons set by plugin.py during startup
_runner: Any | None = None
_conversation_store: ConversationStore | None = None
_db_url: str | None = None


def configure(runner: Any, conversation_store: ConversationStore | None, db_url: str) -> None:
    global _runner, _conversation_store, _db_url
    _runner = runner
    _conversation_store = conversation_store
    _db_url = db_url


def _get_runner():
    if _runner is None:
        raise HTTPException(status_code=503, detail="Chat runner not initialized")
    return _runner


def _get_store() -> ConversationStore:
    if _conversation_store is None:
        raise HTTPException(status_code=503, detail="Conversation store not initialized")
    return _conversation_store


# ── Request/Response Models ──────────────────────────────────────────


class ChatRequest(BaseModel):
    conversation_id: str | None = Field(default=None, max_length=64)
    message: str = Field(..., min_length=1, max_length=100_000)
    data_sources: list[str] | None = None
    report_context: str | None = Field(default=None, deprecated=True)


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    status: str = "processing"


class RespondRequest(BaseModel):
    response: str = Field(..., min_length=1, max_length=100_000)
    prompt_id: str | None = None


class StatusResponse(BaseModel):
    status: str


# ── Helpers ──────────────────────────────────────────────────────────


async def _verify_owner(store: ConversationStore, conversation_id: str, user_id: str) -> None:
    """Raise 404 if the conversation doesn't exist or isn't owned by this user."""
    if not await store.conversation_exists(conversation_id, user_id):
        raise HTTPException(status_code=404, detail="Conversation not found")


# ── Routes ───────────────────────────────────────────────────────────


@router.post("", response_model=ChatResponse, status_code=202)
async def send_message(
    request: ChatRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Send a user message. Persists it and starts agent processing."""
    runner = _get_runner()
    store = _get_store()

    conversation_id = request.conversation_id
    if not conversation_id:
        conversation_id = f"s_{uuid.uuid4().hex}"
        await store.create_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            title="New Session",
        )
        logger.info("Created conversation %s for user %s", conversation_id, user_id)
    else:
        await _verify_owner(store, conversation_id, user_id)

    message_id = uuid.uuid4().hex[:12]
    await store.append_messages(
        conversation_id,
        [
            {
                "id": message_id,
                "role": "user",
                "content": request.message,
                "message_type": "user",
                "created_at": datetime.now(UTC).isoformat(),
            }
        ],
    )

    if runner.is_active(conversation_id):
        raise HTTPException(status_code=409, detail="Conversation already has an active task")

    await runner.run(
        conversation_id=conversation_id,
        user_message=request.message,
        data_sources=request.data_sources,
    )

    return ChatResponse(
        conversation_id=conversation_id,
        message_id=message_id,
    )


@router.post("/{conversation_id}/respond", response_model=StatusResponse)
async def respond_to_interaction(
    conversation_id: str,
    request: RespondRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Respond to a HITL prompt. Persists and resumes agent."""
    runner = _get_runner()
    store = _get_store()

    await _verify_owner(store, conversation_id, user_id)

    message_id = uuid.uuid4().hex[:12]
    await store.append_messages(
        conversation_id,
        [
            {
                "id": message_id,
                "role": "user",
                "content": request.response,
                "message_type": "interaction_response",
                "created_at": datetime.now(UTC).isoformat(),
            }
        ],
    )

    resolved = runner.resolve_interaction(conversation_id, request.response)
    if not resolved:
        raise HTTPException(status_code=404, detail="No pending interaction for this conversation")

    return StatusResponse(status="ok")


@router.get("/{conversation_id}/events")
async def stream_events(
    conversation_id: str,
    last_event_id: int = Query(default=0, ge=0),
    user_id: str = Depends(get_current_user_id),
):
    """SSE stream of all events for a conversation. Stays open across turns."""
    if _db_url is None:
        raise HTTPException(status_code=503, detail="Event store not configured")

    store = _get_store()
    await _verify_owner(store, conversation_id, user_id)

    return StreamingResponse(
        _conversation_sse_generator(conversation_id, _db_url, last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{conversation_id}/cancel", response_model=StatusResponse)
async def cancel_processing(
    conversation_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Cancel active agent processing for a conversation."""
    runner = _get_runner()
    store = _get_store()

    await _verify_owner(store, conversation_id, user_id)

    cancelled = runner.cancel(conversation_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="No active task for this conversation")

    return StatusResponse(status="cancelled")


# ── SSE Generator ────────────────────────────────────────────────────


async def _conversation_sse_generator(
    conversation_id: str,
    db_url: str,
    start_event_id: int = 0,
):
    """SSE generator for conversation-scoped events.

    Unlike job SSE streams, this does NOT terminate on chat.complete — it
    stays open so the frontend receives events from subsequent turns and
    deep research escalations on the same connection.
    """
    from ..jobs import EventStore
    from ..jobs import get_connection_manager

    connection_manager = get_connection_manager()
    last_event_id = start_event_id
    sequence_id = start_event_id
    heartbeat_interval = 15
    poll_interval = 0.5

    def fmt(event_type: str, data: dict, event_id: int | None = None) -> str:
        nonlocal sequence_id
        if event_id is not None:
            sequence_id = event_id
        else:
            sequence_id += 1
        return f"id: {sequence_id}\nevent: {event_type}\ndata: {json.dumps(data)}\n\n"

    is_postgres = EventStore.is_postgres(db_url)
    yield fmt("stream.start", {"conversation_id": conversation_id, "mode": "pubsub" if is_postgres else "polling"})

    if is_postgres:
        async for event_str in _conversation_sse_postgres(
            conversation_id, db_url, last_event_id, fmt, connection_manager, heartbeat_interval
        ):
            yield event_str
    else:
        async for event_str in _conversation_sse_polling(
            conversation_id, db_url, last_event_id, fmt, connection_manager, poll_interval, heartbeat_interval
        ):
            yield event_str


async def _conversation_sse_postgres(
    conversation_id: str,
    db_url: str,
    last_event_id: int,
    fmt,
    connection_manager,
    heartbeat_interval: float,
):
    """Postgres LISTEN/NOTIFY based SSE for conversations."""
    import asyncpg

    from ..jobs import EventStore

    listen_db_url = os.environ.get("AIQ_LISTEN_DB_URL", db_url)
    asyncpg_url = (
        listen_db_url.replace("+psycopg2", "")
        .replace("+asyncpg", "")
        .replace("+psycopg", "")
        .replace("postgresql://", "postgres://")
    )
    channel = f"job_events_{conversation_id.replace('-', '_')}"
    notification_queue: asyncio.Queue = asyncio.Queue()

    def notification_handler(connection, pid, channel_name, payload):
        try:
            notification_queue.put_nowait(payload)
        except asyncio.QueueFull:
            pass

    conn = None
    try:
        conn = await asyncpg.connect(asyncpg_url)
        await conn.add_listener(channel, notification_handler)

        async with connection_manager.track_connection():
            # Replay historical events
            events = await EventStore.get_events_async(db_url, conversation_id, last_event_id, 10000)
            for event in events:
                db_event_id = event.pop("_id", None)
                if db_event_id:
                    last_event_id = db_event_id
                event_type = event.pop("type", "event")
                yield fmt(event_type, event, db_event_id)

            yield fmt("stream.mode", {"mode": "live"})

            while not connection_manager.is_shutting_down:
                try:
                    payload = await asyncio.wait_for(notification_queue.get(), timeout=heartbeat_interval)
                    notification_data = json.loads(payload)
                    event_id = notification_data.get("id")

                    if event_id and event_id > last_event_id:
                        event = await EventStore.get_event_by_id_async(db_url, event_id)
                        if event:
                            last_event_id = event_id
                            db_event_id = event.pop("_id", None)
                            event_type = event.pop("type", "event")
                            yield fmt(event_type, event, db_event_id)
                except TimeoutError:
                    # Heartbeat + fallback poll
                    yield fmt("heartbeat", {"ts": datetime.now(UTC).isoformat()})
                    fallback = await EventStore.get_events_async(db_url, conversation_id, last_event_id, 100)
                    for event in fallback:
                        db_event_id = event.pop("_id", None)
                        if db_event_id:
                            last_event_id = db_event_id
                        event_type = event.pop("type", "event")
                        yield fmt(event_type, event, db_event_id)
                except asyncio.CancelledError:
                    break

    finally:
        if conn:
            try:
                await conn.remove_listener(channel, notification_handler)
                await conn.close()
            except Exception:
                pass


async def _conversation_sse_polling(
    conversation_id: str,
    db_url: str,
    last_event_id: int,
    fmt,
    connection_manager,
    poll_interval: float,
    heartbeat_interval: float,
):
    """Polling-based SSE for SQLite and fallback."""
    from ..jobs import EventStore

    heartbeat_counter = 0
    ticks_per_heartbeat = int(heartbeat_interval / poll_interval)

    async with connection_manager.track_connection():
        yield fmt("stream.mode", {"mode": "polling"})

        while not connection_manager.is_shutting_down:
            try:
                events = await EventStore.get_events_async(db_url, conversation_id, last_event_id, 100)
                for event in events:
                    db_event_id = event.pop("_id", None)
                    if db_event_id:
                        last_event_id = db_event_id
                    event_type = event.pop("type", "event")
                    yield fmt(event_type, event, db_event_id)

                heartbeat_counter += 1
                if heartbeat_counter >= ticks_per_heartbeat:
                    heartbeat_counter = 0
                    yield fmt("heartbeat", {"ts": datetime.now(UTC).isoformat()})

                shutdown = await connection_manager.wait_or_shutdown(poll_interval)
                if shutdown:
                    break

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("SSE polling error for %s: %s", conversation_id, e)
                await asyncio.sleep(poll_interval)
