# Interface Control Document: AI-Q Backend API

**Version:** 1.3
**Date:** 2026-05-19
**Status:** Draft

---

## 1. Overview

The AI-Q backend exposes a REST + SSE API for conversational research agents. It supports two execution modes:

| Mode | Prefix | Execution | SSE Lifecycle | Use Case |
|------|--------|-----------|---------------|----------|
| **Chat (inline)** | `/v1/chat` | API server process | Persistent per conversation | Multi-turn chat, HITL, shallow + deep research |
| **Async Job (Dask)** | `/v1/jobs/async` | Dask worker | Terminates on job completion | Standalone deep research |

Both modes write events to the same `job_events` database table via `EventStore`. The Chat API is the primary interface; the Async Job API is an alternative for fire-and-forget research without conversation context.

Supporting APIs:

| Prefix | Purpose |
|--------|---------|
| `/v1/sessions` | Conversation CRUD, message persistence, report versions |
| `/v1/data_sources` | Available search/RAG tool discovery |
| `/v1/collections`, `/v1/documents` | Knowledge layer (document upload, RAG) |
| `/health` | Health check |

### 1.1 Authentication

All `/v1/sessions` and `/v1/chat` routes extract user identity from the `Authorization` header:

```
Authorization: Bearer <JWT>
```

The backend decodes the JWT payload (without cryptographic verification) and extracts the user ID from the `sub`, `user_id`, or `email` claim, in that order. If no valid token is present, the user ID defaults to `"default-user"`.

**Ownership enforcement:** Chat and session routes that operate on an existing conversation verify that the conversation's `user_id` matches the caller. Requests for conversations the caller does not own receive a `404` (not `403`) to avoid leaking conversation existence.

### 1.2 Base URL

Default: `http://localhost:{port}` (configurable; commonly 8000 or 9000 in dev).

### 1.3 Content Type

All request bodies are `application/json`. SSE streams use `text/event-stream`.

---

## 2. Session Persistence API

Prefix: `/v1/sessions`

Manages conversations, messages, report versions, and collection tracking. All conversation/message routes require a user ID (from auth header or default).

### 2.1 Conversations

#### Create Conversation

```
POST /v1/sessions/conversations
```

**Request:**
```json
{
  "id": "s_abc123",              // optional; auto-generated if omitted (format: s_{uuid.hex})
  "title": "My Research",        // optional; default "New Session", max 512 chars
  "enabled_data_source_ids": ["web_search", "paper_search"]  // optional
}
```

**Response (200):**
```json
{
  "id": "s_c1c91213b58147d59c29b5ee12f196de",
  "user_id": "default-user",
  "title": "My Research",
  "enabled_data_source_ids": ["web_search", "paper_search"],
  "created_at": "2026-05-19T18:19:44.995978+00:00",
  "updated_at": "2026-05-19T18:19:44.995978+00:00"
}
```

#### List Conversations

```
GET /v1/sessions/conversations?limit=100&offset=0
```

**Response (200):**
```json
{
  "conversations": [
    {
      "id": "s_abc123",
      "title": "My Research",
      "enabled_data_source_ids": ["web_search"],
      "created_at": "2026-05-19T18:19:44+00:00",
      "updated_at": "2026-05-19T18:22:47+00:00",
      "message_count": 6
    }
  ],
  "total": 1
}
```

Ordered by `updated_at` descending. `limit` range: 1-500. `offset` >= 0.

#### Get Conversation

```
GET /v1/sessions/conversations/{conversation_id}
```

**Response (200):**
```json
{
  "id": "s_abc123",
  "user_id": "default-user",
  "title": "My Research",
  "enabled_data_source_ids": [],
  "created_at": "...",
  "updated_at": "...",
  "messages": [ ... ],         // full message list (see Message schema)
  "report_versions": [ ... ]   // full report version list
}
```

**Response (404):** `{"detail": "Conversation not found"}`

#### Update Conversation

```
PUT /v1/sessions/conversations/{conversation_id}
```

**Request:**
```json
{
  "title": "Updated Title",                          // optional
  "enabled_data_source_ids": ["web_search"]           // optional
}
```

**Response (200):** `{"status": "ok"}`
**Response (404):** `{"detail": "Conversation not found"}`

#### Delete Conversation

```
DELETE /v1/sessions/conversations/{conversation_id}
```

**Response (200):** `{"status": "ok"}`
**Response (404):** `{"detail": "Conversation not found"}`

#### Bulk Delete

```
POST /v1/sessions/conversations/bulk-delete
```

**Request:**
```json
{
  "conversation_ids": ["s_abc123", "s_def456"]   // 1-100 IDs
}
```

**Response (200):** `{"deleted": 2}`

### 2.2 Messages

#### List Messages

```
GET /v1/sessions/conversations/{conversation_id}/messages?limit=1000&offset=0
```

