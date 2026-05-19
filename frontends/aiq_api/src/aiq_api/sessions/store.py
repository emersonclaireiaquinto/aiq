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

"""Async CRUD operations for session persistence."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC
from datetime import datetime
from typing import Any

from sqlalchemy import delete
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.ext.asyncio import create_async_engine

from .models import conversations_table
from .models import ensure_tables
from .models import messages_table
from .models import report_versions_table
from .models import session_collections_table

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None


def _normalize_db_url(db_url: str) -> str:
    """Normalize to async psycopg driver."""
    url = db_url.replace("+asyncpg", "").replace("+psycopg2", "").replace("+psycopg", "")
    if not url.startswith("postgresql://"):
        url = url.replace("postgres://", "postgresql://")
    return url.replace("postgresql://", "postgresql+psycopg://")


async def get_engine(db_url: str) -> AsyncEngine:
    """Get or create the shared async engine."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            _normalize_db_url(db_url),
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_recycle=1800,
        )
        await ensure_tables(_engine)
    return _engine


def _generate_conversation_id() -> str:
    return f"s_{uuid.uuid4().hex}"


def _now() -> datetime:
    return datetime.now(UTC)


class ConversationStore:
    """Async CRUD for conversations, messages, and collection tracking."""

    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    # ── Conversations ─────────────────────────────────────────────────

    async def conversation_exists(self, conversation_id: str, user_id: str) -> bool:
        """Check if a conversation exists and is owned by the given user."""
        stmt = select(conversations_table.c.id).where(
            conversations_table.c.id == conversation_id,
            conversations_table.c.user_id == user_id,
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            return result.fetchone() is not None

    async def create_conversation(
        self,
        user_id: str,
        title: str = "New Session",
        enabled_data_source_ids: list[str] | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        conv_id = conversation_id or _generate_conversation_id()
        row = {
            "id": conv_id,
            "user_id": user_id,
            "title": title,
            "enabled_data_source_ids": enabled_data_source_ids or [],
            "created_at": now,
            "updated_at": now,
        }
        async with self._engine.begin() as conn:
            await conn.execute(conversations_table.insert().values(**row))
        return {
            "id": conv_id,
            "user_id": user_id,
            "title": title,
            "enabled_data_source_ids": enabled_data_source_ids or [],
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }

    async def list_conversations(
        self,
        user_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(
                conversations_table.c.id,
                conversations_table.c.title,
                conversations_table.c.enabled_data_source_ids,
                conversations_table.c.created_at,
                conversations_table.c.updated_at,
            )
            .where(conversations_table.c.user_id == user_id)
            .order_by(conversations_table.c.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.fetchall()

        conversations = []
        for row in rows:
            msg_count_stmt = (
                select(text("count(*)")).select_from(messages_table).where(messages_table.c.conversation_id == row.id)
            )
            async with self._engine.connect() as conn:
                count_result = await conn.execute(msg_count_stmt)
                msg_count = count_result.scalar() or 0

            conversations.append(
                {
                    "id": row.id,
                    "title": row.title,
                    "enabled_data_source_ids": row.enabled_data_source_ids or [],
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                    "message_count": msg_count,
                }
            )
        return conversations

    async def get_conversation(self, conversation_id: str, user_id: str) -> dict[str, Any] | None:
        stmt = select(conversations_table).where(
            conversations_table.c.id == conversation_id,
            conversations_table.c.user_id == user_id,
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()
        if not row:
            return None

        messages = await self.list_messages(conversation_id)
        versions = await self.list_report_versions(conversation_id)

        return {
            "id": row.id,
            "user_id": row.user_id,
            "title": row.title,
            "enabled_data_source_ids": row.enabled_data_source_ids or [],
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "messages": messages,
            "report_versions": versions,
        }

    async def update_conversation(
        self,
        conversation_id: str,
        user_id: str,
        title: str | None = None,
        enabled_data_source_ids: list[str] | None = None,
    ) -> bool:
        values: dict[str, Any] = {"updated_at": _now()}
        if title is not None:
            values["title"] = title
        if enabled_data_source_ids is not None:
            values["enabled_data_source_ids"] = enabled_data_source_ids

        stmt = (
            conversations_table.update()
            .where(
                conversations_table.c.id == conversation_id,
                conversations_table.c.user_id == user_id,
            )
            .values(**values)
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount > 0

    async def delete_conversation(self, conversation_id: str, user_id: str) -> bool:
        stmt = delete(conversations_table).where(
            conversations_table.c.id == conversation_id,
            conversations_table.c.user_id == user_id,
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount > 0

    async def bulk_delete_conversations(self, conversation_ids: list[str], user_id: str) -> int:
        if not conversation_ids:
            return 0
        stmt = delete(conversations_table).where(
            conversations_table.c.id.in_(conversation_ids),
            conversations_table.c.user_id == user_id,
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount

    # ── Messages ──────────────────────────────────────────────────────

    async def list_messages(
        self,
        conversation_id: str,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(messages_table)
            .where(messages_table.c.conversation_id == conversation_id)
            .order_by(messages_table.c.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.fetchall()
        return [
            {
                "id": row.id,
                "conversation_id": row.conversation_id,
                "role": row.role,
                "content": row.content,
                "message_type": row.message_type,
                "metadata": row.metadata_ or {},
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]

    async def append_messages(self, conversation_id: str, messages: list[dict[str, Any]]) -> list[str]:
        if not messages:
            return []

        ids = []
        rows = []
        now = _now()
        for msg in messages:
            msg_id = msg.get("id") or uuid.uuid4().hex[:12]
            ids.append(msg_id)

            non_metadata_keys = ("id", "conversation_id", "role", "content", "message_type", "created_at")
            metadata = {k: v for k, v in msg.items() if k not in non_metadata_keys}

            rows.append(
                {
                    "id": msg_id,
                    "conversation_id": conversation_id,
                    "role": msg["role"],
                    "content": msg.get("content", ""),
                    "message_type": msg.get("message_type"),
                    "metadata_": metadata or {},
                    "created_at": msg.get("created_at") or now,
                }
            )

        async with self._engine.begin() as conn:
            stmt = pg_insert(messages_table).values(rows).on_conflict_do_nothing(index_elements=["id"])
            await conn.execute(stmt)
            await conn.execute(
                conversations_table.update().where(conversations_table.c.id == conversation_id).values(updated_at=now)
            )
        return ids

    async def update_message(self, message_id: str, updates: dict[str, Any]) -> bool:
        values: dict[str, Any] = {}
        if "content" in updates:
            values["content"] = updates["content"]
        if "message_type" in updates:
            values["message_type"] = updates["message_type"]

        non_meta_keys = ("content", "message_type", "id", "conversation_id", "role", "created_at")
        metadata_updates = {k: v for k, v in updates.items() if k not in non_meta_keys}
        if metadata_updates:
            async with self._engine.connect() as conn:
                result = await conn.execute(select(messages_table.c.metadata_).where(messages_table.c.id == message_id))
                row = result.fetchone()
            if row:
                existing = row.metadata_ or {}
                existing.update(metadata_updates)
                values["metadata_"] = existing

        if not values:
            return False

        stmt = messages_table.update().where(messages_table.c.id == message_id).values(**values)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount > 0

    # ── Report Versions ───────────────────────────────────────────────

    async def list_report_versions(self, conversation_id: str) -> list[dict[str, Any]]:
        stmt = (
            select(report_versions_table)
            .where(report_versions_table.c.conversation_id == conversation_id)
            .order_by(report_versions_table.c.created_at.asc())
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.fetchall()
        return [
            {
                "version_id": row.version_id,
                "conversation_id": row.conversation_id,
                "parent_version_id": row.parent_version_id,
                "content": row.content,
                "triggering_query": row.triggering_query,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]

    async def append_report_version(self, conversation_id: str, version: dict[str, Any]) -> str:
        version_id = version.get("version_id") or uuid.uuid4().hex[:12]
        row = {
            "version_id": version_id,
            "conversation_id": conversation_id,
            "parent_version_id": version.get("parent_version_id"),
            "content": version["content"],
            "triggering_query": version.get("triggering_query", ""),
            "created_at": version.get("created_at") or _now(),
        }
        async with self._engine.begin() as conn:
            existing = await conn.execute(
                select(report_versions_table.c.version_id).where(report_versions_table.c.version_id == version_id)
            )
            if existing.fetchone():
                logger.debug("Report version %s already exists, skipping", version_id)
                return version_id
            await conn.execute(report_versions_table.insert().values(**row))
        return version_id

    # ── Collection Tracking ───────────────────────────────────────────

    async def list_known_collections(self, user_id: str) -> list[str]:
        stmt = (
            select(session_collections_table.c.session_id)
            .where(session_collections_table.c.user_id == user_id)
            .order_by(session_collections_table.c.created_at.desc())
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            return [row.session_id for row in result.fetchall()]

    async def mark_collection(self, session_id: str, user_id: str) -> None:
        async with self._engine.begin() as conn:
            existing = await conn.execute(
                select(session_collections_table.c.session_id).where(
                    session_collections_table.c.session_id == session_id
                )
            )
            if existing.fetchone():
                return
            await conn.execute(
                session_collections_table.insert().values(
                    session_id=session_id,
                    user_id=user_id,
                    created_at=_now(),
                )
            )

    async def unmark_collection(self, session_id: str, user_id: str) -> bool:
        stmt = delete(session_collections_table).where(
            session_collections_table.c.session_id == session_id,
            session_collections_table.c.user_id == user_id,
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount > 0
