// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { render, screen, fireEvent } from '@/test-utils'
import { describe, test, expect } from 'vitest'
import { ReportDiffView } from './ReportDiffView'

describe('ReportDiffView', () => {
  test('renders legend', () => {
    const content = ['## A', 'Hello'].join('\n')
    render(<ReportDiffView oldContent={content} newContent={content} />)
    expect(screen.getByText('added')).toBeInTheDocument()
    expect(screen.getByText('removed')).toBeInTheDocument()
    expect(screen.getByText('unchanged (collapsed)')).toBeInTheDocument()
  })

  test('renders unchanged sections as collapsed', () => {
    const content = ['## A', 'Hello'].join('\n')
    render(<ReportDiffView oldContent={content} newContent={content} />)
    const toggle = screen.getByTestId('diff-unchanged-toggle')
    expect(toggle).toBeInTheDocument()
    expect(toggle).toHaveTextContent('unchanged')
  })

  test('expand/collapse works on unchanged sections', () => {
    const content = ['## A', 'Hello world'].join('\n')
    render(<ReportDiffView oldContent={content} newContent={content} />)
    const toggle = screen.getByTestId('diff-unchanged-toggle')

    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText(/Hello world/)).toBeInTheDocument()
  })

  test('renders added sections with green styling', () => {
    const oldText = ['## A', 'Old'].join('\n')
    const newText = ['## A', 'Old', '## B', 'New content'].join('\n')
    render(<ReportDiffView oldContent={oldText} newContent={newText} />)
    expect(screen.getByTestId('diff-section-added')).toBeInTheDocument()
  })

  test('renders modified sections with diff tokens', () => {
    const oldText = ['## A', 'Hello world'].join('\n')
    const newText = ['## A', 'Hello universe'].join('\n')
    render(
      <ReportDiffView
        oldContent={oldText}
        newContent={newText}
      />
    )
    expect(screen.getByTestId('diff-section-modified')).toBeInTheDocument()
    expect(screen.getByTestId('diff-added')).toHaveTextContent('universe')
    expect(screen.getByTestId('diff-removed')).toHaveTextContent('world')
  })

  test('renders removed sections', () => {
    const oldText = ['## A', 'Keep', '## B', 'Remove me'].join('\n')
    const newText = ['## A', 'Keep'].join('\n')
    render(<ReportDiffView oldContent={oldText} newContent={newText} />)
    expect(screen.getByTestId('diff-section-removed')).toBeInTheDocument()
  })
})