**Response (200):**
```json
{
  "messages": [
    {
      "id": "a193293332f0",
      "conversation_id": "s_abc123",
      "role": "user",
      "content": "Write a report on AI for X-ray spectroscopy",
      "message_type": "user",
      "metadata": {},
      "created_at": "2026-05-19T18:20:00+00:00"
    }
  ]
}
```

Ordered by `created_at` ascending. `limit` range: 1-5000.

**Message Schema:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique message ID |
| `conversation_id` | string | Parent conversation |
| `role` | string | `"user"` or `"assistant"` |
| `content` | string | Message text |
| `message_type` | string\|null | See Message Types below |
| `metadata` | object | Arbitrary key-value pairs |
| `created_at` | string | ISO 8601 timestamp |

**Message Types:**

| Type | Role | Description |
|------|------|-------------|
| `user` | user | Normal user message |
| `interaction_response` | user | Response to a HITL prompt |
| `prompt` | assistant | HITL prompt (clarification, plan approval) |
| `agent_response` | assistant | Agent's final response for a turn |

#### Append Messages

```
POST /v1/sessions/conversations/{conversation_id}/messages
```

**Request:**
```json
{
  "messages": [
    {
      "id": "optional_id",
      "role": "user",
      "content": "Hello",
      "message_type": "user",
      "created_at": "2026-05-19T18:20:00+00:00"
    }
  ]
}
```

Uses `ON CONFLICT DO NOTHING` on the `id` primary key (idempotent). Updates the conversation's `updated_at`.

**Response (200):** `{"message_ids": ["a193293332f0"]}`

#### Update Message

```
PUT /v1/sessions/conversations/{conversation_id}/messages/{message_id}
```

**Request:**
```json
{
  "content": "Updated text",     // optional
  "message_type": "user",        // optional
  "metadata": {"key": "value"}   // optional; merged into existing metadata
}
```

**Response (200):** `{"status": "ok"}`
**Response (404):** `{"detail": "Message not found"}`

### 2.3 Report Versions

Report versions track iterative edits to a research report within a conversation.

#### List Report Versions

```
GET /v1/sessions/conversations/{conversation_id}/report-versions
```

**Response (200):**
```json
{
  "report_versions": [
    {
      "version_id": "rv_001",
      "conversation_id": "s_abc123",
      "parent_version_id": null,
      "content": "# Report Title\n\n...",
      "triggering_query": "Write a report on AI",
      "created_at": "2026-05-19T18:25:00+00:00"
    },
    {
      "version_id": "rv_002",
      "conversation_id": "s_abc123",
      "parent_version_id": "rv_001",
      "content": "# Report Title (Revised)\n\n...",
      "triggering_query": "Expand the section on error correction",
      "created_at": "2026-05-19T18:30:00+00:00"
    }
  ]
}
```

Ordered by `created_at` ascending. Versions form a linked list via `parent_version_id`.

#### Append Report Version

```
POST /v1/sessions/conversations/{conversation_id}/report-versions
```

**Request:**
```json
{
  "version_id": "rv_002",           // optional; auto-generated if omitted
  "parent_version_id": "rv_001",    // optional; null for the first version
  "content": "# Full report text",
  "triggering_query": "Expand section 3"  // optional; default ""
}
```

Idempotent: if `version_id` already exists, the insert is skipped.

**Response (200):** `{"version_id": "rv_002"}`

### 2.4 Collection Tracking

Tracks which knowledge-layer collections a user has interacted with.

```
GET  /v1/sessions/collections/known                  -> {"session_ids": ["col_1", "col_2"]}
POST /v1/sessions/collections/known/{session_id}      -> {"status": "ok"}
DELETE /v1/sessions/collections/known/{session_id}    -> {"status": "ok"}
```

### 2.5 Migration

One-time import of conversations from frontend localStorage.

```
POST /v1/sessions/migrate
```

**Request:**
```json
{
  "conversations": [
    {
      "id": "s_old_123",
      "title": "Imported Session",
      "enabledDataSourceIds": ["web_search"],
      "messages": [
        {"id": "m1", "role": "user", "content": "Hello", "messageType": "user", "timestamp": "..."}
      ],
      "reportVersions": [
        {"versionId": "v1", "parentVersionId": null, "content": "...", "triggeringQuery": "..."}
      ]
    }
  ],
  "current_conversation_id": "s_old_123"
}
```

Skips conversations whose ID already exists. Note camelCase field names in the request (frontend convention) vs snake_case in the backend.

**Response (200):** `{"imported": 1}`

---

## 3. Chat API

Prefix: `/v1/chat`

The primary interface for conversational agent interaction. Runs the agent inline in the API server process. All endpoints authenticate the caller and enforce conversation ownership (see Section 1.1).

### 3.1 Send Message

```
POST /v1/chat
Authorization: Bearer <JWT>
```

**Request:**
```json
{
  "conversation_id": "s_abc123",   // null -> auto-creates conversation
  "message": "Write a report on AI for X-ray spectroscopy",
  "data_sources": ["web_search", "paper_search"]  // optional; null = all sources
}
```

