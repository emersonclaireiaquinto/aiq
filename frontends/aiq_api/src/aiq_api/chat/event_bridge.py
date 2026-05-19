# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bridge deep research job events into the conversation event stream.

When the inline runner detects a deep research escalation, this module
starts an async task that reads events from the job's event store and
re-emits them into the conversation's event store. The frontend receives
all events on one SSE stream.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..jobs.event_store import EventStore

logger = logging.getLogger(__name__)

POLL_INTERVAL = 0.5
TERMINAL_STATUSES = frozenset({"success", "failure", "interrupted"})
TERMINAL_EVENT_TYPES = frozenset({"job.status", "job.error", "job.cancelled"})
IDLE_POLLS_BEFORE_STATUS_CHECK = 4


async def start_event_bridge(
    db_url: str,
    job_id: str,
    conversation_id: str,
) -> None:
    """Poll the job event store and re-emit events to the conversation stream."""
    from ..jobs.event_store import EventStore

    job_store = EventStore(db_url, job_id=job_id)
    conv_store = EventStore(db_url, job_id=conversation_id)

    last_id = 0
    idle_polls = 0
    logger.info("Event bridge started: job %s → conversation %s", job_id, conversation_id)

    try:
        while True:
            try:
                events = await _fetch_events_after(job_store, last_id)
            except Exception as e:
                logger.warning("Event bridge fetch failed: %s", e)
                await asyncio.sleep(POLL_INTERVAL)
                continue

            if events:
                idle_polls = 0
            else:
                idle_polls += 1

            for event in events:
                event_id = event.get("id", 0)
                event_data = event.get("event_data", {})
                if isinstance(event_data, str):
                    import json

                    try:
                        event_data = json.loads(event_data)
                    except (json.JSONDecodeError, TypeError):
                        event_data = {"raw": event_data}

                conv_store.store(event_data)

                last_id = max(last_id, event_id)

                event_type = event.get("event_type", "")
                if event_type in TERMINAL_EVENT_TYPES:
                    status = _extract_terminal_status(event_type, event_data)
                    if status:
                        conv_store.store(
                            {
                                "type": "deep_research.complete",
                                "data": {"job_id": job_id, "status": status},
                            }
                        )
                        logger.info("Event bridge done (event): job %s status=%s", job_id, status)
                        return

            if idle_polls >= IDLE_POLLS_BEFORE_STATUS_CHECK:
                idle_polls = 0
                status = await _check_job_status(db_url, job_id)
                if status in TERMINAL_STATUSES:
                    conv_store.store(
                        {
                            "type": "deep_research.complete",
                            "data": {"job_id": job_id, "status": status},
                        }
                    )
                    logger.info("Event bridge done (job_info): job %s status=%s", job_id, status)
                    return

            await asyncio.sleep(POLL_INTERVAL)

    except asyncio.CancelledError:
        logger.info("Event bridge cancelled: job %s", job_id)
        raise
    except Exception:
        logger.exception("Event bridge crashed: job %s", job_id)
        conv_store.store(
            {
                "type": "deep_research.complete",
                "data": {"job_id": job_id, "status": "failure", "error": "Event bridge crashed"},
            }
        )


def _extract_terminal_status(event_type: str, event_data: dict) -> str | None:
    """Extract a terminal status string from a job event, or None."""
    if event_type == "job.status":
        status = event_data.get("data", {}).get("status", "")
        return status if status in TERMINAL_STATUSES else None
    if event_type == "job.error":
        return "failure"
    if event_type == "job.cancelled":
        return "interrupted"
    return None


async def _check_job_status(db_url: str, job_id: str) -> str | None:
    """Query the job_info table for the job's current status."""
    from sqlalchemy import text

    from ..jobs.event_store import EventStore

    try:
        engine = EventStore._get_or_create_async_engine(db_url)
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT status FROM job_info WHERE job_id = :job_id"),
                {"job_id": job_id},
            )
            row = result.fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.debug("Event bridge status check failed for job %s: %s", job_id, e)
        return None


async def _fetch_events_after(event_store: EventStore, after_id: int) -> list[dict]:
    """Fetch events from the job's event store after a given cursor."""
    from sqlalchemy import text

    engine = event_store._get_or_create_async_engine(event_store.db_url)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT id, event_type, event_data, created_at "
                "FROM job_events "
                "WHERE job_id = :job_id AND id > :after_id "
                "ORDER BY id ASC LIMIT 100"
            ),
            {"job_id": event_store.job_id, "after_id": after_id},
        )
        rows = result.fetchall()

    return [
        {
            "id": row[0],
            "event_type": row[1],
            "event_data": row[2],
            "created_at": str(row[3]) if row[3] else None,
        }
        for row in rows
    ]
