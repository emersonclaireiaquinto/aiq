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

"""Versioned report storage abstraction for iterative research workflows.

Provides a Protocol for storing and retrieving report versions, keyed by
conversation_id. The default InMemoryReportVersionStore keeps everything
in a process-local dict — suitable for dev / single-process deployments.

When AIQ_SESSIONS_DB is set to a PostgreSQL DSN, get_report_version_store()
returns a PostgresReportVersionStore for persistence across restarts.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Protocol
from typing import runtime_checkable

from pydantic import BaseModel
from pydantic import Field

logger = logging.getLogger(__name__)


class ReportVersion(BaseModel):
    """A single immutable snapshot of a research report."""

    version_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    conversation_id: str
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(_dt.UTC))
    triggering_query: str
    parent_version_id: str | None = None


@runtime_checkable
class ReportVersionStore(Protocol):
    """Interface for report version persistence."""

    async def append(self, version: ReportVersion) -> None:
        """Store a new version. Must not duplicate version_id within a conversation."""
        ...

    async def list(self, conversation_id: str) -> list[ReportVersion]:
        """Return all versions for a conversation, ordered oldest-first."""
        ...

    async def get(self, conversation_id: str, version_id: str) -> ReportVersion | None:
        """Fetch a single version by id, or None if not found."""
        ...

    async def latest(self, conversation_id: str) -> ReportVersion | None:
        """Return the most recent version for a conversation, or None."""
        ...


class InMemoryReportVersionStore:
    """Process-local in-memory store. Lost on restart."""

    def __init__(self) -> None:
        self._store: dict[str, list[ReportVersion]] = defaultdict(list)

    async def append(self, version: ReportVersion) -> None:
        versions = self._store[version.conversation_id]
        for existing in versions:
            if existing.version_id == version.version_id:
                logger.warning("Duplicate version_id %s ignored", version.version_id)
                return
        versions.append(version)
        logger.info(
            "Stored report version %s for conversation %s (v%d)",
            version.version_id,
            version.conversation_id,
            len(versions),
        )

    async def list(self, conversation_id: str) -> list[ReportVersion]:
        return list(self._store.get(conversation_id, []))

    async def get(self, conversation_id: str, version_id: str) -> ReportVersion | None:
        for v in self._store.get(conversation_id, []):
            if v.version_id == version_id:
                return v
        return None

    async def latest(self, conversation_id: str) -> ReportVersion | None:
        versions = self._store.get(conversation_id, [])
        return versions[-1] if versions else None


# Process-wide singleton, mirroring the get_checkpointer pattern.
_report_version_store: InMemoryReportVersionStore | None = None
_postgres_report_version_store = None


def get_report_version_store() -> ReportVersionStore:
    """Return the shared report version store.

    Uses PostgresReportVersionStore when AIQ_SESSIONS_DB is set to a
    PostgreSQL DSN, otherwise falls back to InMemoryReportVersionStore.
    """
    db_url = os.environ.get("AIQ_SESSIONS_DB") or os.environ.get("NAT_JOB_STORE_DB_URL")
    if db_url and db_url.startswith("postgresql"):
        return _get_postgres_report_version_store(db_url)
    return _get_in_memory_report_version_store()


def _get_in_memory_report_version_store() -> InMemoryReportVersionStore:
    global _report_version_store
    if _report_version_store is None:
        _report_version_store = InMemoryReportVersionStore()
    return _report_version_store


def _get_postgres_report_version_store(db_url: str):
    global _postgres_report_version_store
    if _postgres_report_version_store is None:
        try:
            from aiq_api.sessions.postgres_report_store import PostgresReportVersionStore
            from aiq_api.sessions.store import _normalize_db_url
            from sqlalchemy.ext.asyncio import create_async_engine

            engine = create_async_engine(
                _normalize_db_url(db_url),
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=10,
                pool_recycle=1800,
            )
            _postgres_report_version_store = PostgresReportVersionStore(engine)
            logger.info("Using PostgresReportVersionStore (db: %s)", db_url[:50])
        except Exception:
            logger.warning("Failed to initialize PostgresReportVersionStore, falling back to in-memory", exc_info=True)
            return _get_in_memory_report_version_store()
    return _postgres_report_version_store