The backend reads report context from the `ReportVersionStore` automatically for follow-up turns — the frontend does not need to send it.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `conversation_id` | string\|null | max 64 chars | Existing conversation ID, or null to create one |
| `message` | string | 1-100,000 chars | User message text |
| `data_sources` | string[]\|null | | Filter to specific data sources |

**Response (202):**
```json
{
  "conversation_id": "s_c1c91213b58147d59c29b5ee12f196de",
  "message_id": "a193293332f0",
  "status": "processing"
}
```

**Error Responses:**
- **404:** `{"detail": "Conversation not found"}` — `conversation_id` doesn't exist or caller doesn't own it.
- **409:** `{"detail": "Conversation already has an active task"}` — another message is still being processed.
- **503:** `{"detail": "Chat runner not initialized"}` — no DB configured.

**Side Effects:**
- When `conversation_id` is null, a new conversation is created and owned by the authenticated user.
- When `conversation_id` is provided, the backend verifies the caller owns the conversation before proceeding.
- Persists the user message to the conversation's message list.
- Starts agent processing as a background asyncio task.
- Events are emitted to the conversation's event store (consumed via the SSE stream).

### 3.2 Respond to HITL Prompt

```
POST /v1/chat/{conversation_id}/respond
Authorization: Bearer <JWT>
```

**Request:**
```json
{
  "response": "approve",
  "prompt_id": "26dd8cdf-5adc-42d5-a101-9d5748a320ac"  // optional
}
```

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `response` | string | 1-100,000 chars | User's response text |
| `prompt_id` | string\|null | | ID of the prompt being responded to (from `interaction.required` event) |

**Response (200):** `{"status": "ok"}`
**Response (404):** `{"detail": "Conversation not found"}` or `{"detail": "No pending interaction for this conversation"}`

**Side Effects:**
- Verifies conversation ownership before proceeding.
- Persists the response as an `interaction_response` message.
- Resolves the asyncio.Future that the agent is awaiting, unblocking agent execution.

### 3.3 Event Stream (SSE)

```
GET /v1/chat/{conversation_id}/events?last_event_id=0
Authorization: Bearer <JWT>
```

Verifies conversation ownership, then returns a Server-Sent Events stream scoped to the conversation. **This stream stays open indefinitely** — it does NOT close when a turn completes. The same EventSource receives events from all subsequent turns and deep research escalations.

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `last_event_id` | int | 0 | Resume cursor; replay events with `id > last_event_id` |

**Response Headers:**
```
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```

**SSE Frame Format:**
```
id: {sequence_number}
event: {event_type}
data: {json_payload}

```

Each frame has a trailing blank line. The `id` field is a monotonically increasing integer that serves as the reconnect cursor.

**Reconnection:** If the SSE connection drops, reconnect with `?last_event_id=N` where N is the last `id` received. All missed events are replayed, then the stream transitions to live mode.

**Delivery Modes:**
- **PostgreSQL:** Uses `LISTEN/NOTIFY` for sub-10ms push-based delivery. Falls back to polling on heartbeat timeout.
- **SQLite:** Uses 0.5s polling interval.

The stream emits a `stream.start` event immediately on connection, followed by a `stream.mode` event indicating the delivery mechanism.

### 3.4 Cancel Processing

```
POST /v1/chat/{conversation_id}/cancel
Authorization: Bearer <JWT>
```

Verifies conversation ownership before cancelling.

**Response (200):** `{"status": "cancelled"}`
**Response (404):** `{"detail": "Conversation not found"}` or `{"detail": "No active task for this conversation"}`

---

## 4. Chat SSE Event Catalog

Events are listed in the order they typically appear during a research session.

### 4.1 Stream Lifecycle Events

| Event Type | Payload | Description |
|------------|---------|-------------|
| `stream.start` | `{"conversation_id": "...", "mode": "pubsub"\|"polling"}` | Emitted immediately on SSE connection |
| `stream.mode` | `{"mode": "polling"\|"pubsub"\|"live"}` | Transition marker: `"live"` means historical replay is complete |
| `heartbeat` | `{"ts": "2026-05-19T18:20:59+00:00"}` | Keepalive, every ~15 seconds |

### 4.2 Chat Turn Events

| Event Type | Payload | Description |
|------------|---------|-------------|
| `chat.start` | `{"data": {"message": "user's query"}}` | Agent begins processing a message |
| `chat.response` | `{"data": {"content": "...", "report_version": ...}}` | Agent's final response (see below) |
| `chat.complete` | `{"data": {}}` | Turn finished (stream remains open for next turn) |
| `chat.error` | `{"data": {"error": "...", "error_type": "..."}}` | Turn failed with error |
| `chat.cancelled` | `{"data": {}}` | Turn was cancelled via `/cancel` |

**`chat.response` payload:**

The `data` object always contains `content` (string) and optionally `report_version` (object or absent):

