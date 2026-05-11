// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { render, screen, fireEvent } from '@/test-utils'
import { vi, describe, test, expect, beforeEach } from 'vitest'

const mockSelectReportVersion = vi.fn()

type MockVersion = { versionId: string; parentVersionId: string | null; content: string; triggeringQuery: string; createdAt: Date }

let mockVersions: MockVersion[] = []

let mockState = {
  currentConversation: { reportVersions: mockVersions } as { reportVersions: MockVersion[] } | null,
  selectedReportVersionId: null as string | null,
  selectReportVersion: mockSelectReportVersion,
}

vi.mock('@/features/chat', () => ({
  useChatStore: vi.fn((selector?: (s: typeof mockState) => unknown) => {
    if (selector) return selector(mockState)
    return mockState
  }),
}))

import { ReportVersionSelector } from './ReportVersionSelector'

describe('ReportVersionSelector', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockVersions = []
    mockState = {
      currentConversation: { reportVersions: mockVersions },
      selectedReportVersionId: null,
      selectReportVersion: mockSelectReportVersion,
    }
  })

  test('renders nothing when zero versions', () => {
    render(<ReportVersionSelector />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  test('renders nothing when one version', () => {
    mockState.currentConversation!.reportVersions = [
      { versionId: 'v1', parentVersionId: null, content: 'Report v1', triggeringQuery: 'First query', createdAt: new Date('2026-01-01') },
    ]
    mockState.selectedReportVersionId = 'v1'

    render(<ReportVersionSelector />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  test('renders pills for multiple versions', () => {
    mockState.currentConversation!.reportVersions = [
      { versionId: 'v1', parentVersionId: null, content: 'Report v1', triggeringQuery: 'First query', createdAt: new Date('2026-01-01') },
      { versionId: 'v2', parentVersionId: 'v1', content: 'Report v2', triggeringQuery: 'Add section on X', createdAt: new Date('2026-01-02') },
    ]
    mockState.selectedReportVersionId = 'v2'

    render(<ReportVersionSelector />)

    expect(screen.getByText('v1')).toBeInTheDocument()
    expect(screen.getByText('v2 (current)')).toBeInTheDocument()
  })

  test('marks the selected version as pressed', () => {
    mockState.currentConversation!.reportVersions = [
      { versionId: 'v1', parentVersionId: null, content: 'Report v1', triggeringQuery: 'First query', createdAt: new Date('2026-01-01') },
      { versionId: 'v2', parentVersionId: 'v1', content: 'Report v2', triggeringQuery: 'Second query', createdAt: new Date('2026-01-02') },
    ]
    mockState.selectedReportVersionId = 'v1'

    render(<ReportVersionSelector />)

    expect(screen.getByLabelText('Select report v1')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByLabelText('Select report v2 (current)')).toHaveAttribute('aria-pressed', 'false')
  })

  test('calls selectReportVersion on click', () => {
    mockState.currentConversation!.reportVersions = [
      { versionId: 'v1', parentVersionId: null, content: 'Report v1', triggeringQuery: 'First query', createdAt: new Date('2026-01-01') },
      { versionId: 'v2', parentVersionId: 'v1', content: 'Report v2', triggeringQuery: 'Second query', createdAt: new Date('2026-01-02') },
    ]
    mockState.selectedReportVersionId = 'v2'

    render(<ReportVersionSelector />)

    fireEvent.click(screen.getByText('v1'))
    expect(mockSelectReportVersion).toHaveBeenCalledWith('v1')
  })

  test('shows triggering query in tooltip', () => {
    mockState.currentConversation!.reportVersions = [
      { versionId: 'v1', parentVersionId: null, content: 'Report v1', triggeringQuery: 'Research about AI safety', createdAt: new Date('2026-01-01') },
      { versionId: 'v2', parentVersionId: 'v1', content: 'Report v2', triggeringQuery: 'Add a section on alignment', createdAt: new Date('2026-01-02') },
    ]
    mockState.selectedReportVersionId = 'v2'

    render(<ReportVersionSelector />)

    const v1Button = screen.getByLabelText('Select report v1')
    expect(v1Button.getAttribute('title')).toContain('Research about AI safety')
  })

  test('renders three versions correctly', () => {
    mockState.currentConversation!.reportVersions = [
      { versionId: 'v1', parentVersionId: null, content: 'v1', triggeringQuery: 'q1', createdAt: new Date('2026-01-01') },
      { versionId: 'v2', parentVersionId: 'v1', content: 'v2', triggeringQuery: 'q2', createdAt: new Date('2026-01-02') },
      { versionId: 'v3', parentVersionId: 'v2', content: 'v3', triggeringQuery: 'q3', createdAt: new Date('2026-01-03') },
    ]
    mockState.selectedReportVersionId = 'v3'

    render(<ReportVersionSelector />)

    expect(screen.getByText('v1')).toBeInTheDocument()
    expect(screen.getByText('v2')).toBeInTheDocument()
    expect(screen.getByText('v3 (current)')).toBeInTheDocument()
  })
})
