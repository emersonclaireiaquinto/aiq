# Frontend Developer Guide: AI-Q

A practical guide for building a frontend against the AI-Q backend API. Read alongside `ICD-backend-api.md` for endpoint schemas and payload formats.

---

## 1. Architecture at a Glance

```
┌─────────────────────────────────────────────────────────┐
│  Frontend                                               │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────────┐  │
│  │ Sidebar  │  │  Chat    │  │  Report Panel         │  │
│  │ (convos) │  │  (msgs)  │  │  (rendered markdown)  │  │
│  └──────────┘  └──────────┘  └───────────────────────┘  │
│       │              │  ▲               ▲                │
│       │         POST │  │ SSE           │ report_version │
│       │              │  │               │ from SSE       │
└───────┼──────────────┼──┼───────────────┼────────────────┘
        │              │  │               │
   REST CRUD      REST+SSE          parsed from
   /v1/sessions   /v1/chat          chat.response
```

The frontend maintains three core pieces of state:

1. **Conversation list** — from `/v1/sessions/conversations`
2. **Active conversation** — messages + report versions, fed by REST + SSE
3. **Report content** — extracted from `chat.response` events containing `report_version`

All real-time updates arrive via a **single SSE connection per conversation** that stays open across turns.

---

## 2. Boot Sequence

On application start:

```
1. GET /v1/data_sources          → store as available sources for UI toggles
2. GET /v1/sessions/conversations?limit=100  → populate sidebar
3. If a conversation was previously selected (from localStorage/URL):
   a. GET /v1/sessions/conversations/{id}   → load messages + report versions
   b. GET /v1/chat/{id}/events?last_event_id=0  → open SSE connection
```

