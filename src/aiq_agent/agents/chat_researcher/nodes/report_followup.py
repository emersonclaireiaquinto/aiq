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

"""Report follow-up node: classifies follow-up messages and handles Q&A, gap detection, and refine routing."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.messages import BaseMessage
from langchain_core.messages import SystemMessage
from langgraph.types import Command

from aiq_agent.common import extract_json
from aiq_agent.common import load_prompt
from aiq_agent.common import render_prompt_template
from aiq_agent.common.report_version_store import ReportVersionStore

from ..models import ChatResearcherState
from ..utils import trim_message_history

logger = logging.getLogger(__name__)


class ReportFollowup:
    """Classifies follow-up messages against an existing report and routes accordingly."""

    def __init__(
        self,
        llm: BaseChatModel,
        report_version_store: ReportVersionStore,
        *,
        callbacks: list[BaseCallbackHandler] | None = None,
        max_history: int = 50,
    ) -> None:
        self.llm = llm
        self.store = report_version_store
        self.callbacks = callbacks or []
        self.max_history = max_history
        self._prompt = self._load_prompt()

    def _load_prompt(self) -> str:
        try:
            return load_prompt(Path(__file__).parent.parent / "prompts", "report_followup.j2")
        except Exception:
            logger.warning("Failed to load report_followup prompt, using inline fallback")
            return (
                "/no_think\n\nClassify the user's follow-up message as one of: "
                "answer_from_report, gap_detected, refine_report, new_topic.\n"
                'Respond ONLY with JSON: {"category": "...", "response": "...", "edit_instruction": null}'
            )

    async def run(self, state: ChatResearcherState, *, conversation_id: str) -> dict[str, Any] | Command:
        """Classify and handle the follow-up message."""
        latest = await self.store.latest(conversation_id)
        if not latest:
            logger.warning("report_followup invoked but no report found for %s", conversation_id)
            return self._fallthrough_to_depth(state)

        query = self._extract_query(state.messages)
        if not query:
            return {"messages": [AIMessage(content="Could you clarify your question about the report?")]}

        system_content = render_prompt_template(
            self._prompt,
            report_content=latest.content,
            query=query,
        )
        trimmed = trim_message_history(list(state.messages), self.max_history)
        messages: list[BaseMessage] = [SystemMessage(content=system_content)] + trimmed

        try:
            config = {"callbacks": self.callbacks} if self.callbacks else {}
            response = await self.llm.ainvoke(messages, config=config)
            parsed = extract_json((response.content or "").strip())
        except Exception as e:
            logger.exception("Error in report_followup classification: %s", e)
            return self._fallthrough_to_depth(state)

        if not parsed or not isinstance(parsed, dict):
            logger.warning("report_followup: unparseable LLM response, falling through to depth routing")
            return self._fallthrough_to_depth(state)

        category = (parsed.get("category") or "new_topic").strip().lower()
        response_text = parsed.get("response") or ""
        edit_instruction = parsed.get("edit_instruction")

        if category == "answer_from_report":
            return {"messages": [AIMessage(content=response_text)]}

        if category == "gap_detected":
            return {"messages": [AIMessage(content=response_text)]}

        if category == "refine_report":
            instruction = edit_instruction or query
            return Command(
                goto="deep_research",
                update={
                    "edit_instruction": instruction,
                    "original_query": query,
                    "messages": [AIMessage(content="Updating the report based on your request...")],
                },
            )

        # new_topic — fall through to normal routing
        return self._fallthrough_to_depth(state)

    @staticmethod
    def _extract_query(messages: list) -> str:
        for m in reversed(messages):
            content = getattr(m, "content", None)
            if content and hasattr(m, "type") and m.type == "human":
                return content if isinstance(content, str) else str(content)
        return ""

    @staticmethod
    def _fallthrough_to_depth(state: ChatResearcherState) -> Command:
        """Route to the normal research flow based on the depth_decision set by intent_classifier."""
        if state.depth_decision and state.depth_decision.decision == "deep":
            return Command(goto="clarifier")
        return Command(goto="shallow_research")