```json
{
  "data": {
    "content": "Done — I updated the report title.",
    "report_version": {
      "versionId": "14b39e28070e",
      "parentVersionId": "883757cf3461",
      "content": "# Full updated report...",
      "triggeringQuery": "edit: old title -> new title",
      "parentContent": "# Full previous report..."
    }
  }
}
```

- `content` is always the agent's text message (never JSON-encoded).
- `report_version` is present only when the agent modified the report during this turn. See Section 8.8 for field details.
- When no report was modified: `{"data": {"content": "The answer is..."}}`

### 4.3 Agent Pipeline Events (from NAT IntermediateStep)

These are forwarded from the NAT framework's step instrumentation:

| Event Type | Payload | Description |
|------------|---------|-------------|
| `step.WORKFLOW_START` | `{"data": {"id", "parent_id", "name", "payload"}}` | Top-level agent graph starts |
| `step.WORKFLOW_END` | `{"data": {"id", "parent_id", "name", "payload"}}` | Top-level agent graph ends |
| `step.FUNCTION_START` | `{"data": {"id", "parent_id", "name", "payload"}}` | Agent node/function starts |
| `step.FUNCTION_END` | `{"data": {"id", "parent_id", "name", "payload"}}` | Agent node/function ends |
| `step.LLM_START` | `{"data": {"id", "parent_id", "name", "payload"}}` | LLM call begins |
| `step.LLM_END` | `{"data": {"id", "parent_id", "name", "payload"}}` | LLM call ends |
| `step.TOOL_START` | `{"data": {"id", "parent_id", "name", "payload"}}` | Tool call begins (followup agent tools) |
| `step.TOOL_END` | `{"data": {"id", "parent_id", "name", "payload"}}` | Tool call ends |

The `payload` field is a JSON string containing the full `IntermediateStepPayload` with event metadata, inputs/outputs, and usage info.

**Note:** `step.*` events are emitted by the NAT framework for the inline chat agent's execution. Deep research events from the Dask worker use the unprefixed forms (`tool.start`, `llm.start`, etc. — see Section 4.6). Both may appear on the same SSE stream during a follow-up deep research turn.

### 4.4 HITL (Human-in-the-Loop) Events

| Event Type | Payload | Description |
|------------|---------|-------------|
| `interaction.required` | See below | Agent needs user input to proceed |
| `interaction.resolved` | `{"data": {"prompt_id": "..."}}` | User responded; agent resuming |
| `interaction.timeout` | `{"data": {"prompt_id": "..."}}` | No response within timeout (default 300s) |

