# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""FastAPI routes for session persistence (/v1/sessions/*)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from pydantic import BaseModel
from pydantic import Field

from .auth import get_current_user_id
from .store import ConversationStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])

_store: ConversationStore | None = None


def set_store(store: ConversationStore) -> None:
    global _store
    _store = store


def _get_store() -> ConversationStore:
    if _store is None:
        raise HTTPException(status_code=503, detail="Session store not initialized")
    return _store


# ── Request/Response Models ───────────────────────────────────────────


class CreateConversationRequest(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    title: str = Field(default="New Session", max_length=512)
    enabled_data_source_ids: list[str] | None = None


class UpdateConversationRequest(BaseModel):
    title: str | None = Field(default=None, max_length=512)
    enabled_data_source_ids: list[str] | None = None


class BulkDeleteRequest(BaseModel):
    conversation_ids: list[str] = Field(..., min_length=1, max_length=100)


class AppendMessagesRequest(BaseModel):
    messages: list[dict[str, Any]] = Field(..., min_length=1)


class UpdateMessageRequest(BaseModel):
    content: str | None = None
    message_type: str | None = None
    metadata: dict[str, Any] | None = None


class AppendReportVersionRequest(BaseModel):
    version_id: str | None = None
    parent_version_id: str | None = None
    content: str = Field(...)
    triggering_query: str = Field(default="")


class MigrateRequest(BaseModel):
    conversations: list[dict[str, Any]] = Field(default_factory=list)
    current_conversation_id: str | None = None


# ── Conversation Routes ───────────────────────────────────────────────


@router.post("/conversations")
async def create_conversation(
    body: CreateConversationRequest,
    user_id: str = Depends(get_current_user_id),
    store: ConversationStore = Depends(_get_store),
):
    return await store.create_conversation(
        user_id=user_id,
        title=body.title,
        enabled_data_source_ids=body.enabled_data_source_ids,
        conversation_id=body.id,
    )


@router.get("/conversations")
async def list_conversations(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(get_current_user_id),
    store: ConversationStore = Depends(_get_store),
):
    conversations = await store.list_conversations(user_id=user_id, limit=limit, offset=offset)
    return {"conversations": conversations, "total": len(conversations)}


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    user_id: str = Depends(get_current_user_id),
    store: ConversationStore = Depends(_get_store),
):
    conv = await store.get_conversation(conversation_id=conversation_id, user_id=user_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.put("/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    body: UpdateConversationRequest,
    user_id: str = Depends(get_current_user_id),
    store: ConversationStore = Depends(_get_store),
):
    updated = await store.update_conversation(
        conversation_id=conversation_id,
        user_id=user_id,
        title=body.title,
        enabled_data_source_ids=body.enabled_data_source_ids,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "ok"}


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    user_id: str = Depends(get_current_user_id),
    store: ConversationStore = Depends(_get_store),
):
    deleted = await store.delete_conversation(conversation_id=conversation_id, user_id=user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "ok"}


@router.post("/conversations/bulk-delete")
async def bulk_delete_conversations(
    body: BulkDeleteRequest,
    user_id: str = Depends(get_current_user_id),
    store: ConversationStore = Depends(_get_store),
):
    count = await store.bulk_delete_conversations(conversation_ids=body.conversation_ids, user_id=user_id)
    return {"deleted": count}


# ── Message Routes ────────────────────────────────────────────────────


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    limit: int = Query(default=1000, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    store: ConversationStore = Depends(_get_store),
):
    messages = await store.list_messages(conversation_id=conversation_id, limit=limit, offset=offset)
    return {"messages": messages}


@router.post("/conversations/{conversation_id}/messages")
async def append_messages(
    conversation_id: str,
    body: AppendMessagesRequest,
    store: ConversationStore = Depends(_get_store),
):
    ids = await store.append_messages(conversation_id=conversation_id, messages=body.messages)
    return {"message_ids": ids}


@router.put("/conversations/{conversation_id}/messages/{message_id}")
async def update_message(
    conversation_id: str,
    message_id: str,
    body: UpdateMessageRequest,
    store: ConversationStore = Depends(_get_store),
):
    updates = {}
    if body.content is not None:
        updates["content"] = body.content
    if body.message_type is not None:
        updates["message_type"] = body.message_type
    if body.metadata is not None:
        updates.update(body.metadata)
    updated = await store.update_message(message_id=message_id, updates=updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"status": "ok"}


# ── Report Version Routes ────────────────────────────────────────────


@router.get("/conversations/{conversation_id}/report-versions")
async def list_report_versions(
    conversation_id: str,
    store: ConversationStore = Depends(_get_store),
):
    versions = await store.list_report_versions(conversation_id=conversation_id)
    return {"report_versions": versions}


@router.post("/conversations/{conversation_id}/report-versions")
async def append_report_version(
    conversation_id: str,
    body: AppendReportVersionRequest,
    store: ConversationStore = Depends(_get_store),
):
    version_id = await store.append_report_version(
        conversation_id=conversation_id,
        version={
            "version_id": body.version_id,
            "parent_version_id": body.parent_version_id,
            "content": body.content,
            "triggering_query": body.triggering_query,
        },
    )
    return {"version_id": version_id}


# ── Collection Tracking Routes ────────────────────────────────────────


@router.get("/collections/known")
async def list_known_collections(
    user_id: str = Depends(get_current_user_id),
    store: ConversationStore = Depends(_get_store),
):
    session_ids = await store.list_known_collections(user_id=user_id)
    return {"session_ids": session_ids}


@router.post("/collections/known/{session_id}")
async def mark_collection(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    store: ConversationStore = Depends(_get_store),
):
    await store.mark_collection(session_id=session_id, user_id=user_id)
    return {"status": "ok"}


@router.delete("/collections/known/{session_id}")
async def unmark_collection(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    store: ConversationStore = Depends(_get_store),
):
    await store.unmark_collection(session_id=session_id, user_id=user_id)
    return {"status": "ok"}


# ── Migration Route ──────────────────────────────────────────────────


@router.post("/migrate")
async def migrate_from_localstorage(
    body: MigrateRequest,
    user_id: str = Depends(get_current_user_id),
    store: ConversationStore = Depends(_get_store),
):
    """One-time migration: import conversations from frontend localStorage."""
    imported = 0
    for conv_data in body.conversations:
        conv_id = conv_data.get("id")
        if not conv_id:
            continue

        existing = await store.get_conversation(conversation_id=conv_id, user_id=user_id)
        if existing:
            continue

        await store.create_conversation(
            user_id=user_id,
            title=conv_data.get("title", "Imported Session"),
            enabled_data_source_ids=conv_data.get("enabledDataSourceIds"),
            conversation_id=conv_id,
        )

        messages = conv_data.get("messages", [])
        if messages:
            normalized = []
            for msg in messages:
                normalized.append(
                    {
                        "id": msg.get("id"),
                        "role": msg.get("role", "user"),
                        "content": msg.get("content", ""),
                        "message_type": msg.get("messageType"),
                        "created_at": msg.get("timestamp"),
                    }
                )
            await store.append_messages(conversation_id=conv_id, messages=normalized)

        for version in conv_data.get("reportVersions", []):
            await store.append_report_version(
                conversation_id=conv_id,
                version={
                    "version_id": version.get("versionId"),
                    "parent_version_id": version.get("parentVersionId"),
                    "content": version.get("content", ""),
                    "triggering_query": version.get("triggeringQuery", ""),
                },
            )

        imported += 1

    logger.info("Migrated %d conversations for user %s", imported, user_id)
    return {"imported": imported}
