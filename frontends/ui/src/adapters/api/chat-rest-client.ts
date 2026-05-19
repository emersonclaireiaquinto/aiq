// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Chat REST Client
 *
 * REST calls for the unified chat API: sending messages, responding to
 * HITL prompts, and cancelling processing.
 */

import { authenticatedFetch } from './authenticated-fetch'

const getBaseUrl = (): string => {
  const isBrowser = typeof window !== 'undefined'
  return isBrowser ? '/api/v1/chat' : `${process.env.BACKEND_URL || 'http://localhost:8000'}/v1/chat`
}

// ── Types ────────────────────────────────────────────────────────────

export interface SendMessageRequest {
  conversation_id?: string
  message: string
  data_sources?: string[]
  report_context?: string
}

export interface SendMessageResponse {
  conversation_id: string
  message_id: string
  status: string
}

export interface RespondRequest {
  response: string
  prompt_id?: string
}

// ── API Functions ────────────────────────────────────────────────────

export async function sendMessage(request: SendMessageRequest): Promise<SendMessageResponse> {
  const response = await authenticatedFetch(getBaseUrl(), {
    method: 'POST',
    body: JSON.stringify(request),
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new Error(error?.detail || `Send message failed: ${response.status}`)
  }
  return response.json()
}

export async function respondToInteraction(
  conversationId: string,
  request: RespondRequest
): Promise<void> {
  const response = await authenticatedFetch(`${getBaseUrl()}/${conversationId}/respond`, {
    method: 'POST',
    body: JSON.stringify(request),
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new Error(error?.detail || `Respond failed: ${response.status}`)
  }
}

export async function cancelProcessing(conversationId: string): Promise<void> {
  const response = await authenticatedFetch(`${getBaseUrl()}/${conversationId}/cancel`, {
    method: 'POST',
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new Error(error?.detail || `Cancel failed: ${response.status}`)
  }
}

/**
 * Build the SSE events URL for a conversation.
 * Used by the SSE client to connect to the event stream.
 */
export function buildEventsUrl(conversationId: string, lastEventId?: number, authToken?: string): string {
  const isBrowser = typeof window !== 'undefined'
  const baseUrl = isBrowser ? '' : (process.env.BACKEND_URL || 'http://localhost:8000')

  let url = `${baseUrl}/api/v1/chat/${conversationId}/events`

  const params = new URLSearchParams()
  if (lastEventId && lastEventId > 0) {
    params.set('last_event_id', String(lastEventId))
  }
  if (authToken) {
    params.set('token', authToken)
  }
  const qs = params.toString()
  if (qs) url += `?${qs}`

  return url
}
