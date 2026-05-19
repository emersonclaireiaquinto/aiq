// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * useSSEChat Hook
 *
 * Unified chat hook using REST + SSE. Replaces useWebSocketChat.
 * User messages are sent via REST POST; all responses stream via SSE.
 * HITL works via SSE events + REST POST.
 *
 * Same public interface as useWebSocketChat for drop-in replacement.
 */

'use client'

import { useCallback, useRef, useEffect, useState } from 'react'
import { createChatSSEClient, type ChatSSEClient } from '@/adapters/api/chat-sse-client'
import * as chatApi from '@/adapters/api/chat-rest-client'
import { useChatStore } from '../store'
import { useAuth } from '@/adapters/auth'
import { useLayoutStore } from '@/features/layout/store'
import type {
  Conversation,
  StatusType,
  ThinkingStep,
  PendingInteraction,
} from '../types'

interface UseSSEChatOptions {
  autoConnect?: boolean
}

interface UseSSEChatReturn {
  sendMessage: (content: string) => Promise<void>
  respondToInteraction: (response: string) => void
  disconnect: () => void
  connect: () => void
  isConnected: boolean
  isStreaming: boolean
  isLoading: boolean
  messages: Conversation['messages'] | undefined
  conversation: Conversation | null
  createConversation: () => void
  userConversations: Conversation[]
  selectConversation: (conversationId: string) => void
  thinkingSteps: ThinkingStep[]
  reportContent: string
  currentStatus: StatusType | null
  pendingInteraction: PendingInteraction | null
}