The data sources list rarely changes (it's driven by backend config), so it can be cached for the session.

---

## 3. Conversation Lifecycle

### 3.1 Create

```
POST /v1/sessions/conversations  {title: "New Session"}
→ {id: "s_abc123", ...}
```

Then immediately open the SSE stream for the new conversation:
```
GET /v1/chat/s_abc123/events?last_event_id=0
```

### 3.2 Switch

When the user clicks a different conversation in the sidebar:

1. You can keep the old SSE connection open or close it (the backend handles both).
2. `GET /v1/sessions/conversations/{id}` to load full state (messages + report versions).
3. Open a new SSE connection: `GET /v1/chat/{id}/events?last_event_id=0`.
4. Restore `reportContent` from the latest report version in the loaded conversation.

### 3.3 Resume After Page Reload

Same as switch. The conversation and all messages are persisted server-side. The SSE stream will replay any events that occurred while the page was closed (though old events may have been cleaned up if the `expiry_seconds` window has passed).

### 3.4 Delete

```
DELETE /v1/sessions/conversations/{id}
```

If the deleted conversation was active, clear the chat and report panels.

---

## 4. SSE Connection Management

### 4.1 Opening

Use the browser's `EventSource` API or a library that supports it:

```javascript
const es = new EventSource(`/v1/chat/${conversationId}/events?last_event_id=0`);

es.addEventListener('chat.start', (e) => { ... });
es.addEventListener('chat.response', (e) => { ... });
// etc.
```

If authentication is required, `EventSource` doesn't support custom headers. Use `fetch()` with `ReadableStream` or a library like `@microsoft/fetch-event-source` instead.

### 4.2 Reconnection

The SSE stream assigns an incrementing `id` to every event. Track it:

```javascript
let lastEventId = 0;

es.onmessage = (e) => {
  lastEventId = parseInt(e.lastEventId, 10);
};
```

On disconnect, reconnect with the cursor:
```
GET /v1/chat/{id}/events?last_event_id={lastEventId}
```

The backend replays all missed events, then sends `stream.mode {"mode": "live"}` to signal the transition from replay to real-time.

### 4.3 Stream Lifetime

The SSE stream **does not close** when a chat turn completes. It stays open so you receive events from:
- Subsequent `POST /v1/chat` turns on the same conversation
- Deep research progress (bridged from Dask workers)
- Heartbeats (every ~15 seconds)

Only close it when switching conversations or when the user leaves.

### 4.4 Event Routing

Every SSE frame has an `event` field (the event type) and a `data` field (JSON payload). Route by event type:

```javascript
// Stream lifecycle
es.addEventListener('stream.start', handleStreamStart);
es.addEventListener('stream.mode', handleStreamMode);
es.addEventListener('heartbeat', handleHeartbeat);

// Chat turn
es.addEventListener('chat.start', handleChatStart);
es.addEventListener('chat.response', handleChatResponse);
es.addEventListener('chat.complete', handleChatComplete);
es.addEventListener('chat.error', handleChatError);
es.addEventListener('chat.cancelled', handleChatCancelled);

// HITL
es.addEventListener('interaction.required', handleInteractionRequired);
es.addEventListener('interaction.resolved', handleInteractionResolved);
es.addEventListener('interaction.timeout', handleInteractionTimeout);

// Deep research
es.addEventListener('deep_research.started', handleDeepResearchStarted);
es.addEventListener('deep_research.complete', handleDeepResearchComplete);

// Agent pipeline (inline agent — NAT step events)
es.addEventListener('step.WORKFLOW_START', handleStep);
es.addEventListener('step.WORKFLOW_END', handleStep);
es.addEventListener('step.FUNCTION_START', handleStep);
es.addEventListener('step.FUNCTION_END', handleStep);
es.addEventListener('step.LLM_START', handleStep);
es.addEventListener('step.LLM_END', handleStep);
es.addEventListener('step.TOOL_START', handleStep);
es.addEventListener('step.TOOL_END', handleStep);

// Deep research events (from Dask worker via event bridge)
es.addEventListener('workflow.start', handleResearchEvent);
es.addEventListener('workflow.end', handleResearchEvent);
es.addEventListener('llm.start', handleResearchEvent);
es.addEventListener('llm.end', handleResearchEvent);
es.addEventListener('tool.start', handleResearchEvent);
es.addEventListener('tool.end', handleResearchEvent);
es.addEventListener('artifact.update', handleArtifactUpdate);
es.addEventListener('job.heartbeat', handleJobHeartbeat);
```

**Two namespaces for the same concepts:** The inline agent emits `step.TOOL_START` (NAT framework). The Dask worker emits `tool.start` (AgentEventCallback). Both may appear during a follow-up deep research turn. The `step.*` events carry a `payload` JSON string; the unprefixed events have structured top-level fields.

---

## 5. UI State Machine

The conversation UI should track these states:

```
                          ┌──────────────────────────────────────────┐
                          │                                          │
  ┌──────┐  POST /chat  ┌▼─────────┐  chat.complete  ┌──────┐      │
  │ IDLE ├──────────────►│PROCESSING├────────────────►│ IDLE │      │
  └──────┘               └┬────┬────┘                 └──────┘      │
                          │    │                                     │
           interaction    │    │  deep_research                      │
           .required      │    │  .started                           │
                          │    │                                     │
                    ┌─────▼┐  ┌▼──────────────┐                     │
                    │ HITL │  │DEEP_RESEARCHING│                     │
                    └──┬───┘  └───────┬────────┘                     │
                       │              │                              │
              POST     │    deep_research                            │
              /respond │    .complete                                │
                       │              │                              │
           interaction │     ┌────────▼────────┐  chat.complete      │
           .resolved   │     │  INTEGRATING    ├─────────────────────┘
                       │     │  (followup only)│
                       │     └─────────────────┘
                       │
                       └──► back to PROCESSING
```

### State Definitions

| State | User Can | Indicator |
|-------|----------|-----------|
| `IDLE` | Type and send messages | Input enabled |
| `PROCESSING` | Cancel only | Spinner / "thinking" animation |
| `HITL` | Respond to prompt | Display prompt text, show input for response |
| `DEEP_RESEARCHING` | Watch progress | Progress panel (tools, sources, todos) |
| `INTEGRATING` | Watch | "Integrating research into report..." |

### Transitions

| From | Event | To | Notes |
|------|-------|----|-------|
| IDLE | `POST /v1/chat` returns 202 | PROCESSING | Disable input |
| PROCESSING | `interaction.required` | HITL | Show the prompt |
| HITL | `interaction.resolved` | PROCESSING | Hide prompt, show spinner |
| HITL | `interaction.timeout` | IDLE | Show timeout message |
| PROCESSING | `deep_research.started` without `is_followup` | DEEP_RESEARCHING | Show progress panel |
| PROCESSING | `deep_research.started` with `is_followup: true` | DEEP_RESEARCHING | Show progress panel |
| DEEP_RESEARCHING | `deep_research.complete` (initial, no `is_followup`) | IDLE | `chat.complete` already arrived earlier |
| DEEP_RESEARCHING | `deep_research.complete` (followup) | INTEGRATING | Backend is now running edit_report |
| INTEGRATING | `chat.complete` | IDLE | Parse report_version from chat.response |
| PROCESSING | `chat.complete` (no deep research) | IDLE | Parse response |
| PROCESSING | `chat.error` | IDLE | Show error |
| ANY | `chat.cancelled` | IDLE | User cancelled |

### Critical Timing Detail

For **initial** deep research, the event order is:
```
deep_research.started → chat.response → chat.complete → [research events] → deep_research.complete
```
`chat.complete` arrives early because the inline agent's turn ends after submitting the job. Don't transition to IDLE on `chat.complete` if you've seen `deep_research.started` without a matching `deep_research.complete`.

For **follow-up** deep research, the order is:
```
deep_research.started → [research events] → deep_research.complete → [integration tool calls] → chat.response → chat.complete
```
Everything happens within one turn. `chat.complete` is the real "done" signal.

---

## 6. Sending Messages

### 6.1 Basic Send

```javascript
const res = await fetch('/v1/chat', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    conversation_id: currentConversationId,
    message: userText,
    data_sources: enabledSourceIds,  // from source toggles, or null for all
  }),
});

if (res.status === 202) {
  // Success — events will arrive on the SSE stream
  const {conversation_id, message_id} = await res.json();
} else if (res.status === 409) {
  // Another task is already running — show "please wait"
} else if (res.status === 404) {
  // Conversation not found or not owned by caller
}
```

### 6.2 Follow-up Messages

Follow-up messages use the same endpoint as initial messages. The backend reads the current report from the `ReportVersionStore` automatically — you don't need to send report content:

```javascript
const res = await fetch('/v1/chat', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    conversation_id: currentConversationId,
    message: userText,
  }),
});
```

### 6.3 HITL Response

When `interaction.required` arrives:

```javascript
function handleInteractionRequired(event) {
  const {prompt_id, text, input_type, timeout, options} = JSON.parse(event.data).data;

  // Display the prompt text to the user
  showPromptUI(text, options);

  // When user responds:
  onUserSubmit(async (responseText) => {
    await fetch(`/v1/chat/${conversationId}/respond`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({response: responseText, prompt_id}),
    });
  });
}
```

The `text` field is markdown-formatted. Common patterns:
- **Clarification:** Numbered options (e.g., "Which angle? 1. Option A  2. Option B ... or type 'skip'")
- **Plan approval:** Research plan preview with "Reply **approve** to proceed, **reject** to cancel, or provide feedback"

### 6.4 Cancel

```javascript
await fetch(`/v1/chat/${conversationId}/cancel`, {method: 'POST'});
```

---

## 7. Parsing chat.response

The `chat.response` event has a structured `data` object with `content` (always a string) and an optional `report_version` (object, present only when the report was edited):

```javascript
function handleChatResponse(event) {
  const data = JSON.parse(event.data).data;

  // content is always the agent's text message
  appendAssistantMessage(data.content);

  // report_version is present only when the report was modified
  if (data.report_version) {
    updateReport(data.report_version);
  }
}
```

No `JSON.parse` gymnastics needed — `content` is always plain text, `report_version` is always a structured object (or absent).

### 7.1 Report Version Object

When present in `chat.response`:

```javascript
{
  versionId: "14b39e28070e",         // unique ID for this version
  parentVersionId: "883757cf3461",   // previous version (for diff computation)
  content: "# Full report text...",  // complete report after the edit
  triggeringQuery: "edit: ...",      // description of the change
  parentContent: "# Previous..."    // full previous version content (for diff display)
}
```

**`parentContent`** is included so you can compute and display a diff without a separate API call.

### 7.2 Report Version Chain

Versions form a linked list via `parentVersionId`:

```
v1 (initial deep research) → v2 (title edit) → v3 (follow-up integration)
```

Store all versions so the user can browse version history. On conversation load, versions come from `GET /v1/sessions/conversations/{id}` in the `report_versions` array.

---

## 8. Deep Research Progress UI

During deep research (`deep_research.started` → `deep_research.complete`), several event types provide progress information suitable for a progress panel.

### 8.1 Todo Progress (Primary Progress Indicator)

`artifact.update` events with `data.type === "todo"` contain a checklist:

```javascript
function handleArtifactUpdate(event) {
  const {data} = JSON.parse(event.data);
  if (data.type === 'todo') {
    // data.content is an array of {content: string, status: string}
    // status: "pending" | "in_progress" | "completed"
    updateProgressChecklist(data.content);
  }
}
```

Typical steps:
```
✓ Task decomposition
✓ Save request
✓ Planning
● Research         ← in_progress
○ Verify           ← pending
○ Synthesize
○ Write report
○ Final verification
```

### 8.2 Tool Activity

`tool.start` and `tool.end` events show what the researcher is doing:

```javascript
function handleToolStart(event) {
  const {name, data} = JSON.parse(event.data);
  // name: "tavily_search", "paper_search_tool", "write_file", etc.
  // data.input: tool input (may be trimmed for large inputs)
  addActiveToolIndicator(name, data?.input);
}

function handleToolEnd(event) {
  const {name} = JSON.parse(event.data);
  removeActiveToolIndicator(name);
}
```

### 8.3 Source Tracking

`artifact.update` events with `data.type === "citation_source"` indicate a URL was discovered:

```javascript
if (data.type === 'citation_source') {
  addSource(data.url);  // e.g., "https://pubs.rsc.org/en/content/..."
}
if (data.type === 'citation_use') {
  markSourceAsCited(data.url);  // source was actually used in the report
}
```

Display: "Sources: 136 found, 14 cited"

### 8.4 Sub-Agent Activity

`workflow.start` and `workflow.end` events bracket sub-agent execution:

```javascript
if (eventType === 'workflow.start') {
  // data.name: "researcher-agent", etc.
  showSubAgentActive(data.name);
}
```

### 8.5 Report Content

`artifact.update` events with `data.type === "file"` and `data.name === "/report.md"` contain the report being written:

```javascript
if (data.type === 'file' && data.name?.endsWith('/report.md')) {
  // data.content has the report being written
  // Note: this is the in-progress report from the Dask worker,
  // NOT the final verified version
}
```

The final verified report arrives as `artifact.update` with `data.type === "output"` and `data.output_category === "final_report"`.

### 8.6 LLM Activity

`llm.start` / `llm.end` events indicate LLM calls. Useful for a "thinking" indicator. The `llm.end` metadata may include:

```javascript
// metadata.thinking — model's chain-of-thought (trimmed to 300 chars)
// metadata.usage — token counts {input_tokens, output_tokens, total_tokens}
```

---

## 9. Report Panel

### 9.1 When to Show

Show the report panel when:
- The conversation has report versions (check `report_versions.length > 0` on load)
- A `chat.response` with `report_version` arrives
- An `artifact.update` with `output_category === "final_report"` arrives during deep research

### 9.2 Content Source Priority

Multiple events deliver report-like content. Use this priority:

1. **`report_version` from `chat.response`** — highest priority, this is the backend's authoritative version after edits
2. **`artifact.update` with `output_category: "final_report"`** — the verified report from deep research
3. **`artifact.update` with `type: "file"`, name `/report.md`** — the in-progress report being written (show as preview)

### 9.3 Version Selector

If you implement a version history dropdown:

```javascript
// On conversation load:
const conv = await fetch(`/v1/sessions/conversations/${id}`).then(r => r.json());
const versions = conv.report_versions;  // ordered by created_at asc

// Display the latest version by default
setReportContent(versions[versions.length - 1]?.content);

// When user selects a different version:
setReportContent(versions[selectedIndex].content);
```

### 9.4 Diff Display

When a `report_version` includes `parentContent`, you can compute a diff:

```javascript
if (reportVersion.parentContent) {
  const diff = computeDiff(reportVersion.parentContent, reportVersion.content);
  showDiffView(diff);
}
```

### 9.5 Persisting Report Versions

The backend persists report versions created by the agent automatically. If the frontend creates versions (e.g., from initial deep research completion), persist them:

```javascript
await fetch(`/v1/sessions/conversations/${conversationId}/report-versions`, {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    version_id: reportVersion.versionId,
    parent_version_id: reportVersion.parentVersionId,
    content: reportVersion.content,
    triggering_query: reportVersion.triggeringQuery,
  }),
});
```

The endpoint is idempotent — duplicate `version_id` inserts are silently skipped.

---

## 10. Data Source Toggles

### 10.1 Fetching

```javascript
const sources = await fetch('/v1/data_sources').then(r => r.json());
// [{id: "web_search", name: "Web Search", description: "...", requires_auth: false}, ...]
```

### 10.2 Per-Conversation Persistence

Enabled sources are stored on the conversation:

```javascript
// On create:
await fetch('/v1/sessions/conversations', {
  method: 'POST',
  body: JSON.stringify({
    title: 'New Session',
    enabled_data_source_ids: ['web_search', 'paper_search'],
  }),
});

// On toggle change:
await fetch(`/v1/sessions/conversations/${id}`, {
  method: 'PUT',
  body: JSON.stringify({
    enabled_data_source_ids: newEnabledIds,
  }),
});
```

### 10.3 Sending with Messages

Pass the enabled sources on each message so the agent filters its tools:

```javascript
body: JSON.stringify({
  conversation_id: id,
  message: text,
  data_sources: enabledSourceIds,  // null means "use all"
})
```

---

## 11. Error Handling

### 11.1 HTTP Errors

| Status | Meaning | UI Action |
|--------|---------|-----------|
| 202 | Message accepted | Show processing state |
| 404 | Conversation not found or not owned | Show "session not found", redirect to new session |
| 409 | Already processing | Show "please wait for current request to finish" |
| 503 | Backend not ready | Show "server starting up, please retry" |

### 11.2 SSE Errors

| Event | Meaning | UI Action |
|-------|---------|-----------|
| `chat.error` | Agent crashed | Show error message, return to IDLE |
| `chat.cancelled` | User cancelled | Return to IDLE |
| `interaction.timeout` | HITL prompt expired | Show timeout message, return to IDLE |
| `job.error` | Deep research job failed | Show error in progress panel |
| `job.shutdown` | Server shutting down | Show "server restarting", auto-reconnect |

### 11.3 SSE Connection Loss

The SSE connection can drop due to network issues, server restarts, or proxy timeouts. Implement exponential backoff reconnection:

```javascript
let reconnectDelay = 1000;
const MAX_DELAY = 30000;

function connect() {
  const es = new EventSource(`/v1/chat/${id}/events?last_event_id=${lastEventId}`);

  es.onopen = () => { reconnectDelay = 1000; };  // reset on success

  es.onerror = () => {
    es.close();
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, MAX_DELAY);
  };
}
```

---

## 12. Optimistic UI Patterns

### 12.1 Messages

When the user sends a message, add it to the chat immediately (optimistic):

```javascript
// Add user message to UI immediately
appendMessage({role: 'user', content: userText, id: tempId});

// Then POST
const res = await fetch('/v1/chat', { ... });
const {message_id} = await res.json();

// Update the temp ID with the real one
updateMessageId(tempId, message_id);
```

The backend also persists the message, so on page reload it will be there.

### 12.2 Report Edits

When `chat.response` includes a `report_version`, update the report panel immediately. The version is already persisted by the backend — no additional save needed.

---

## 13. Complete Example Flow

Here's a full annotated flow for a research session with follow-up editing:

```
Boot:
  GET /v1/data_sources → [web_search, paper_search, knowledge_layer]
  GET /v1/sessions/conversations → [{id: "s_old1", ...}]

User clicks "New Chat":
  POST /v1/sessions/conversations {title: "Research"} → {id: "s_abc"}
  GET /v1/chat/s_abc/events?last_event_id=0 → SSE opens
  ← stream.start {conversation_id: "s_abc", mode: "pubsub"}
  ← stream.mode {mode: "live"}

User types "Write a report on AI for X-ray spectroscopy":
  POST /v1/chat {conversation_id: "s_abc", message: "...", data_sources: ["web_search"]}
  → 202 {message_id: "m1"}
  UI: transition to PROCESSING

  ← chat.start
  ← step.WORKFLOW_START {name: "chat_deepresearcher_agent"}
  ← step.LLM_START     (intent classification)
  ← step.LLM_END
  ← interaction.required {prompt_id: "p1", text: "Which angle? 1. Data analysis..."}
  UI: transition to HITL, show prompt

User types "5. Broad overview":
  POST /v1/chat/s_abc/respond {response: "5. Broad overview", prompt_id: "p1"}
  ← interaction.resolved
  UI: transition to PROCESSING

  ← step.LLM_START     (plan generation)
  ← step.LLM_END
  ← interaction.required {prompt_id: "p2", text: "**Research Plan**\n\n...approve/reject"}
  UI: show plan for approval

User types "approve":
  POST /v1/chat/s_abc/respond {response: "approve", prompt_id: "p2"}
  ← interaction.resolved

  ← deep_research.started {job_id: "job1"}
  UI: transition to DEEP_RESEARCHING, show progress panel

  ← chat.response {content: "Deep research job submitted. Job ID: job1"}
  ← chat.complete
  (DON'T transition to IDLE — deep research is still running)

  ← tool.start {name: "paper_search_tool"}     ← from Dask via bridge
  ← tool.end
  ← artifact.update {type: "todo", content: [{content: "Research", status: "in_progress"}, ...]}
  ← artifact.update {type: "citation_source", url: "https://..."}
  ... (50-100+ events over several minutes) ...
  ← artifact.update {type: "output", output_category: "final_report", content: "# Report..."}
  ← deep_research.complete {job_id: "job1", status: "success"}
  UI: transition to IDLE, show report in report panel

  Optional: GET /v1/jobs/async/job/job1/report → full report text
  Optional: GET /v1/jobs/async/job/job1/state  → artifacts/sources summary

User types "Change the title to: A Comprehensive Review":
  POST /v1/chat {conversation_id: "s_abc", message: "..."}
  → 202
  UI: transition to PROCESSING

  ← chat.start
  ← step.TOOL_START {name: "read_report"}
  ← step.TOOL_END
  ← step.TOOL_START {name: "edit_report"}
  ← step.TOOL_END
  ← chat.response {content: "Done — title updated", report_version: {versionId: "...", ...}}
  ← chat.complete
  UI: update report panel from report_version, transition to IDLE

User types "Do more deep research on autonomous spectroscopy, integrate it":
  POST /v1/chat {conversation_id: "s_abc", message: "..."}
  → 202
  UI: transition to PROCESSING

  ← chat.start
  ← step.TOOL_START {name: "request_deep_research"}
  ← deep_research.started {job_id: "job2", is_followup: true, backend_integration: true}
  UI: transition to DEEP_RESEARCHING

  ← tool.start, artifact.update, ... (research events from bridge)
  ← step.TOOL_END {name: "request_deep_research"}
  ← deep_research.complete {job_id: "job2", status: "success"}
  UI: transition to INTEGRATING

  ← step.TOOL_START {name: "read_report"}
  ← step.TOOL_END
  ← step.TOOL_START {name: "edit_report"}
  ← step.TOOL_END
  ← chat.response {content: "Done — expanded section", report_version: {versionId: "...", ...}}
  ← chat.complete
  UI: update report panel from report_version, transition to IDLE
```

---

## 14. Key Gotchas

### 14.1 Don't transition to IDLE on chat.complete during deep research

For initial deep research, `chat.complete` arrives before the research finishes. Track whether `deep_research.started` has a matching `deep_research.complete` before returning to IDLE.

### 14.2 report_context is not needed

The backend reads the current report from the `ReportVersionStore` automatically. You do not need to send report content with follow-up messages. The `report_context` field on the request is deprecated and ignored.

### 14.3 chat.response has a stable schema

`data.content` is always the agent's text message. `data.report_version` is an object when the report was edited, absent otherwise. No speculative JSON parsing needed.

### 14.4 Version IDs are hex strings, not sequential

Report version IDs look like `14b39e28070e` (hex). Don't assume ordering from IDs — use `created_at` or array position.

### 14.5 One active task per conversation

Sending a second `POST /v1/chat` while a turn is processing returns `409`. Disable the send button while processing.

### 14.6 step.* vs unprefixed events

`step.TOOL_START` (inline agent, NAT framework) and `tool.start` (Dask worker, AgentEventCallback) look similar but have different payload structures. The `step.*` events have a `payload` field containing a JSON string. The unprefixed events have structured top-level fields.

### 14.7 EventSource doesn't support auth headers

The browser `EventSource` API doesn't allow custom headers. If your deployment requires authentication for the SSE endpoint, use `fetch()` with a streaming reader or a polyfill library that supports headers.

### 14.8 deep_research.started can appear inside step.TOOL_START/END

For follow-up deep research, the event sequence has `step.TOOL_START {name: "request_deep_research"}` *before* `deep_research.started`. The `step.TOOL_END` for `request_deep_research` arrives *after* `deep_research.complete` — the tool call spans the entire research duration.

### 14.9 SSE event IDs jump between namespaces

Event IDs come from the `job_events` table's auto-increment primary key. When an event bridge pipes events from a Dask job into the conversation stream, the conversation's event IDs may jump by thousands (because the Dask job's events have their own ID sequence, and the re-emitted events get new IDs in the conversation's stream). Don't assume sequential IDs — only use them as cursors for reconnection.
