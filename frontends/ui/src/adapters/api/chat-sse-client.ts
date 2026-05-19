// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Unified Chat SSE Client
 *
 * Handles all server-to-client streaming for the chat API via SSE.
 * Replaces both the WebSocket client (sync chat) and the deep research
 * SSE client with a single conversation-scoped stream.
 *
 * The stream stays open across turns — it does NOT close on chat.complete.
 */

import { buildEventsUrl } from './chat-rest-client'
import type { TodoItem } from './deep-research-client'

// Re-export for convenience
export type { TodoItem }

/** Normalize tool input to a Record, handling string/object/undefined */
const normalizeToolInput = (input: unknown): Record<string, unknown> | undefined => {
  if (input == null) return undefined
  if (typeof input === 'object' && !Array.isArray(input)) return input as Record<string, unknown>
  return { _raw: String(input) }
}

// ── Event types ──────────────────────────────────────────────────────

/** All SSE event types the conversation stream can emit */
export type ChatSSEEventType =
  // Chat lifecycle
  | 'stream.start'
  | 'stream.mode'
  | 'heartbeat'
  | 'chat.start'
  | 'chat.response'
  | 'chat.complete'
  | 'chat.error'
  | 'chat.cancelled'
  // HITL
  | 'interaction.required'
  | 'interaction.resolved'
  | 'interaction.timeout'
  // Deep research
  | 'deep_research.started'
  | 'deep_research.complete'
  // Intermediate steps (from inline runner)
  | 'step.WORKFLOW_START'
  | 'step.WORKFLOW_END'
  | 'step.FUNCTION_START'
  | 'step.FUNCTION_END'
  | 'step.LLM_START'
  | 'step.LLM_END'
  | 'step.LLM_CHUNK'
  // Deep research events (bridged from job event store)
  | 'job.status'
  | 'job.heartbeat'
  | 'workflow.start'
  | 'workflow.end'
  | 'llm.start'
  | 'llm.chunk'
  | 'llm.end'
  | 'tool.start'
  | 'tool.end'
  | 'artifact.update'

// ── Callbacks ────────────────────────────────────────────────────────

export interface ChatSSECallbacks {
  // Chat lifecycle
  onChatResponse?: (content: string) => void
  onChatComplete?: () => void
  onChatError?: (error: string, errorType?: string) => void
  onChatCancelled?: () => void

  // HITL
  onInteractionRequired?: (promptId: string, text: string, inputType: string, options?: unknown[]) => void
  onInteractionResolved?: (promptId: string) => void
  onInteractionTimeout?: (promptId: string) => void

  // Deep research
  onDeepResearchStarted?: (jobId: string) => void
  onDeepResearchComplete?: (jobId: string, status: string) => void

  // Intermediate steps (from inline execution)
  onStep?: (stepType: string, id: string, name: string, payload: string, parentId?: string) => void

  // Deep research granular events (bridged)
  onWorkflowStart?: (name: string, input?: string, eventId?: string, agentId?: string) => void
  onWorkflowEnd?: (name: string, output?: string, eventId?: string, agentId?: string) => void
  onLLMStart?: (name: string, workflow?: string) => void
  onLLMChunk?: (chunk: string) => void
  onLLMEnd?: (output: string, thinking?: string, usage?: { input_tokens: number; output_tokens: number }) => void
  onToolStart?: (name: string, input?: Record<string, unknown>, workflow?: string, eventId?: string, agentId?: string) => void
  onToolEnd?: (name: string, output?: string, eventId?: string, agentId?: string) => void
  onTodoUpdate?: (todos: TodoItem[], workflow?: string) => void
  onCitationUpdate?: (url: string, content: string, isCited?: boolean) => void
  onFileUpdate?: (filename: string, content: string) => void
  onOutputUpdate?: (content: string, outputCategory?: string, workflow?: string) => void
  onJobStatus?: (status: string, error?: string) => void

  // Connection
  onConnected?: () => void
  onDisconnect?: () => void
  onError?: (error: Error) => void
  onHeartbeat?: () => void
}

// ── Client ───────────────────────────────────────────────────────────

export interface ChatSSEClient {
  connect: () => void
  disconnect: () => void
  isConnected: () => boolean
  getLastEventId: () => string | null
}

export interface ChatSSEClientOptions {
  conversationId: string
  callbacks: ChatSSECallbacks
  lastEventId?: number
  authToken?: string
}

const MAX_RECONNECT_ATTEMPTS = 5

