// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { diffWordsWithSpace } from 'diff'

export interface DiffToken {
  value: string
  added?: boolean
  removed?: boolean
}

export type SectionChangeKind = 'added' | 'removed' | 'modified' | 'unchanged'

export interface DiffSection {
  heading: string
  changeKind: SectionChangeKind
  tokens: DiffToken[]
}

const HEADING_RE = /^#{1,2}\s+/

function splitIntoSections(text: string): { heading: string; body: string }[] {
  const lines = text.split('\n')
  const sections: { heading: string; body: string }[] = []
  let currentHeading = '(preamble)'
  let currentLines: string[] = []

  for (const line of lines) {
    if (HEADING_RE.test(line)) {
      if (currentLines.length > 0 || currentHeading !== '(preamble)') {
        sections.push({ heading: currentHeading, body: currentLines.join('\n') })
      }
      currentHeading = line.replace(HEADING_RE, '').trim()
      currentLines = [line]
    } else {
      currentLines.push(line)
    }
  }

  if (currentLines.length > 0 || sections.length === 0) {
    sections.push({ heading: currentHeading, body: currentLines.join('\n') })
  }

  return sections
}

export function computeReportDiff(oldContent: string, newContent: string): DiffSection[] {
  if (oldContent === newContent) {
    const sections = splitIntoSections(newContent)
    return sections.map((s) => ({
      heading: s.heading,
      changeKind: 'unchanged' as const,
      tokens: [{ value: s.body }],
    }))
  }

  const oldSections = splitIntoSections(oldContent)
  const newSections = splitIntoSections(newContent)

  const oldByHeading = new Map(oldSections.map((s) => [s.heading, s.body]))
  const newByHeading = new Map(newSections.map((s) => [s.heading, s.body]))

  const result: DiffSection[] = []
  const processedHeadings = new Set<string>()

  for (const newSec of newSections) {
    processedHeadings.add(newSec.heading)
    const oldBody = oldByHeading.get(newSec.heading)

    if (oldBody === undefined) {
      result.push({
        heading: newSec.heading,
        changeKind: 'added',
        tokens: [{ value: newSec.body, added: true }],
      })
    } else if (oldBody === newSec.body) {
      result.push({
        heading: newSec.heading,
        changeKind: 'unchanged',
        tokens: [{ value: newSec.body }],
      })
    } else {
      const changes = diffWordsWithSpace(oldBody, newSec.body)
      const tokens: DiffToken[] = changes.map((c) => ({
        value: c.value,
        added: c.added || undefined,
        removed: c.removed || undefined,
      }))
      result.push({
        heading: newSec.heading,
        changeKind: 'modified',
        tokens,
      })
    }
  }

  for (const oldSec of oldSections) {
    if (!processedHeadings.has(oldSec.heading)) {
      result.push({
        heading: oldSec.heading,
        changeKind: 'removed',
        tokens: [{ value: oldSec.body, removed: true }],
      })
    }
  }

  return result
}
