// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Sessions API Client
 *
 * Typed client for /v1/sessions/* endpoints.
 * Manages conversations, messages, report versions, and collection tracking.
 */

import { authenticatedFetch } from './authenticated-fetch'

const getBaseUrl = (): string => {
  const isBrowser = typeof window !== 'undefined'
  return isBrowser ? '/api/v1/sessions' : `${process.env.BACKEND_URL || 'http://localhost:8000'}/v1/sessions`
}

// ============================================================================
// Types
// ============================================================================

export interface ConversationListItem {
  id: string
  title: string
  enabled_data_source_ids: string[]
  created_at: string
  updated_at: string
  message_count: number
}

export interface ConversationDetail {
  id: string
  user_id: string
  title: string
  enabled_data_source_ids: string[]
  created_at: string
  updated_at: string
  messages: MessageRecord[]
  report_versions: ReportVersionRecord[]
}

export interface MessageRecord {
  id: string
  conversation_id: string
  role: string
  content: string
  message_type: string | null
  metadata: Record<string, unknown>
  created_at: string
}

export interface ReportVersionRecord {
  version_id: string
  conversation_id: string
  parent_version_id: string | null
  content: string
  triggering_query: string
  created_at: string
}

export interface CreateConversationRequest {
  id?: string
  title?: string
  enabled_data_source_ids?: string[]
}

export interface UpdateConversationRequest {
  title?: string
  enabled_data_source_ids?: string[]
}

export interface AppendMessagesRequest {
  messages: Array<{
    id?: string
    role: string
    content: string
    message_type?: string
    [key: string]: unknown
  }>
}

export interface MigrateRequest {
  conversations: Array<Record<string, unknown>>
  current_conversation_id?: string | null
}

// ============================================================================
// Client Functions
// ============================================================================

async function handleResponse<T>(response: Response, context: string): Promise<T> {
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new Error(error?.detail || `${context}: ${response.status} ${response.statusText}`)
  }
  return response.json()
}

// ── Conversations ─────────────────────────────────────────────────────

export async function createConversation(
  request: CreateConversationRequest = {}
): Promise<ConversationListItem> {
  const response = await authenticatedFetch(`${getBaseUrl()}/conversations`, {
    method: 'POST',
    body: JSON.stringify(request),
  })
  return handleResponse(response, 'Create conversation')
}

export async function listConversations(
  limit = 100,
  offset = 0
): Promise<{ conversations: ConversationListItem[]; total: number }> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  const response = await authenticatedFetch(`${getBaseUrl()}/conversations?${params}`)
  return handleResponse(response, 'List conversations')
}

export async function getConversation(conversationId: string): Promise<ConversationDetail> {
  const response = await authenticatedFetch(`${getBaseUrl()}/conversations/${conversationId}`)
  return handleResponse(response, 'Get conversation')
}

export async function updateConversation(
  conversationId: string,
  request: UpdateConversationRequest
): Promise<void> {
  const response = await authenticatedFetch(`${getBaseUrl()}/conversations/${conversationId}`, {
    method: 'PUT',
    body: JSON.stringify(request),
  })
  await handleResponse(response, 'Update conversation')
}

export async function deleteConversation(conversationId: string): Promise<void> {
  const response = await authenticatedFetch(`${getBaseUrl()}/conversations/${conversationId}`, {
    method: 'DELETE',
  })
  await handleResponse(response, 'Delete conversation')
}

export async function bulkDeleteConversations(conversationIds: string[]): Promise<{ deleted: number }> {
  const response = await authenticatedFetch(`${getBaseUrl()}/conversations/bulk-delete`, {
    method: 'POST',
    body: JSON.stringify({ conversation_ids: conversationIds }),
  })
  return handleResponse(response, 'Bulk delete conversations')
}

// ── Messages ──────────────────────────────────────────────────────────

export async function listMessages(
  conversationId: string,
  limit = 1000,
  offset = 0
): Promise<{ messages: MessageRecord[] }> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  const response = await authenticatedFetch(
    `${getBaseUrl()}/conversations/${conversationId}/messages?${params}`
  )
  return handleResponse(response, 'List messages')
}

export async function appendMessages(
  conversationId: string,
  request: AppendMessagesRequest
): Promise<{ message_ids: string[] }> {
  const response = await authenticatedFetch(
    `${getBaseUrl()}/conversations/${conversationId}/messages`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    }
  )
  return handleResponse(response, 'Append messages')
}

export async function updateMessage(
  conversationId: string,
  messageId: string,
  updates: Record<string, unknown>
): Promise<void> {
  const response = await authenticatedFetch(
    `${getBaseUrl()}/conversations/${conversationId}/messages/${messageId}`,
    {
      method: 'PUT',
      body: JSON.stringify(updates),
    }
  )
  await handleResponse(response, 'Update message')
}

// ── Report Versions ──────────────────────────────────────────────────

export async function listReportVersions(
  conversationId: string
): Promise<{ report_versions: ReportVersionRecord[] }> {
  const response = await authenticatedFetch(
    `${getBaseUrl()}/conversations/${conversationId}/report-versions`
  )
  return handleResponse(response, 'List report versions')
}

export async function appendReportVersion(
  conversationId: string,
  version: {
    version_id?: string
    parent_version_id?: string | null
    content: string
    triggering_query?: string
  }
): Promise<{ version_id: string }> {
  const response = await authenticatedFetch(
    `${getBaseUrl()}/conversations/${conversationId}/report-versions`,
    {
      method: 'POST',
      body: JSON.stringify(version),
    }
  )
  return handleResponse(response, 'Append report version')
}

// ── Collection Tracking ──────────────────────────────────────────────

export async function listKnownCollections(): Promise<{ session_ids: string[] }> {
  const response = await authenticatedFetch(`${getBaseUrl()}/collections/known`)
  return handleResponse(response, 'List known collections')
}

export async function markCollection(sessionId: string): Promise<void> {
  const response = await authenticatedFetch(`${getBaseUrl()}/collections/known/${sessionId}`, {
    method: 'POST',
  })
  await handleResponse(response, 'Mark collection')
}

export async function unmarkCollection(sessionId: string): Promise<void> {
  const response = await authenticatedFetch(`${getBaseUrl()}/collections/known/${sessionId}`, {
    method: 'DELETE',
  })
  await handleResponse(response, 'Unmark collection')
}

// ── Migration ────────────────────────────────────────────────────────

export async function migrateFromLocalStorage(
  request: MigrateRequest
): Promise<{ imported: number }> {
  const response = await authenticatedFetch(`${getBaseUrl()}/migrate`, {
    method: 'POST',
    body: JSON.stringify(request),
  })
  return handleResponse(response, 'Migrate from localStorage')
}
