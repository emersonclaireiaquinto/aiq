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

"""Report follow-up agent: tool-calling agent for Q&A, editing, and deep research escalation."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.messages import AnyMessage
from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt import tools_condition
from pydantic import BaseModel

from aiq_agent.common import load_prompt
from aiq_agent.common import render_prompt_template
from aiq_agent.common.report_version_store import ReportVersion
from aiq_agent.common.report_version_store import ReportVersionStore

from ..models import ChatResearcherState
from ..utils import trim_message_history

logger = logging.getLogger(__name__)


class ReportFollowupState(BaseModel):
    """Internal state for the report follow-up sub-graph."""

    messages: Annotated[list[AnyMessage], add_messages]
    tool_iterations: int = 0


class ReportFollowup:
    """Tool-calling agent for interacting with and modifying an existing research report."""

    def __init__(
        self,
        llm: BaseChatModel,
        report_version_store: ReportVersionStore,
        *,
        edit_job_submitter: Callable[[str, str], Awaitable[str]] | None = None,
        deep_research_fn: Callable[..., Awaitable[Any]] | None = None,
        callbacks: list[BaseCallbackHandler] | None = None,
        max_history: int = 50,
        max_tool_iterations: int = 10,
    ) -> None:
        self.llm = llm
        self.store = report_version_store
        self.edit_job_submitter = edit_job_submitter
        self.deep_research_fn = deep_research_fn
        self.callbacks = callbacks or []
        self.max_history = max_history
        self.max_tool_iterations = max_tool_iterations
        self._prompt = self._load_prompt()

    def _load_prompt(self) -> str:
        try:
            return load_prompt(Path(__file__).parent.parent / "prompts", "report_followup.j2")
        except Exception:
            logger.warning("Failed to load report_followup prompt, using inline fallback")
            return (
                "You are a report assistant. You can read, edit, and rewrite the research report "
                "using the provided tools. For questions about the report, respond directly.\n\n"
                "## Current Report\n{{ report_content }}"
            )

    def _build_tools(self, conversation_id: str) -> list:
        store = self.store

        @tool
        async def read_report() -> str:
            """Read the current research report content."""
            latest = await store.latest(conversation_id)
            if not latest:
                return "No report found for this conversation."
            return latest.content

        @tool
        async def edit_report(search: str, replace: str) -> str:
            """Edit the report by replacing an exact string match. Call read_report first to get exact text."""
            latest = await store.latest(conversation_id)
            if not latest:
                return "Error: No report found to edit."
            if search not in latest.content:
                return (
                    "Error: The search string was not found in the report. "
                    "Make sure you copy the exact text including whitespace and punctuation. "
                    "Call read_report to get the current text."
                )
            new_content = latest.content.replace(search, replace, 1)
            version = ReportVersion(
                conversation_id=conversation_id,
                content=new_content,
                triggering_query=f"edit: {search[:50]}... -> {replace[:50]}...",
                parent_version_id=latest.version_id,
            )
            await store.append(version)
            return f"Report updated successfully (version {version.version_id})."

        @tool
        async def rewrite_report(content: str) -> str:
            """Fully rewrite the report. Use for major structural changes only."""
            latest = await store.latest(conversation_id)
            parent_id = latest.version_id if latest else None
            version = ReportVersion(
                conversation_id=conversation_id,
                content=content,
                triggering_query="full rewrite",
                parent_version_id=parent_id,
            )
            await store.append(version)
            return f"Report rewritten successfully (version {version.version_id})."

        @tool
        async def request_deep_research(instruction: str) -> str:
            """Submit a deep research job for edits requiring new external sources. Returns a job ID."""
            if self.edit_job_submitter is not None:
                job_id = await self.edit_job_submitter(instruction, conversation_id)
                return f"Deep research job submitted. Job ID: {job_id}"

            if self.deep_research_fn is not None:
                from aiq_agent.agents.deep_researcher.models import DeepResearchAgentState

                latest = await store.latest(conversation_id)
                deep_state = DeepResearchAgentState(
                    messages=[HumanMessage(content=instruction)],
                    prior_report=latest.content if latest else None,
                    edit_instruction=instruction,
                )
                result = await self.deep_research_fn(deep_state)
                if result.messages:
                    report_content = result.messages[-1].content
                    if isinstance(report_content, list):
                        report_content = " ".join(
                            p.get("text", "") for p in report_content if isinstance(p, dict) and p.get("type") == "text"
                        )
                    if isinstance(report_content, str):
                        version = ReportVersion(
                            conversation_id=conversation_id,
                            content=report_content,
                            triggering_query=instruction,
                            parent_version_id=latest.version_id if latest else None,
                        )
                        await store.append(version)
                        return f"Deep research completed. Report updated (version {version.version_id})."
                return "Deep research completed but produced no output."

            return "Deep research is not configured. Use edit_report or rewrite_report to modify the report directly."

        return [read_report, edit_report, rewrite_report, request_deep_research]

    def _build_graph(self, tools: list) -> Any:
        max_iters = self.max_tool_iterations
        llm = self.llm

        async def agent_node(state: ReportFollowupState) -> dict[str, Any]:
            iterations = state.tool_iterations

            if iterations >= max_iters:
                logger.warning("Report follow-up: max iterations (%d) reached, forcing synthesis.", iterations)
                response = await llm.ainvoke(state.messages)
                return {"messages": [response], "tool_iterations": iterations}

            llm_with_tools = llm.bind_tools(tools)
            response = await llm_with_tools.ainvoke(state.messages)

            new_iterations = iterations
            if hasattr(response, "tool_calls") and response.tool_calls:
                new_iterations += len(response.tool_calls)

            return {"messages": [response], "tool_iterations": new_iterations}

        builder = StateGraph(ReportFollowupState)
        builder.set_entry_point("agent")
        builder.add_node("agent", agent_node)
        builder.add_node("tools", ToolNode(tools))
        builder.add_conditional_edges("agent", tools_condition, {"tools": "tools", "__end__": "__end__"})
        builder.add_edge("tools", "agent")
        return builder.compile()

    async def run(self, state: ChatResearcherState, *, conversation_id: str) -> dict[str, Any]:
        """Run the report follow-up agent."""
        latest = await self.store.latest(conversation_id)
        if not latest:
            logger.warning("report_followup invoked but no report found for %s", conversation_id)
            return {"messages": [AIMessage(content="No report found for this conversation.")]}

        tools = self._build_tools(conversation_id)
        graph = self._build_graph(tools)

        current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        system_content = render_prompt_template(
            self._prompt,
            report_content=latest.content,
            current_datetime=current_datetime,
        )
        trimmed = trim_message_history(list(state.messages), self.max_history)
        messages = [SystemMessage(content=system_content)] + trimmed

        sub_state = ReportFollowupState(messages=messages)
        config: dict[str, Any] = {"recursion_limit": (self.max_tool_iterations * 2) + 10}
        if self.callbacks:
            config["callbacks"] = self.callbacks

        result = await graph.ainvoke(sub_state, config=config)

        result_messages = result.get("messages", []) if isinstance(result, dict) else result.messages
        new_messages = result_messages[len(messages) :]

        final_ai = None
        for m in reversed(new_messages):
            if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
                final_ai = m
                break

        update: dict[str, Any] = {}
        if final_ai:
            update["messages"] = [final_ai]
        else:
            update["messages"] = [AIMessage(content="I've processed your request about the report.")]

        versions = await self.store.list(conversation_id)
        if versions:
            update["report_version_ids"] = [v.version_id for v in versions]

        return update
