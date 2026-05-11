// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { render, screen } from '@/test-utils'
import { vi, describe, test, expect, beforeEach } from 'vitest'
import { ReportTab } from './ReportTab'

let mockStoreState: Record<string, unknown> = {
  reportContent: '',
  reportContentCategory: null,
  isStreaming: false,
  currentStatus: null,
  currentConversation: { reportVersions: [] },
  selectedReportVersionId: null,
  reportViewMode: 'latest' as const,
  setReportViewMode: vi.fn(),
}

// Mock the chat store
vi.mock('@/features/chat', () => ({
  useChatStore: vi.fn((selector?: (s: Record<string, unknown>) => unknown) => {
    if (selector) return selector(mockStoreState)
    return mockStoreState
  }),
  selectReportVersions: (s: Record<string, unknown>) => (s.currentConversation as Record<string, unknown> | null)?.reportVersions ?? [],
}))

// Mock MarkdownRenderer
vi.mock('@/shared/components/MarkdownRenderer', () => ({
  MarkdownRenderer: ({ content, isStreaming }: { content: string; isStreaming?: boolean }) => (
    <div data-testid="markdown" data-streaming={isStreaming}>
      {content}
      {isStreaming && <span data-testid="streaming-indicator">Generating report...</span>}
    </div>
  ),
}))

// Mock ExportFooter
vi.mock('./ExportFooter', () => ({
  ExportFooter: () => <div data-testid="export-footer">Export Footer</div>,
}))

// Mock ReportVersionSelector
vi.mock('./ReportVersionSelector', () => ({
  ReportVersionSelector: () => null,
}))

// Mock ReportDiffView
vi.mock('./ReportDiffView', () => ({
  ReportDiffView: () => <div data-testid="diff-view">Diff View</div>,
}))

describe('ReportTab', () => {
  beforeEach(() => {
    mockStoreState = {
      reportContent: '',
      reportContentCategory: null,
      isStreaming: false,
      currentStatus: null,
      currentConversation: { reportVersions: [] },
      selectedReportVersionId: null,
      reportViewMode: 'latest' as const,
      setReportViewMode: vi.fn(),
    }
  })

  test('displays empty state when no report content', () => {
    render(<ReportTab />)

    expect(screen.getByText(/report content will appear here/i)).toBeInTheDocument()
    expect(document.querySelector('svg')).toBeInTheDocument()
  })

  test('renders report content via MarkdownRenderer', () => {
    mockStoreState.reportContent = '# Report Title\n\nReport content here'

    render(<ReportTab />)

    expect(screen.getByTestId('markdown')).toHaveTextContent('# Report Title')
  })

  test('renders title when provided', () => {
    mockStoreState.reportContent = 'Some content'

    render(<ReportTab />)

    expect(screen.getByText('Some content')).toBeInTheDocument()
  })

  test('shows generating indicator when streaming and writing', () => {
    mockStoreState.reportContent = 'Partial content...'
    mockStoreState.isStreaming = true
    mockStoreState.currentStatus = 'writing'

    render(<ReportTab />)

    expect(screen.getByTestId('streaming-indicator')).toBeInTheDocument()
    expect(screen.getByText('Generating report...')).toBeInTheDocument()
  })

  test('renders children when provided', () => {
    render(
      <ReportTab>
        <div>Custom content</div>
      </ReportTab>
    )

    expect(screen.getByText('Custom content')).toBeInTheDocument()
    expect(screen.queryByTestId('markdown')).not.toBeInTheDocument()
  })

  test('always renders export footer', () => {
    render(<ReportTab />)

    expect(screen.getByTestId('export-footer')).toBeInTheDocument()
  })
})
