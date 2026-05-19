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

"""SQLAlchemy table definitions for session persistence."""

from __future__ import annotations

import logging

from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import Index
from sqlalchemy import MetaData
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

logger = logging.getLogger(__name__)

metadata = MetaData()

conversations_table = Table(
    "conversations",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("user_id", String(128), nullable=False),
    Column("title", String(512), nullable=False, server_default="New Session"),
    Column("enabled_data_source_ids", JSONB, server_default="[]"),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Index("idx_conversations_user", "user_id", "updated_at"),
)

messages_table = Table(
    "messages",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("conversation_id", String(64), nullable=False, index=True),
    Column("role", String(16), nullable=False),
    Column("content", Text, nullable=False, server_default=""),
    Column("message_type", String(32), nullable=True),
    Column("metadata_", JSONB, server_default="{}"),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Index("idx_messages_conversation", "conversation_id", "created_at"),
)

report_versions_table = Table(
    "report_versions",
    metadata,
    Column("version_id", String(64), primary_key=True),
    Column("conversation_id", String(64), nullable=False, index=True),
    Column("parent_version_id", String(64), nullable=True),
    Column("content", Text, nullable=False),
    Column("triggering_query", Text, nullable=False, server_default=""),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Index("idx_report_versions_conversation", "conversation_id", "created_at"),
)

session_collections_table = Table(
    "session_collections",
    metadata,
    Column("session_id", String(64), primary_key=True),
    Column("user_id", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Index("idx_session_collections_user", "user_id"),
)


_tables_initialized: set[str] = set()


async def ensure_tables(engine) -> None:
    """Create session tables if they don't exist (idempotent)."""
    key = str(engine.url)
    if key in _tables_initialized:
        return

    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    _tables_initialized.add(key)
    logger.info("Session tables initialized")