export const createChatSSEClient = (options: ChatSSEClientOptions): ChatSSEClient => {
  const { conversationId, callbacks, lastEventId, authToken } = options

  let eventSource: EventSource | null = null
  let lastReceivedEventId: string | null = lastEventId ? String(lastEventId) : null
  let intentionalClose = false
  let reconnectAttempts = 0

  const buildUrl = (): string => {
    const numericId = lastReceivedEventId ? parseInt(lastReceivedEventId, 10) : undefined
    return buildEventsUrl(conversationId, numericId, authToken)
  }

  const parse = (data: string): unknown => {
    try {
      return JSON.parse(data)
    } catch {
      return data
    }
  }

  const handleMessage = (event: MessageEvent, eventType: string) => {
    if (event.lastEventId) {
      lastReceivedEventId = event.lastEventId
    }

    const raw = parse(event.data) as Record<string, unknown>

    switch (eventType) {
      // ── Chat lifecycle ──
      case 'chat.response': {
        const data = (raw.data || raw) as { content?: string }
        callbacks.onChatResponse?.(data.content || '')
        break
      }
      case 'chat.complete':
        callbacks.onChatComplete?.()
        break
      case 'chat.error': {
        const data = (raw.data || raw) as { error?: string; error_type?: string }
        callbacks.onChatError?.(data.error || 'Unknown error', data.error_type)
        break
      }
      case 'chat.cancelled':
        callbacks.onChatCancelled?.()
        break

      // ── HITL ──
      case 'interaction.required': {
        const data = (raw.data || raw) as { prompt_id: string; text: string; input_type: string; options?: unknown[] }
        callbacks.onInteractionRequired?.(data.prompt_id, data.text, data.input_type, data.options)
        break
      }
      case 'interaction.resolved': {
        const data = (raw.data || raw) as { prompt_id: string }
        callbacks.onInteractionResolved?.(data.prompt_id)
        break
      }
      case 'interaction.timeout': {
        const data = (raw.data || raw) as { prompt_id: string }
        callbacks.onInteractionTimeout?.(data.prompt_id)
        break
      }

      // ── Deep research ──
      case 'deep_research.started': {
        const data = (raw.data || raw) as { job_id: string }
        callbacks.onDeepResearchStarted?.(data.job_id)
        break
      }
      case 'deep_research.complete': {
        const data = (raw.data || raw) as { job_id: string; status: string }
        callbacks.onDeepResearchComplete?.(data.job_id, data.status)
        break
      }

      // ── Inline intermediate steps ──
      case 'step.WORKFLOW_START':
      case 'step.WORKFLOW_END':
      case 'step.FUNCTION_START':
      case 'step.FUNCTION_END':
      case 'step.LLM_START':
      case 'step.LLM_END':
      case 'step.LLM_CHUNK': {
        const data = (raw.data || raw) as { id: string; name: string; payload: string; parent_id?: string }
        callbacks.onStep?.(eventType, data.id, data.name, data.payload, data.parent_id)
        break
      }

      // ── Deep research bridged events ──
      case 'workflow.start': {
        const d = raw as { id?: string; name: string; data?: { input?: string }; metadata?: { agent_id?: string } }
        callbacks.onWorkflowStart?.(d.name, d.data?.input, d.id, d.metadata?.agent_id)
        break
      }
      case 'workflow.end': {
        const d = raw as { id?: string; name: string; data?: { output?: string }; metadata?: { agent_id?: string } }
        callbacks.onWorkflowEnd?.(d.name, d.data?.output, d.id, d.metadata?.agent_id)
        break
      }
      case 'llm.start': {
        const d = raw as { name: string; metadata?: { workflow?: string } }
        callbacks.onLLMStart?.(d.name, d.metadata?.workflow)
        break
      }
      case 'llm.chunk': {
        const d = raw as { chunk?: string; data?: { chunk?: string } }
        callbacks.onLLMChunk?.(d.chunk || d.data?.chunk || '')
        break
      }
      case 'llm.end': {
        const d = raw as { data?: { output?: string }; metadata?: { thinking?: string; usage?: Record<string, number> } }
        const usage = d.metadata?.usage
          ? { input_tokens: d.metadata.usage.input_tokens ?? d.metadata.usage.prompt_tokens ?? 0,
              output_tokens: d.metadata.usage.output_tokens ?? d.metadata.usage.completion_tokens ?? 0 }
          : undefined
        callbacks.onLLMEnd?.(d.data?.output || '', d.metadata?.thinking, usage)
        break
      }
      case 'tool.start': {
        const d = raw as { id?: string; name: string; data?: { input?: unknown }; metadata?: { workflow?: string; agent_id?: string } }
        callbacks.onToolStart?.(d.name, normalizeToolInput(d.data?.input), d.metadata?.workflow, d.id, d.metadata?.agent_id)
        break
      }
      case 'tool.end': {
        const d = raw as { id?: string; name: string; data?: { output?: string }; metadata?: { agent_id?: string } }
        callbacks.onToolEnd?.(d.name, d.data?.output, d.id, d.metadata?.agent_id)
        break
      }
      case 'artifact.update': {
        const wrapper = raw as { data?: { type: string; content: unknown; url?: string; output_category?: string }; metadata?: { workflow?: string } }
        const ad = wrapper.data
        if (!ad) break
        const w = wrapper.metadata?.workflow
        switch (ad.type) {
          case 'todo': callbacks.onTodoUpdate?.(ad.content as TodoItem[], w); break
          case 'citation_source': callbacks.onCitationUpdate?.(ad.url || '', ad.content as string, false); break
          case 'citation_use': callbacks.onCitationUpdate?.(ad.url || '', ad.content as string, true); break
          case 'file': {
            const r = ad as Record<string, unknown>
            const path = (r.file_path || r.path || ad.url || 'unknown') as string
            callbacks.onFileUpdate?.(path.split('/').pop() || path, ad.content as string)
            break
          }
          case 'output': callbacks.onOutputUpdate?.(ad.content as string, ad.output_category, w); break
        }
        break
      }
      case 'job.status': {
        const d = (raw as { data?: { status: string; error?: string } }).data || raw as { status: string; error?: string }
        callbacks.onJobStatus?.(d.status, d.error)
        break
      }

      // ── Keepalive ──
      case 'heartbeat':
      case 'job.heartbeat':
        callbacks.onHeartbeat?.()
        break
      case 'stream.start':
        callbacks.onConnected?.()
        break
      case 'stream.mode':
      case 'chat.start':
        break

      default:
        if (process.env.NODE_ENV === 'development') {
          console.warn(`[chat-sse] Unknown event: ${eventType}`, raw)
        }
    }
  }

  // ── All event types we register listeners for ──
  const ALL_EVENT_TYPES: ChatSSEEventType[] = [
    'stream.start', 'stream.mode', 'heartbeat',
    'chat.start', 'chat.response', 'chat.complete', 'chat.error', 'chat.cancelled',
    'interaction.required', 'interaction.resolved', 'interaction.timeout',
    'deep_research.started', 'deep_research.complete',
    'step.WORKFLOW_START', 'step.WORKFLOW_END', 'step.FUNCTION_START', 'step.FUNCTION_END',
    'step.LLM_START', 'step.LLM_END', 'step.LLM_CHUNK',
    'job.status', 'job.heartbeat',
    'workflow.start', 'workflow.end',
    'llm.start', 'llm.chunk', 'llm.end',
    'tool.start', 'tool.end',
    'artifact.update',
  ]

  const connect = () => {
    if (eventSource) return

    intentionalClose = false
    const url = buildUrl()
    eventSource = new EventSource(url)

    eventSource.onopen = () => {
      reconnectAttempts = 0
    }

    eventSource.onmessage = (event) => {
      handleMessage(event, 'message')
    }

    ALL_EVENT_TYPES.forEach((et) => {
      eventSource?.addEventListener(et, (event) => {
        handleMessage(event as MessageEvent, et)
      })
    })

    eventSource.onerror = () => {
      if (intentionalClose) {
        eventSource?.close()
        eventSource = null
        return
      }
      if (eventSource?.readyState === EventSource.CLOSED) {
        callbacks.onDisconnect?.()
        eventSource = null
      } else if (eventSource?.readyState === EventSource.CONNECTING) {
        reconnectAttempts++
        if (reconnectAttempts > MAX_RECONNECT_ATTEMPTS) {
          eventSource?.close()
          eventSource = null
          callbacks.onError?.(new Error(`SSE failed after ${reconnectAttempts} reconnect attempts`))
        }
      }
    }
  }

  const disconnect = () => {
    if (eventSource) {
      intentionalClose = true
      eventSource.close()
      eventSource = null
    }
  }

  const isConnected = (): boolean => {
    return eventSource !== null && eventSource.readyState === EventSource.OPEN
  }

  const getLastEventId = (): string | null => {
    return lastReceivedEventId
  }

  return { connect, disconnect, isConnected, getLastEventId }
}
