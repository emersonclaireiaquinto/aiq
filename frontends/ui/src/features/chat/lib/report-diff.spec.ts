// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, test, expect } from 'vitest'
import { computeReportDiff } from './report-diff'

describe('computeReportDiff', () => {
  test('identical inputs produce all-unchanged sections', () => {
    const content = '## Intro\nHello world\n## Methods\nSome method'
    const result = computeReportDiff(content, content)

    expect(result).toHaveLength(2)
    expect(result.every((s) => s.changeKind === 'unchanged')).toBe(true)
  })

  test('pure addition produces an added section', () => {
    const oldContent = '## Intro\nHello world'
    const newContent = '## Intro\nHello world\n## New Section\nBrand new content'
    const result = computeReportDiff(oldContent, newContent)

    expect(result).toHaveLength(2)
    expect(result[0].changeKind).toBe('unchanged')
    expect(result[1].changeKind).toBe('added')
    expect(result[1].heading).toBe('New Section')
  })

  test('pure removal produces a removed section', () => {
    const oldContent = '## Intro\nHello\n## Extra\nGoodbye'
    const newContent = '## Intro\nHello'
    const result = computeReportDiff(oldContent, newContent)

    const removed = result.find((s) => s.changeKind === 'removed')
    expect(removed).toBeDefined()
    expect(removed!.heading).toBe('Extra')
  })

  test('modification produces mixed tokens', () => {
    const oldContent = '## Intro\nHello world'
    const newContent = '## Intro\nHello universe'
    const result = computeReportDiff(oldContent, newContent)

    expect(result).toHaveLength(1)
    expect(result[0].changeKind).toBe('modified')
    const hasAdded = result[0].tokens.some((t) => t.added)
    const hasRemoved = result[0].tokens.some((t) => t.removed)
    expect(hasAdded).toBe(true)
    expect(hasRemoved).toBe(true)
  })

  test('handles preamble before first heading', () => {
    const oldContent = 'Preamble text\n## Section\nBody'
    const newContent = 'Preamble text\n## Section\nBody'
    const result = computeReportDiff(oldContent, newContent)

    expect(result).toHaveLength(2)
    expect(result[0].heading).toBe('(preamble)')
    expect(result[0].changeKind).toBe('unchanged')
  })

  test('handles empty inputs', () => {
    const result = computeReportDiff('', '')
    expect(result).toHaveLength(1)
    expect(result[0].changeKind).toBe('unchanged')
  })

  test('section reordering produces sensible output', () => {
    const oldContent = '## A\nContent A\n## B\nContent B'
    const newContent = '## B\nContent B\n## A\nContent A'
    const result = computeReportDiff(oldContent, newContent)

    const headings = result.map((s) => s.heading)
    expect(headings).toContain('A')
    expect(headings).toContain('B')
    expect(result.every((s) => s.changeKind === 'unchanged')).toBe(true)
  })
})
