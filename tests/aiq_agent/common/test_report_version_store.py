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

"""Tests for InMemoryReportVersionStore.

This suite doubles as a contract test: any future SQL-backed
ReportVersionStore implementation should pass these same assertions.
"""

import pytest

from aiq_agent.common.report_version_store import InMemoryReportVersionStore
from aiq_agent.common.report_version_store import ReportVersion
from aiq_agent.common.report_version_store import ReportVersionStore


@pytest.fixture
def store() -> InMemoryReportVersionStore:
    return InMemoryReportVersionStore()


def _make_version(
    conversation_id: str = "conv-1",
    *,
    version_id: str = "v1",
    content: str = "# Report",
    query: str = "test query",
    parent: str | None = None,
) -> ReportVersion:
    return ReportVersion(
        version_id=version_id,
        conversation_id=conversation_id,
        content=content,
        triggering_query=query,
        parent_version_id=parent,
    )


class TestReportVersionStoreProtocol:
    def test_in_memory_store_implements_protocol(self):
        assert isinstance(InMemoryReportVersionStore(), ReportVersionStore)


class TestAppend:
    async def test_append_single(self, store: InMemoryReportVersionStore):
        v = _make_version()
        await store.append(v)
        result = await store.list("conv-1")
        assert len(result) == 1
        assert result[0].version_id == "v1"

    async def test_append_preserves_order(self, store: InMemoryReportVersionStore):
        await store.append(_make_version(version_id="v1"))
        await store.append(_make_version(version_id="v2", parent="v1"))
        await store.append(_make_version(version_id="v3", parent="v2"))
        result = await store.list("conv-1")
        assert [v.version_id for v in result] == ["v1", "v2", "v3"]

    async def test_append_deduplicates(self, store: InMemoryReportVersionStore):
        v = _make_version(version_id="v1")
        await store.append(v)
        await store.append(v)
        result = await store.list("conv-1")
        assert len(result) == 1


class TestList:
    async def test_list_empty(self, store: InMemoryReportVersionStore):
        result = await store.list("nonexistent")
        assert result == []

    async def test_list_scoped_per_conversation(self, store: InMemoryReportVersionStore):
        await store.append(_make_version("conv-1", version_id="v1"))
        await store.append(_make_version("conv-2", version_id="v2"))
        assert len(await store.list("conv-1")) == 1
        assert len(await store.list("conv-2")) == 1
        assert (await store.list("conv-1"))[0].version_id == "v1"

    async def test_list_returns_copies(self, store: InMemoryReportVersionStore):
        await store.append(_make_version())
        a = await store.list("conv-1")
        b = await store.list("conv-1")
        assert a is not b


class TestGet:
    async def test_get_existing(self, store: InMemoryReportVersionStore):
        await store.append(_make_version(version_id="v1", content="first"))
        result = await store.get("conv-1", "v1")
        assert result is not None
        assert result.content == "first"

    async def test_get_missing_version(self, store: InMemoryReportVersionStore):
        await store.append(_make_version(version_id="v1"))
        assert await store.get("conv-1", "nonexistent") is None

    async def test_get_wrong_conversation(self, store: InMemoryReportVersionStore):
        await store.append(_make_version("conv-1", version_id="v1"))
        assert await store.get("conv-2", "v1") is None


class TestLatest:
    async def test_latest_empty(self, store: InMemoryReportVersionStore):
        assert await store.latest("nonexistent") is None

    async def test_latest_returns_last_appended(self, store: InMemoryReportVersionStore):
        await store.append(_make_version(version_id="v1", content="first"))
        await store.append(_make_version(version_id="v2", content="second", parent="v1"))
        result = await store.latest("conv-1")
        assert result is not None
        assert result.version_id == "v2"
        assert result.content == "second"


class TestReportVersionModel:
    def test_auto_generates_version_id(self):
        v = ReportVersion(conversation_id="c", content="x", triggering_query="q")
        assert len(v.version_id) == 12

    def test_auto_generates_created_at(self):
        v = ReportVersion(conversation_id="c", content="x", triggering_query="q")
        assert v.created_at is not None

    def test_parent_version_id_defaults_to_none(self):
        v = ReportVersion(conversation_id="c", content="x", triggering_query="q")
        assert v.parent_version_id is None
