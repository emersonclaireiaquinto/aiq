# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the report_followup node."""

import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from aiq_agent.agents.chat_researcher.models import ChatResearcherState
from aiq_agent.agents.chat_researcher.models import DepthDecision
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
    return llm


@pytest.fixture
def followup(mock_llm, store):
    return ReportFollowup(llm=mock_llm, report_version_store=store)


def _make_state(**overrides):
    defaults = {
        "messages": [HumanMessage(content="What does the report say about X?")],
        "report_version_ids": ["v1"],
    }
    defaults.update(overrides)
    return ChatResearcherState(**defaults)


def _llm_response(category, response="", edit_instruction=None):
    payload = {"category": category, "response": response, "edit_instruction": edit_instruction}
    msg = MagicMock()
    msg.content = json.dumps(payload)
    return msg


class TestReportFollowup:
    async def test_answer_from_report(self, followup, mock_llm, store):
        version = ReportVersion(conversation_id="conv1", content="Report about AI", triggering_query="q")
        await store.append(version)

        mock_llm.ainvoke.return_value = _llm_response("answer_from_report", "The report says X is important.")
        state = _make_state()

        result = await followup.run(state, conversation_id="conv1")

        assert isinstance(result, dict)
        assert result["messages"][0].content == "The report says X is important."

    async def test_gap_detected(self, followup, mock_llm, store):
        version = ReportVersion(conversation_id="conv1", content="Report about AI", triggering_query="q")
        await store.append(version)

        mock_llm.ainvoke.return_value = _llm_response("gap_detected", "The report doesn't cover X. Want me to add it?")
        state = _make_state()

        result = await followup.run(state, conversation_id="conv1")

        assert isinstance(result, dict)
        assert "doesn't cover" in result["messages"][0].content

    async def test_refine_report(self, followup, mock_llm, store):
        version = ReportVersion(conversation_id="conv1", content="Report about AI", triggering_query="q")
        await store.append(version)

        mock_llm.ainvoke.return_value = _llm_response(
            "refine_report", "Updating the report...", "Add a section on safety"
        )
        state = _make_state()

        result = await followup.run(state, conversation_id="conv1")

        assert isinstance(result, Command)
        assert result.goto == "deep_research"
        assert result.update["edit_instruction"] == "Add a section on safety"

    async def test_new_topic_falls_through_to_shallow(self, followup, mock_llm, store):
        version = ReportVersion(conversation_id="conv1", content="Report about AI", triggering_query="q")
        await store.append(version)

        mock_llm.ainvoke.return_value = _llm_response("new_topic")
        state = _make_state()

        result = await followup.run(state, conversation_id="conv1")

        assert isinstance(result, Command)
        assert result.goto == "shallow_research"

    async def test_new_topic_falls_through_to_deep_when_depth_is_deep(self, followup, mock_llm, store):
        version = ReportVersion(conversation_id="conv1", content="Report about AI", triggering_query="q")
        await store.append(version)

        mock_llm.ainvoke.return_value = _llm_response("new_topic")
        state = _make_state(depth_decision=DepthDecision(decision="deep", raw_reasoning="complex query"))

        result = await followup.run(state, conversation_id="conv1")

        assert isinstance(result, Command)
        assert result.goto == "clarifier"

    async def test_no_report_falls_through(self, followup, mock_llm, store):
        state = _make_state()

        result = await followup.run(state, conversation_id="conv1")

        assert isinstance(result, Command)
        assert result.goto == "shallow_research"
        mock_llm.ainvoke.assert_not_called()

    async def test_empty_query_returns_clarification(self, followup, mock_llm, store):
        version = ReportVersion(conversation_id="conv1", content="Report", triggering_query="q")
        await store.append(version)

        state = _make_state(messages=[])

        result = await followup.run(state, conversation_id="conv1")

        assert isinstance(result, dict)
        assert "clarify" in result["messages"][0].content.lower()

    async def test_llm_error_falls_through(self, followup, mock_llm, store):
        version = ReportVersion(conversation_id="conv1", content="Report", triggering_query="q")
        await store.append(version)

        mock_llm.ainvoke.side_effect = Exception("LLM timeout")
        state = _make_state()

        result = await followup.run(state, conversation_id="conv1")

        assert isinstance(result, Command)
        assert result.goto == "shallow_research"
