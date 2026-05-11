# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the report_followup tool-calling agent."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.messages import HumanMessage

from aiq_agent.agents.chat_researcher.models import ChatResearcherState
from aiq_agent.agents.chat_researcher.nodes.report_followup import ReportFollowup
from aiq_agent.common.report_version_store import InMemoryReportVersionStore
from aiq_agent.common.report_version_store import ReportVersion


@pytest.fixture
def store():
    return InMemoryReportVersionStore()


@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.ainvoke = AsyncMock()
    llm.bind_tools = MagicMock()
    return llm


@pytest.fixture
def followup(mock_llm, store):
    return ReportFollowup(llm=mock_llm, report_version_store=store, max_tool_iterations=5)


def _make_state(**overrides):
    defaults = {
        "messages": [HumanMessage(content="What does the report say about AI?")],
        "report_version_ids": ["v1"],
    }
    defaults.update(overrides)
    return ChatResearcherState(**defaults)


async def _seed_report(store, conversation_id="conv1", content="# Test Report\n\nThis is a test report about AI."):
    version = ReportVersion(conversation_id=conversation_id, content=content, triggering_query="initial query")
    await store.append(version)
    return version


class TestReportFollowup:
    async def test_qa_without_tools(self, followup, mock_llm, store):
        """When LLM responds without tool calls, the response goes directly to the user."""
        await _seed_report(store)

        response_msg = AIMessage(content="The report says AI is important.")
        response_msg.tool_calls = []
        mock_llm.bind_tools.return_value.ainvoke = AsyncMock(return_value=response_msg)

        state = _make_state()
        result = await followup.run(state, conversation_id="conv1")

        assert isinstance(result, dict)
        assert result["messages"][0].content == "The report says AI is important."

    async def test_no_report_returns_fallback(self, followup, mock_llm, store):
        """When no report exists, returns a fallback message without calling LLM."""
        state = _make_state()
        result = await followup.run(state, conversation_id="conv1")

        assert isinstance(result, dict)
        assert "no report" in result["messages"][0].content.lower()
        mock_llm.ainvoke.assert_not_called()

    async def test_edit_report_stores_new_version(self, store):
        """edit_report tool creates a new version with the edit applied."""
        version = await _seed_report(store, content="# Report\n\nXAS Fundamentals section.")

        followup = ReportFollowup(
            llm=MagicMock(),
            report_version_store=store,
            max_tool_iterations=5,
        )
        tools = followup._build_tools("conv1")
        edit_tool = next(t for t in tools if t.name == "edit_report")

        result = await edit_tool.ainvoke(
            {
                "search": "XAS Fundamentals",
                "replace": "X-ray Absorption Spectroscopy Fundamentals",
            }
        )

        assert "updated successfully" in result.lower()
        latest = await store.latest("conv1")
        assert "X-ray Absorption Spectroscopy Fundamentals" in latest.content
        assert "XAS Fundamentals" not in latest.content
        assert latest.parent_version_id == version.version_id

    async def test_edit_report_search_not_found(self, store):
        """edit_report returns error when search string isn't in the report."""
        await _seed_report(store)

        followup = ReportFollowup(llm=MagicMock(), report_version_store=store)
        tools = followup._build_tools("conv1")
        edit_tool = next(t for t in tools if t.name == "edit_report")

        result = await edit_tool.ainvoke({"search": "nonexistent text", "replace": "new text"})

        assert "error" in result.lower()
        assert "not found" in result.lower()

    async def test_rewrite_report_stores_new_version(self, store):
        """rewrite_report tool replaces the entire report content."""
        version = await _seed_report(store)

        followup = ReportFollowup(llm=MagicMock(), report_version_store=store)
        tools = followup._build_tools("conv1")
        rewrite_tool = next(t for t in tools if t.name == "rewrite_report")

        new_content = "# Completely New Report\n\nBrand new content."
        result = await rewrite_tool.ainvoke({"content": new_content})

        assert "rewritten successfully" in result.lower()
        latest = await store.latest("conv1")
        assert latest.content == new_content
        assert latest.parent_version_id == version.version_id

    async def test_read_report_returns_content(self, store):
        """read_report tool returns the current report text."""
        await _seed_report(store, content="Report content here.")

        followup = ReportFollowup(llm=MagicMock(), report_version_store=store)
        tools = followup._build_tools("conv1")
        read_tool = next(t for t in tools if t.name == "read_report")

        result = await read_tool.ainvoke({})
        assert result == "Report content here."

    async def test_read_report_no_report(self, store):
        """read_report returns message when no report exists."""
        followup = ReportFollowup(llm=MagicMock(), report_version_store=store)
        tools = followup._build_tools("conv1")
        read_tool = next(t for t in tools if t.name == "read_report")

        result = await read_tool.ainvoke({})
        assert "no report" in result.lower()

    async def test_request_deep_research_with_submitter(self, store):
        """request_deep_research calls the edit_job_submitter and returns job ID."""
        await _seed_report(store)

        mock_submitter = AsyncMock(return_value="job-abc-123")
        followup = ReportFollowup(
            llm=MagicMock(),
            report_version_store=store,
            edit_job_submitter=mock_submitter,
        )
        tools = followup._build_tools("conv1")
        dr_tool = next(t for t in tools if t.name == "request_deep_research")

        result = await dr_tool.ainvoke({"instruction": "Add a section on catalysts"})

        assert "job-abc-123" in result
        mock_submitter.assert_called_once_with("Add a section on catalysts", "conv1")

    async def test_request_deep_research_not_configured(self, store):
        """request_deep_research returns message when neither submitter nor fn is available."""
        await _seed_report(store)

        followup = ReportFollowup(llm=MagicMock(), report_version_store=store)
        tools = followup._build_tools("conv1")
        dr_tool = next(t for t in tools if t.name == "request_deep_research")

        result = await dr_tool.ainvoke({"instruction": "Add section"})
        assert "not configured" in result.lower()

    async def test_report_version_ids_updated_after_edit(self, store):
        """After an edit, report_version_ids in the return dict reflects all versions."""
        await _seed_report(store)

        followup = ReportFollowup(llm=MagicMock(), report_version_store=store, max_tool_iterations=5)

        # Simulate: LLM returns a tool call for edit_report, then a final response
        tool_call_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "name": "edit_report",
                    "args": {"search": "test report", "replace": "updated report"},
                }
            ],
        )
        final_msg = AIMessage(content="Done, I updated the report.")
        final_msg.tool_calls = []

        mock_llm = MagicMock()
        bound_llm = AsyncMock()
        # First call returns tool call, second returns final response
        bound_llm.ainvoke = AsyncMock(side_effect=[tool_call_msg, final_msg])
        mock_llm.bind_tools = MagicMock(return_value=bound_llm)

        followup.llm = mock_llm
        state = _make_state()
        result = await followup.run(state, conversation_id="conv1")

        assert "report_version_ids" in result
        assert len(result["report_version_ids"]) == 2  # original + edited
