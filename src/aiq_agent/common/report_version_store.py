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
Swap to a SQL-backed implementation for persistence across restarts.
"""

from __future__ import annotations

import datetime as _dt
import logging
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


def get_report_version_store() -> InMemoryReportVersionStore:
    """Return the shared in-memory report version store."""
    global _report_version_store
    if _report_version_store is None:
        _report_version_store = InMemoryReportVersionStore()
    return _report_version_store
