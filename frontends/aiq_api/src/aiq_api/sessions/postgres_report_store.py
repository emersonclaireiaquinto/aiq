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

"""PostgreSQL-backed implementation of the ReportVersionStore Protocol."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from aiq_agent.common.report_version_store import ReportVersion

from .models import report_versions_table

logger = logging.getLogger(__name__)


class PostgresReportVersionStore:
    """Persistent report version store backed by PostgreSQL.

    Implements the ReportVersionStore Protocol from aiq_agent.common.
    """

    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def append(self, version: ReportVersion) -> None:
        async with self._engine.begin() as conn:
            existing = await conn.execute(
                select(report_versions_table.c.version_id).where(
                    report_versions_table.c.version_id == version.version_id
                )
            )
            if existing.fetchone():
                logger.warning("Duplicate version_id %s ignored", version.version_id)
                return

            await conn.execute(
                report_versions_table.insert().values(
                    version_id=version.version_id,
                    conversation_id=version.conversation_id,
                    parent_version_id=version.parent_version_id,
                    content=version.content,
                    triggering_query=version.triggering_query,
                    created_at=version.created_at,
                )
            )
        logger.info(
            "Stored report version %s for conversation %s",
            version.version_id,
            version.conversation_id,
        )

    async def list(self, conversation_id: str) -> list[ReportVersion]:
        stmt = (
            select(report_versions_table)
            .where(report_versions_table.c.conversation_id == conversation_id)
            .order_by(report_versions_table.c.created_at.asc())
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.fetchall()
        return [
            ReportVersion(
                version_id=row.version_id,
                conversation_id=row.conversation_id,
                content=row.content,
                created_at=row.created_at,
                triggering_query=row.triggering_query,
                parent_version_id=row.parent_version_id,
            )
            for row in rows
        ]

    async def get(self, conversation_id: str, version_id: str) -> ReportVersion | None:
        stmt = select(report_versions_table).where(
            report_versions_table.c.conversation_id == conversation_id,
            report_versions_table.c.version_id == version_id,
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()
        if not row:
            return None
        return ReportVersion(
            version_id=row.version_id,
            conversation_id=row.conversation_id,
            content=row.content,
            created_at=row.created_at,
            triggering_query=row.triggering_query,
            parent_version_id=row.parent_version_id,
        )

    async def latest(self, conversation_id: str) -> ReportVersion | None:
        stmt = (
            select(report_versions_table)
            .where(report_versions_table.c.conversation_id == conversation_id)
            .order_by(report_versions_table.c.created_at.desc())
            .limit(1)
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()
        if not row:
            return None
        return ReportVersion(
            version_id=row.version_id,
            conversation_id=row.conversation_id,
            content=row.content,
            created_at=row.created_at,
            triggering_query=row.triggering_query,
            parent_version_id=row.parent_version_id,
        )