export const useSSEChat = (options: UseSSEChatOptions = {}): UseSSEChatReturn => {
  const { autoConnect = true } = options

  const sseClientRef = useRef<ChatSSEClient | null>(null)
  const [isConnected, setIsConnected] = useState(false)

  const { user } = useAuth()

  const {
    currentConversation,
    isStreaming,
    isLoading,
    thinkingSteps,
    reportContent,
    currentStatus,
    pendingInteraction,
    addUserMessage,
    addAgentResponse,
    addThinkingStep,
    completeThinkingStep,
    addAgentPrompt,
    addErrorCard,
    setCurrentStatus,
    setPendingInteraction,
    clearPendingInteraction,
    setLoading,
    setStreaming,
    createConversation: storeCreateConversation,
    setCurrentUser,
    getUserConversations,
    selectConversation: storeSelectConversation,
    startDeepResearch,
    addDeepResearchBanner,
    addAgentResponseWithMeta,
    addPlanMessage,
    updateConversationTitle,
  } = useChatStore()

  // Hydrate from server on auth
  useEffect(() => {
    const userId = user?.id ?? null
    setCurrentUser(userId)
    if (userId) {
      import('../store').then(({ hydrateFromServer }) => {
        hydrateFromServer()
      })
    }
  }, [user?.id, setCurrentUser])

  // Track current thinking step for appending
  const currentThinkingStepIdRef = useRef<string | null>(null)

  /**
   * Connect SSE stream to the current conversation
   */
  const connectSSE = useCallback((conversationId: string) => {
    // Disconnect existing client
    sseClientRef.current?.disconnect()

    const client = createChatSSEClient({
      conversationId,
      callbacks: {
        onConnected: () => {
          setIsConnected(true)
        },

        onChatResponse: (content) => {
          if (content && content.trim()) {
            addAgentResponse(content)
          }
          // Complete any pending thinking step
          if (currentThinkingStepIdRef.current) {
            completeThinkingStep(currentThinkingStepIdRef.current)
            currentThinkingStepIdRef.current = null
          }
          setStreaming(false)
          setLoading(false)
          setCurrentStatus('complete')
          clearPendingInteraction()
        },

        onChatComplete: () => {
          // Turn complete — input is re-enabled by onChatResponse
        },

        onChatError: (error) => {
          addErrorCard('agent.response_failed', error)
          setStreaming(false)
          setLoading(false)
        },

        onChatCancelled: () => {
          setStreaming(false)
          setLoading(false)
          setCurrentStatus('complete')
        },

        // HITL
        onInteractionRequired: (promptId, text, inputType, options) => {
          const typedInputType = inputType as PendingInteraction['inputType']
          const interaction: PendingInteraction = {
            id: promptId,
            parentId: promptId,
            inputType: typedInputType,
            text,
            options: options as string[] | undefined,
          }
          setPendingInteraction(interaction)

          addPlanMessage({ text, inputType: typedInputType })

          const promptType = typedInputType === 'binary_choice' ? 'approval' : 'clarification'
          addAgentPrompt(
            promptType,
            text,
            options as string[] | undefined,
            undefined,
            promptId,
            promptId,
            typedInputType,
          )

          setStreaming(false)
          setLoading(false)
        },

        onInteractionTimeout: (promptId) => {
          clearPendingInteraction()
          addErrorCard('connection.timeout', 'The interaction timed out waiting for a response')
        },

        // Deep research escalation
        onDeepResearchStarted: (jobId) => {
          addDeepResearchBanner('starting', jobId)
          const messageId = addAgentResponseWithMeta('', false, {
            deepResearchJobId: jobId,
            deepResearchJobStatus: 'submitted',
            isDeepResearchActive: true,
          })
          startDeepResearch(jobId, messageId)
          setLoading(false)
        },

        onDeepResearchComplete: (jobId, status) => {
          addDeepResearchBanner(status === 'success' ? 'success' : 'failure', jobId)
          setStreaming(false)
        },

        // Intermediate steps from inline execution
        onStep: (stepType, id, name, _payload, parentId) => {
          if (stepType === 'step.LLM_START' || stepType === 'step.FUNCTION_START') {
            const stepId = addThinkingStep({
              category: 'agents',
              functionName: name,
              displayName: name,
              content: 'Processing...\n',
              isComplete: false,
            })
            currentThinkingStepIdRef.current = stepId
            setCurrentStatus('thinking')
          } else if (stepType === 'step.LLM_END' || stepType === 'step.FUNCTION_END') {
            if (currentThinkingStepIdRef.current) {
              completeThinkingStep(currentThinkingStepIdRef.current)
              currentThinkingStepIdRef.current = null
            }
          }
        },

        onDisconnect: () => {
          setIsConnected(false)
        },

        onError: (error) => {
          console.error('[sse-chat] SSE error:', error)
          setIsConnected(false)
        },

        onHeartbeat: () => {
          // keepalive — no action needed
        },
      },
    })

    client.connect()
    sseClientRef.current = client
  }, [
    addAgentResponse, addThinkingStep, completeThinkingStep, addAgentPrompt,
    addErrorCard, setCurrentStatus, setPendingInteraction, clearPendingInteraction,
    setLoading, setStreaming, startDeepResearch, addDeepResearchBanner,
    addAgentResponseWithMeta, addPlanMessage,
  ])

  // Auto-connect when conversation changes
  useEffect(() => {
    if (!autoConnect || !currentConversation?.id) return

    connectSSE(currentConversation.id)

    return () => {
      sseClientRef.current?.disconnect()
      sseClientRef.current = null
    }
  }, [autoConnect, currentConversation?.id, connectSSE])

  /**
   * Send a user message
   */
  const sendMessage = useCallback(async (content: string) => {
    if (!content.trim()) return

    const conversationId = currentConversation?.id || undefined
    const layoutState = useLayoutStore.getState()
    const enabledDataSourceIds = [...layoutState.enabledDataSourceIds]

    setLoading(true)
    setStreaming(true)
    setCurrentStatus('thinking')

    try {
      // POST to backend first to get the canonical conversation_id
      const result = await chatApi.sendMessage({
        conversation_id: conversationId,
        message: content,
        data_sources: enabledDataSourceIds.length > 0 ? enabledDataSourceIds : undefined,
      })

      // If backend created a new conversation, ensure it exists in the store
      // before adding the user message (so addUserMessage doesn't create a
      // local conversation with a mismatched ID).
      if (!conversationId || result.conversation_id !== conversationId) {
        const store = useChatStore.getState()
        const exists = store.conversations.some((c) => c.id === result.conversation_id)
        if (!exists) {
          const newConv: Conversation = {
            id: result.conversation_id,
            userId: store.currentUserId || 'default-user',
            title: content.length > 60 ? content.slice(0, 57) + '...' : content,
            messages: [],
            createdAt: new Date(),
            updatedAt: new Date(),
            enabledDataSourceIds: enabledDataSourceIds,
          }
          useChatStore.setState((state) => ({
            conversations: [newConv, ...state.conversations],
            currentConversation: newConv,
          }))
        } else {
          storeSelectConversation(result.conversation_id)
        }
      }

      // Now add the user message to the current conversation
      addUserMessage(content)

      // Connect SSE if needed
      if (!conversationId || result.conversation_id !== conversationId) {
        connectSSE(result.conversation_id)
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to send message'
      addErrorCard('connection.failed', message)
      setLoading(false)
      setStreaming(false)
    }
  }, [currentConversation?.id, addUserMessage, setLoading, setStreaming, setCurrentStatus, addErrorCard, connectSSE, storeSelectConversation])

  /**
   * Respond to a HITL interaction
   */
  const respondToInteraction = useCallback((response: string) => {
    const interaction = useChatStore.getState().pendingInteraction
    if (!interaction || !currentConversation?.id) return

    // Update UI
    const { respondToPrompt } = useChatStore.getState()
    const messages = currentConversation.messages || []
    const lastPrompt = [...messages].reverse().find(m => m.promptId)
    if (lastPrompt) {
      respondToPrompt(lastPrompt.id, response)
    }

    // Send to backend
    setStreaming(true)
    setLoading(true)
    chatApi.respondToInteraction(currentConversation.id, { response }).catch((err) => {
      console.error('[sse-chat] Failed to respond:', err)
      setStreaming(false)
      setLoading(false)
    })

    clearPendingInteraction()
  }, [currentConversation, setStreaming, setLoading, clearPendingInteraction])

  const connect = useCallback(() => {
    if (currentConversation?.id) {
      connectSSE(currentConversation.id)
    }
  }, [currentConversation?.id, connectSSE])

  const disconnect = useCallback(() => {
    sseClientRef.current?.disconnect()
    sseClientRef.current = null
    setIsConnected(false)
    setStreaming(false)
    setLoading(false)
  }, [setStreaming, setLoading])

  const createConversation = useCallback(() => {
    storeCreateConversation()
  }, [storeCreateConversation])

  const selectConversation = useCallback((conversationId: string) => {
    storeSelectConversation(conversationId)
  }, [storeSelectConversation])

  const userConversations = getUserConversations()

  return {
    sendMessage,
    respondToInteraction,
    disconnect,
    connect,
    isConnected,
    isStreaming,
    isLoading,
    messages: currentConversation?.messages ?? [],
    conversation: currentConversation,
    createConversation,
    userConversations,
    selectConversation,
    thinkingSteps,
    reportContent,
    currentStatus,
    pendingInteraction,
  }
}