**`interaction.required` Payload:**
```json
{
  "data": {
    "prompt_id": "26dd8cdf-5adc-42d5-a101-9d5748a320ac",
    "text": "Which report should I prepare?\n\n1. Option A\n2. Option B\n...",
    "input_type": "text",
    "timeout": null,
    "options": null
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `prompt_id` | string | Use this when calling `/v1/chat/{id}/respond` |
| `text` | string | The question/prompt to display to the user |
| `input_type` | string | Input type hint (typically `"text"`) |
| `timeout` | int\|null | Seconds before timeout; null = default 300s |
| `options` | array\|null | Structured options if applicable |

### 4.5 Deep Research Escalation Events

When the inline agent escalates to deep research, it submits a Dask job. An **event bridge** background task then polls the job's event store and re-emits all events into the conversation stream.

| Event Type | Payload | Description |
|------------|---------|-------------|
| `deep_research.started` | See below | Dask job submitted; bridge started |
| `deep_research.complete` | `{"data": {"job_id": "...", "status": "success"\|"failure"\|"interrupted"}}` | Job finished; bridge exiting |

Between these two events, deep research events (see Section 4.6) flow through the bridge onto the same SSE stream.

**`deep_research.started` Payload:**

| Field | Type | Present | Description |
|-------|------|---------|-------------|
| `job_id` | string | Always | Dask job ID |
| `is_followup` | boolean | Follow-up only | `true` when this is a follow-up research request on an existing report |
| `backend_integration` | boolean | Follow-up only | `true` when the backend will automatically integrate the research result into the report |

**Initial deep research** (no existing report):
```json
{"data": {"job_id": "c1fd2a7d-..."}}
```

**Follow-up deep research** (existing report):
```json
{"data": {"job_id": "c1fd2a7d-...", "is_followup": true, "backend_integration": true}}
```

When `backend_integration` is `true`, the backend waits for the deep research job to complete, retrieves the final report, and feeds it back to the report follow-up agent for integration via `edit_report`/`rewrite_report` — all within the same chat turn. The client does not need to send any additional messages to trigger integration. The integrated result is returned via `chat.response` after `deep_research.complete`.

### 4.6 Deep Research Events (via Event Bridge)

These events originate from the Dask worker's `AgentEventCallback` and are re-emitted into the conversation stream by the event bridge.

| Event Type | Payload | Description |
|------------|---------|-------------|
| `workflow.start` | `{"id", "name", "timestamp", "data": {"input": "..."}}` | Sub-agent starts (e.g., researcher agent) |
| `workflow.end` | `{"id", "name", "timestamp", "data": {"output": "..."}}` | Sub-agent ends |
| `llm.start` | `{"id", "name", "timestamp", "metadata": {"message_count": N}}` | LLM inference begins |
| `llm.chunk` | `{"data": {"chunk": "token"}}` | Streaming token (if enabled) |
| `llm.end` | `{"id", "name", "timestamp", "metadata": {"thinking": "...", "usage": {...}}}` | LLM inference ends |
| `tool.start` | `{"id", "name", "timestamp", "data": {"input": ...}}` | Tool invocation begins |
| `tool.end` | `{"id", "name", "timestamp"}` | Tool invocation ends |
| `artifact.update` | See below | Structured artifact emitted |
| `job.update` | `{"name": "retry", "data": {"output": "Retrying ..."}}` | Retry/error hint |
| `job.heartbeat` | `{"data": {"uptime_seconds": 150}}` | Worker heartbeat (every 30s) |

**`artifact.update` Payload:**
```json
{
  "id": "uuid",
  "name": "optional_name",
  "timestamp": "2026-05-19T18:25:47+00:00",
  "data": {
    "type": "<artifact_type>",
    "content": "...",
    ...
  },
  "metadata": {
    "workflow": "researcher_agent",
    "agent_id": "run-uuid"
  }
}
```

**Artifact Types:**

| Type | `data` Fields | Description |
|------|---------------|-------------|
| `file` | `content`, `file_path` | File written (e.g., `/report.md`) |
| `output` | `content`, `output_category` | LLM output artifact |
| `citation_source` | `content`, `url`, `tool` | URL discovered by a search tool |
| `citation_use` | `content`, `url` | URL cited in output text |
| `todo` | `content` (array of `{content, status}`) | Progress checklist from orchestrator |

**Output Categories** (in `output` artifacts):

| Category | Meaning |
|----------|---------|
| `final_report` | The completed, verified research report |
| `research_notes` | Intermediate researcher agent output |
| `draft` | Orchestrator draft output |
| `intermediate` | Other intermediate output |

**Todo Statuses:** `"pending"`, `"in_progress"`, `"completed"`

Typical todo sequence for deep research:
```
Task decomposition -> Save request -> Planning -> Research -> Verify -> Synthesize -> Write report -> Final verification
```

---

## 5. Async Job API

Prefix: `/v1/jobs/async`

Alternative API for standalone deep research without conversation context. Requires Dask (`NAT_DASK_SCHEDULER_ADDRESS`) and a job store DB (`NAT_JOB_STORE_DB_URL`).

### 5.1 List Agents

```
GET /v1/jobs/async/agents
```

**Response (200):**
```json
{
  "agents": [
    {"agent_type": "deep_researcher", "description": "Performs comprehensive multi-loop deep research"},
    {"agent_type": "shallow_researcher", "description": "Performs quick single-turn research"}
  ]
}
```

### 5.2 Submit Job

```
POST /v1/jobs/async/submit
```

**Request:**
```json
{
  "agent_type": "deep_researcher",
  "input": "What are the latest advances in quantum computing?",
  "job_id": null,               // optional; auto-generated if omitted
  "expiry_seconds": 86400       // optional; default from config, range 600-604800
}
```

**Response (200):**
```json
{
  "job_id": "c1fd2a7d-f351-4608-a626-df73334ed4bc",
  "status": "submitted",
  "agent_type": "deep_researcher",
  "error": null,
  "created_at": null
}
```

**Error Responses:**
- **400:** Unknown agent type.
- **503:** Dask scheduler not available.

### 5.3 Get Job Status

```
GET /v1/jobs/async/job/{job_id}
```

**Response (200):**
```json
{
  "job_id": "c1fd2a7d-...",
  "status": "running",
  "agent_type": null,
  "error": null,
  "created_at": "2026-05-19T18:22:46+00:00"
}
```

**Job Statuses:** `submitted` -> `running` -> `success` | `failure` | `interrupted`

**Response (404):** `{"detail": "Job not found: ..."}`

### 5.4 Stream Job Events (SSE)

```
GET /v1/jobs/async/job/{job_id}/stream
GET /v1/jobs/async/job/{job_id}/stream/{last_event_id}   # resume after disconnect
```

**Key difference from Chat SSE:** This stream **terminates** after the terminal `job.status` event. The backend drains all pending events before emitting the terminal status to ensure no data is lost.

**SSE Event Types:**

Same deep research events as Section 4.6, plus:

| Event Type | Payload | Description |
|------------|---------|-------------|
| `stream.mode` | `{"mode": "polling"\|"pubsub", ...}` | Delivery mode |
| `job.status` | `{"status": "success"\|"failure"\|"interrupted", "error": "..."}` | Terminal — stream closes after this |
| `job.error` | `{"error": "..."}` | Fatal error |
| `job.shutdown` | `{"message": "Server shutting down"}` | Server is shutting down |

On reconnection (`last_event_id > 0`), the payload includes `"reconnected": true` in the terminal `job.status` event.

### 5.5 Get Job Artifacts

```
GET /v1/jobs/async/job/{job_id}/state
```

**Response (200):**
```json
{
  "job_id": "c1fd2a7d-...",
  "has_state": true,
  "state": null,
  "artifacts": {
    "tools": [
      {
        "id": "tool-uuid",
        "name": "tavily_search",
        "input": "quantum computing advances",
        "output": "...",
        "status": "completed",
        "workflow": "researcher_agent",
        "timestamp": "2026-05-19T18:23:00+00:00"
      }
    ],
    "outputs": [
      {
        "type": "output",
        "content": "...",
        "output_category": "final_report",
        "workflow": "researcher_agent",
        "timestamp": "..."
      }
    ],
    "sources": {
      "found": 136,
      "cited": 14,
      "found_urls": ["https://..."],
      "cited_urls": ["https://..."]
    }
  }
}
```

### 5.6 Get Final Report

```
GET /v1/jobs/async/job/{job_id}/report
```

**Response (200):**
```json
{
  "job_id": "c1fd2a7d-...",
  "has_report": true,
  "report": "# AI for X-ray Spectroscopy\n\n## Introduction\n\n..."
}
```

### 5.7 Cancel Job

```
POST /v1/jobs/async/job/{job_id}/cancel
```

**Response (200):**
```json
{
  "job_id": "c1fd2a7d-...",
  "status": "interrupted",
  "task_cancelled": true
}
```

**Error Responses:**
- **400:** Job not in RUNNING state.
- **404:** Job not found.

---

## 6. Data Sources API

```
GET /v1/data_sources
```

**Response (200):**
```json
[
  {
    "id": "web_search",
    "name": "Web Search",
    "description": "Search the web for real-time information.",
    "requires_auth": false
  },
  {
    "id": "paper_search",
    "name": "Academic Papers",
    "description": "Search academic papers and scientific publications.",
    "requires_auth": false
  },
  {
    "id": "knowledge_layer",
    "name": "Uploaded Documents",
    "description": "Search your uploaded documents.",
    "requires_auth": false
  }
]
```

Data sources are registered dynamically from the YAML config's `data_sources` function (`_type: data_source_registry`). The `id` values are what you pass in `data_sources` arrays on chat requests and conversation settings.

---

## 7. Health Check

### 7.1 Basic Health (always available)

```
GET /health
```

Provided by the NAT framework. Returns a simple status.

**Response (200):**
```json
{
  "status": "healthy"
}
```

### 7.2 Extended Health (when Dask is configured)

When Dask is available, an extended health check is registered at the same path. It validates DB connectivity and reports Dask availability. If it overrides the basic endpoint depends on registration order; clients should handle both response shapes.

**Response (200):**
```json
{
  "status": "ok",
  "dask_available": true,
  "db": "ok"
}
```

**Response (503):**
```json
{
  "status": "degraded",
  "dask_available": true,
  "db": "unreachable"
}
```

### 7.3 Knowledge Health

```
GET /v1/knowledge/health
```

Checks if the knowledge backend (RAG) is reachable.

**Response (200):**
```json
{
  "status": "healthy",
  "backend": "llamaindex"
}
```

**Response (503):** `{"detail": "Knowledge backend unhealthy"}`

---

## 8. Frontend Integration Sequence

### 8.1 Application Boot

```
1. GET /v1/data_sources                     -> populate source toggles
2. GET /v1/sessions/conversations?limit=100 -> populate sidebar
3. GET /health                              -> verify backend connectivity (optional)
```

### 8.2 New Conversation

```
1. POST /v1/sessions/conversations          -> get conversation_id
2. GET  /v1/chat/{id}/events?last_event_id=0  -> open SSE stream (keep alive)
```

### 8.3 Send Message (each turn)

```
1. POST /v1/chat  {conversation_id, message, data_sources?}  -> 202
2. SSE delivers: chat.start -> step.* -> ... -> chat.response -> chat.complete
3. (stream stays open for next turn)
```

### 8.4 HITL Interaction

```
1. SSE delivers: interaction.required  {prompt_id, text, ...}
2. Display prompt to user, collect input
3. POST /v1/chat/{id}/respond  {response, prompt_id}
4. SSE delivers: interaction.resolved
5. Agent continues processing
```

### 8.5 Deep Research Escalation (Initial)

When the agent decides the query needs deep research (no existing report):

```
1. SSE delivers: deep_research.started  {job_id}
2. SSE delivers: chat.response  {"content": "Deep research job submitted. Job ID: ..."}
3. SSE delivers: chat.complete                          ← inline turn ends here
4. SSE delivers: tool.start, artifact.update, ...       ← bridged from Dask job
5. SSE delivers: deep_research.complete  {job_id, status}
6. Optionally: GET /v1/jobs/async/job/{job_id}/report   -> fetch final report
7. Optionally: GET /v1/jobs/async/job/{job_id}/state    -> fetch artifacts/sources
```

**Important timing:** `chat.response` and `chat.complete` arrive *before* the deep research events. The inline agent's turn completes immediately after submitting the Dask job. The event bridge then runs independently, piping deep research progress events onto the same SSE stream. Clients should not treat `chat.complete` as "all done" when `deep_research.started` has been received — wait for `deep_research.complete` instead.

### 8.5.1 Follow-up Deep Research (Backend-Integrated)

When the report follow-up agent requests additional deep research on an existing report, the backend handles the entire integration cycle within a single chat turn:

```
1. SSE delivers: step.TOOL_START  {name: "request_deep_research"}
2. SSE delivers: deep_research.started  {job_id, is_followup: true, backend_integration: true}
3. SSE delivers: tool.start, artifact.update, llm.start, ...  (bridged from Dask job)
4. SSE delivers: step.TOOL_END    {name: "request_deep_research"}  ← research returned to followup agent
5. SSE delivers: deep_research.complete  {job_id, status}
6. SSE delivers: step.TOOL_START  {name: "read_report"}            ← agent reads current report
7. SSE delivers: step.TOOL_END    {name: "read_report"}
8. SSE delivers: step.TOOL_START  {name: "edit_report"}            ← agent integrates findings
9. SSE delivers: step.TOOL_END    {name: "edit_report"}
10. SSE delivers: chat.response  {content: "Done — expanded section", report_version: {...}}
11. SSE delivers: chat.complete
```

**Key difference from initial deep research:** Here `chat.complete` arrives *after* `deep_research.complete` because the backend blocks the inline turn while waiting for the Dask job. The follow-up agent then reads the result, integrates it via `edit_report`/`rewrite_report`, and only then completes the turn.

The client does not need to send an additional message to trigger integration. The `chat.response` payload includes an embedded `report_version` object with the updated report content (see Section 8.8 for response format).

**Followup tool names visible in `step.TOOL_START`/`step.TOOL_END`:**

| Tool | Purpose |
|------|---------|
| `read_report` | Reads the current report content |
| `edit_report` | Applies a targeted search-and-replace edit to the report |
| `rewrite_report` | Replaces the entire report content |
| `request_deep_research` | Submits a deep research job and waits for completion |

### 8.5.2 Simple Report Edit (No Deep Research)

When a follow-up message requests a simple change (e.g., rename title, rephrase a paragraph):

```
1. SSE delivers: chat.start
2. SSE delivers: step.TOOL_START  {name: "read_report"}
3. SSE delivers: step.TOOL_END    {name: "read_report"}
4. SSE delivers: step.TOOL_START  {name: "edit_report"}
5. SSE delivers: step.TOOL_END    {name: "edit_report"}
6. SSE delivers: chat.response  {content: "Done — title updated", report_version: {...}}
7. SSE delivers: chat.complete
```

No `deep_research.started`/`deep_research.complete` events. The edit is performed synchronously within the chat turn.

### 8.6 Reconnection After Disconnect

```
1. Record the last SSE `id` value received
2. GET /v1/chat/{id}/events?last_event_id=42
3. All missed events are replayed
4. SSE delivers: stream.mode {"mode": "live"}  -> replay complete, now live
```

### 8.7 Resume Existing Conversation

```
1. GET /v1/sessions/conversations/{id}        -> get messages + report versions
2. GET /v1/chat/{id}/events?last_event_id=0   -> open SSE stream
3. POST /v1/chat  {conversation_id, message}  -> send new message
```

---

### 8.8 Report Version in Chat Response

When the report follow-up agent modifies the report during a chat turn (via `edit_report` or `rewrite_report`), the `chat.response` event's `data` object includes a `report_version` field alongside the text `content`:

```json
{
  "data": {
    "content": "Done — I updated the report title to: ...",
    "report_version": {
      "versionId": "rv_003",
      "parentVersionId": "rv_002",
      "content": "# Full Updated Report\n\n...",
      "triggeringQuery": "edit: old text... -> new text...",
      "parentContent": "# Previous Report Content\n\n..."
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `data.content` | string | Agent's conversational response text (always present, always plain text) |
| `data.report_version` | object\|absent | Present only when the report was modified |
| `data.report_version.versionId` | string | Unique ID for this version |
| `data.report_version.parentVersionId` | string\|null | Previous version ID (for diff computation) |
| `data.report_version.content` | string | Full report content after the edit |
| `data.report_version.triggeringQuery` | string | Description of what triggered this edit |
| `data.report_version.parentContent` | string\|null | Full content of the parent version (for diff display) |

Check for `report_version` in the parsed `data` object. If absent, no report was modified.

---

## 9. Database Schema

The backend uses two sets of tables:

### 9.1 Session Tables (managed by `aiq_api.sessions`)

Require PostgreSQL (uses `JSONB` column type).

**`conversations`**

| Column | Type | Description |
|--------|------|-------------|
| `id` | VARCHAR(64) PK | Conversation ID (format: `s_{uuid.hex}`) |
| `user_id` | VARCHAR(128) | User identity |
| `title` | VARCHAR(512) | Display title |
| `enabled_data_source_ids` | JSONB | Array of enabled source IDs |
| `created_at` | TIMESTAMP WITH TZ | Creation time |
| `updated_at` | TIMESTAMP WITH TZ | Last activity time |

**`messages`**

| Column | Type | Description |
|--------|------|-------------|
| `id` | VARCHAR(64) PK | Message ID |
| `conversation_id` | VARCHAR(64) | FK to conversations |
| `role` | VARCHAR(16) | `"user"` or `"assistant"` |
| `content` | TEXT | Message content |
| `message_type` | VARCHAR(32) | See Message Types (Section 2.2) |
| `metadata_` | JSONB | Arbitrary metadata |
| `created_at` | TIMESTAMP WITH TZ | Creation time |

**`report_versions`**

| Column | Type | Description |
|--------|------|-------------|
| `version_id` | VARCHAR(64) PK | Version ID |
| `conversation_id` | VARCHAR(64) | FK to conversations |
| `parent_version_id` | VARCHAR(64) | Previous version (linked list) |
| `content` | TEXT | Full report content |
| `triggering_query` | TEXT | Query that produced this version |
| `created_at` | TIMESTAMP WITH TZ | Creation time |

**`session_collections`**

| Column | Type | Description |
|--------|------|-------------|
| `session_id` | VARCHAR(64) PK | Knowledge-layer collection ID |
| `user_id` | VARCHAR(128) | User identity |
| `created_at` | TIMESTAMP WITH TZ | When marked |

### 9.2 Event Store Table (managed by `aiq_api.jobs`)

Works with both PostgreSQL and SQLite.

**`job_events`**

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK AUTOINCREMENT | Event sequence number (SSE cursor) |
| `job_id` | VARCHAR(64) | Job ID or conversation ID |
| `event_type` | VARCHAR(64) | Event type string |
| `event_data` | TEXT | JSON-encoded event payload |
| `created_at` | TIMESTAMP | Server default `now()` |

Index: `idx_job_events_job_id_id` on `(job_id, id)` for efficient cursor-based queries.

For PostgreSQL, each `INSERT` also issues `pg_notify('job_events_{job_id}', '{"id": N, "type": "..."}')` for push-based SSE delivery.

### 9.3 Job Store Table (managed by NAT)

**`job_info`** — managed by NAT's `JobStore`, not by this codebase. Key columns:

| Column | Type | Description |
|--------|------|-------------|
| `job_id` | VARCHAR PK | Job ID |
| `status` | VARCHAR | `submitted`, `running`, `success`, `failure`, `interrupted` |
| `output` | TEXT | JSON result (contains `{"report": "..."}` on success) |
| `error` | TEXT | Error message on failure |
| `is_expired` | BOOLEAN | Set by NAT's periodic cleanup |
| `created_at` | TIMESTAMP | Job creation time |

---

## 10. Error Handling

All error responses use standard HTTP status codes with a JSON body:

```json
{"detail": "Human-readable error message"}
```

| Status | Meaning |
|--------|---------|
| 400 | Bad request (invalid input, unknown agent type) |
| 404 | Resource not found, or caller does not own the resource (conversation, job, message). Uses 404 instead of 403 for ownership failures to avoid leaking existence. |
| 409 | Conflict (conversation already has active task) |
| 503 | Service unavailable (store not initialized, Dask not available) |

---

## 11. Configuration

Key environment variables:

| Variable | Purpose | Default |
|----------|---------|---------|
| `AIQ_SESSIONS_DB` | PostgreSQL URL for session persistence | Falls back to `NAT_JOB_STORE_DB_URL` |
| `NAT_JOB_STORE_DB_URL` | PostgreSQL URL for job store + event store | `sqlite:///./data/jobs.db` |
| `NAT_DASK_SCHEDULER_ADDRESS` | Dask scheduler for async jobs | (none; async jobs disabled) |
| `NAT_CONFIG_FILE` | Path to NAT YAML config | (required for async jobs) |
| `AIQ_LISTEN_DB_URL` | Direct PostgreSQL URL for LISTEN/NOTIFY (bypasses PgBouncer) | Same as `NAT_JOB_STORE_DB_URL` |
| `AIQ_VERBOSE` | Enable verbose LLM tracing | `false` |

The front-end config in YAML also sets:

```yaml
front_end:
  _type: aiq_api
  db_url: "sqlite+aiosqlite:///./jobs.db"    # default
  expiry_seconds: 86400                       # job TTL, range 600-604800
```
