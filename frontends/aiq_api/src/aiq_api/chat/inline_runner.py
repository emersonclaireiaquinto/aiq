# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Inline agent runner with event store integration and HITL support.

Runs the NAT workflow in the API server process (not Dask), emitting events
to the event store for SSE streaming. HITL works via asyncio.Future resolved
by the REST /v1/chat/{id}/respond endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any

from nat.data_models.api_server import ResponseIntermediateStep
from nat.data_models.api_server import ResponsePayloadOutput
from nat.data_models.interactive import HumanResponseText
from nat.front_ends.fastapi.response_helpers import generate_streaming_response_full

if TYPE_CHECKING:
    from nat.data_models.interactive import HumanResponse
    from nat.data_models.interactive import InteractionPrompt
    from nat.front_ends.fastapi.step_adaptor import StepAdaptor
    from nat.runtime.session import SessionManager

    from ..sessions.store import ConversationStore

logger = logging.getLogger(__name__)

HITL_TIMEOUT_SECONDS = 300


class InlineRunner:
    """Runs agent workflows inline with SSE event emission and HITL support.

    Shares the API server process and event loop so asyncio.Future-based HITL
    works: the REST respond endpoint resolves the same Future the agent awaits.
    """

    def __init__(
        self,
        session_manager: SessionManager,
        step_adaptor: StepAdaptor,
        event_store_db_url: str,
        conversation_store: ConversationStore | None = None,
    ):
        self._session_manager = session_manager
        self._step_adaptor = step_adaptor
        self._event_store_db_url = event_store_db_url
        self._conversation_store = conversation_store

        self._pending_interactions: dict[str, asyncio.Future[str]] = {}
        self._active_tasks: dict[str, asyncio.Task] = {}

    @property
    def active_conversations(self) -> set[str]:
        return set(self._active_tasks.keys())

    def is_active(self, conversation_id: str) -> bool:
        task = self._active_tasks.get(conversation_id)
        return task is not None and not task.done()

    async def run(
        self,
        conversation_id: str,
        user_message: str,
        data_sources: list[str] | None = None,
    ) -> None:
        """Start agent processing as a background task.

        The caller should NOT await this directly — it creates and tracks
        an asyncio.Task so the REST endpoint can return immediately.
        """
        if self.is_active(conversation_id):
            raise RuntimeError(f"Conversation {conversation_id} already has an active task")

        task = asyncio.create_task(
            self._execute(conversation_id, user_message, data_sources),
            name=f"chat-{conversation_id}",
        )
        self._active_tasks[conversation_id] = task

        def _cleanup(t: asyncio.Task) -> None:
            self._active_tasks.pop(conversation_id, None)
            self._pending_interactions.pop(conversation_id, None)

        task.add_done_callback(_cleanup)

    async def _execute(
        self,
        conversation_id: str,
        user_message: str,
        data_sources: list[str] | None,
    ) -> None:
        """Run the workflow, emitting events to the conversation's event store."""
        from ..jobs.event_store import BatchingEventStore
        from ..jobs.event_store import EventStore

        event_store = EventStore(self._event_store_db_url, job_id=conversation_id)
        batching_store = BatchingEventStore(event_store)

        try:
            batching_store.store({"type": "chat.start", "data": {"message": user_message}})

            from aiq_agent.common.report_version_store import get_report_version_store

            version_store = get_report_version_store()
            pre_versions = await version_store.list(conversation_id)
            pre_version_count = len(pre_versions)

            async def hitl_callback(prompt: InteractionPrompt) -> HumanResponse:
                return await self._handle_hitl(conversation_id, prompt, batching_store)

            async with self._session_manager.session(
                user_input_callback=hitl_callback,
                conversation_id=conversation_id,
            ) as session:
                self._session_manager._context_state.conversation_id.set(conversation_id)

                response_content = ""
                async for item in generate_streaming_response_full(
                    user_message,
                    session=session,
                    streaming=True,
                ):
                    if isinstance(item, ResponseIntermediateStep):
                        batching_store.store(
                            {
                                "type": f"step.{item.type}",
                                "data": {
                                    "id": item.id,
                                    "parent_id": item.parent_id,
                                    "name": item.name,
                                    "payload": item.payload,
                                },
                            }
                        )
                    elif isinstance(item, ResponsePayloadOutput):
                        payload = item.payload
                        if hasattr(payload, "choices") and payload.choices:
                            text = payload.choices[0].message.content or ""
                        elif hasattr(payload, "content"):
                            text = payload.content
                        else:
                            text = str(payload)
                        response_content = text

            report_version = await self._build_report_version(conversation_id, version_store, pre_version_count)

            response_data: dict[str, Any] = {"content": response_content}
            if report_version:
                response_data["report_version"] = report_version
            batching_store.store({"type": "chat.response", "data": response_data})

            batching_store.store({"type": "chat.complete", "data": {}})

            if self._conversation_store and response_content:
                try:
                    await self._conversation_store.append_messages(
                        conversation_id,
                        [
                            {
                                "id": uuid.uuid4().hex[:12],
                                "role": "assistant",
                                "content": response_content,
                                "message_type": "agent_response",
                                "created_at": datetime.now(UTC).isoformat(),
                            }
                        ],
                    )
                except Exception as e:
                    logger.warning("Failed to persist assistant response: %s", e)

        except asyncio.CancelledError:
            batching_store.store({"type": "chat.cancelled", "data": {}})
            raise
        except Exception as e:
            logger.exception("Chat execution failed for %s", conversation_id)
            batching_store.store(
                {
                    "type": "chat.error",
                    "data": {"error": str(e), "error_type": type(e).__name__},
                }
            )
        finally:
            batching_store.flush()

    async def _handle_hitl(
        self,
        conversation_id: str,
        prompt: InteractionPrompt,
        event_store: Any,
    ) -> HumanResponse:
        """Emit interaction.required event and block until REST endpoint resolves."""
        prompt_id = prompt.id or uuid.uuid4().hex
        prompt_content = prompt.content

        event_store.store(
            {
                "type": "interaction.required",
                "data": {
                    "prompt_id": prompt_id,
                    "text": prompt_content.text,
                    "input_type": str(prompt_content.input_type),
                    "timeout": prompt_content.timeout,
                    "options": (
                        [o.model_dump() for o in prompt_content.options] if hasattr(prompt_content, "options") else None
                    ),
                },
            }
        )
        event_store.flush() if hasattr(event_store, "flush") else None

        # Persist the prompt as an assistant message
        if self._conversation_store:
            try:
                await self._conversation_store.append_messages(
                    conversation_id,
                    [
                        {
                            "id": prompt_id,
                            "role": "assistant",
                            "content": prompt_content.text,
                            "message_type": "prompt",
                            "created_at": datetime.now(UTC).isoformat(),
                        }
                    ],
                )
            except Exception as e:
                logger.warning("Failed to persist HITL prompt: %s", e)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending_interactions[conversation_id] = future

        timeout = prompt_content.timeout or HITL_TIMEOUT_SECONDS
        try:
            response_text = await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            event_store.store(
                {
                    "type": "interaction.timeout",
                    "data": {"prompt_id": prompt_id},
                }
            )
            raise
        finally:
            self._pending_interactions.pop(conversation_id, None)

        event_store.store(
            {
                "type": "interaction.resolved",
                "data": {"prompt_id": prompt_id},
            }
        )

        return HumanResponseText(text=response_text)

    @staticmethod
    async def _build_report_version(
        conversation_id: str,
        version_store: Any,
        pre_version_count: int,
    ) -> dict[str, Any] | None:
        """Build a report_version dict if new versions were created during this turn."""
        try:
            post_versions = await version_store.list(conversation_id)
            if len(post_versions) <= pre_version_count:
                return None
            latest = post_versions[-1]
            parent_content = None
            if latest.parent_version_id:
                parent = await version_store.get(conversation_id, latest.parent_version_id)
                parent_content = parent.content if parent else None
            return {
                "versionId": latest.version_id,
                "parentVersionId": latest.parent_version_id,
                "content": latest.content,
                "triggeringQuery": latest.triggering_query,
                "parentContent": parent_content,
            }
        except Exception as e:
            logger.warning("Failed to build report version for %s: %s", conversation_id, e)
            return None

    def resolve_interaction(self, conversation_id: str, response_text: str) -> bool:
        """Resolve a pending HITL interaction. Called by the REST respond endpoint."""
        future = self._pending_interactions.get(conversation_id)
        if future is None or future.done():
            return False
        future.set_result(response_text)
        return True

    def cancel(self, conversation_id: str) -> bool:
        """Cancel the active task for a conversation."""
        task = self._active_tasks.get(conversation_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True
