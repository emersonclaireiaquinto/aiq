// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client'

import { type FC, useMemo, useState, useCallback } from 'react'
import { Flex, Text } from '@/adapters/ui'
import { computeReportDiff, type DiffSection, type DiffToken } from '@/features/chat/lib/report-diff'

interface ReportDiffViewProps {
  oldContent: string
  newContent: string
}

const DiffLegend: FC = () => (
  <Flex align="center" gap="3" className="shrink-0 rounded-md border border-base bg-surface-secondary px-3 py-2 mb-3 text-xs">
    <span className="inline-flex items-center gap-1">
      <span className="inline-block h-3 w-3 rounded-sm bg-green-100 dark:bg-green-900/30" />
      <Text kind="body/regular/xs">added</Text>
    </span>
    <span className="inline-flex items-center gap-1">
      <span className="inline-block h-3 w-3 rounded-sm bg-red-100 dark:bg-red-900/30" />
      <Text kind="body/regular/xs">removed</Text>
    </span>
    <span className="inline-flex items-center gap-1">
      <span className="text-subtle">&#9654;</span>
      <Text kind="body/regular/xs">unchanged (collapsed)</Text>
    </span>
  </Flex>
)

const TokenSpan: FC<{ token: DiffToken }> = ({ token }) => {
  if (token.added) {
    return (
      <span className="bg-green-100 dark:bg-green-900/30" data-testid="diff-added">
        {token.value}
      </span>
    )
  }
  if (token.removed) {
    return (
      <span className="bg-red-100 line-through dark:bg-red-900/30" data-testid="diff-removed">
        {token.value}
      </span>
    )
  }
  return <span>{token.value}</span>
}

const SectionView: FC<{ section: DiffSection }> = ({ section }) => {
  const [expanded, setExpanded] = useState(false)
  const toggle = useCallback(() => setExpanded((e) => !e), [])

  if (section.changeKind === 'unchanged') {
    return (
      <div className="my-1">
        <button
          onClick={toggle}
          className="text-left text-sm text-subtle hover:text-primary transition-colors w-full"
          aria-expanded={expanded}
          data-testid="diff-unchanged-toggle"
        >
          {expanded ? '▾' : '▸'} {section.heading === '(preamble)' ? '(preamble)' : `## ${section.heading}`} — unchanged
        </button>
        {expanded && (
          <pre className="mt-1 whitespace-pre-wrap text-sm text-subtle pl-4">
            {section.tokens.map((t, i) => (
              <span key={i}>{t.value}</span>
            ))}
          </pre>
        )}
      </div>
    )
  }

  const label =
    section.changeKind === 'added'
      ? 'bg-green-50 border-green-200 dark:bg-green-950/20 dark:border-green-800'
      : section.changeKind === 'removed'
        ? 'bg-red-50 border-red-200 dark:bg-red-950/20 dark:border-red-800'
        : 'bg-yellow-50 border-yellow-200 dark:bg-yellow-950/20 dark:border-yellow-800'

  return (
    <div className={`my-2 rounded-md border p-3 ${label}`} data-testid={`diff-section-${section.changeKind}`}>
      <Text kind="label/semibold/sm" className="mb-1 block">
        {section.heading === '(preamble)' ? '(preamble)' : `## ${section.heading}`}
        <span className="ml-2 text-xs font-normal opacity-60">{section.changeKind}</span>
      </Text>
      <pre className="whitespace-pre-wrap text-sm">
        {section.tokens.map((token, i) => (
          <TokenSpan key={i} token={token} />
        ))}
      </pre>
    </div>
  )
}

export const ReportDiffView: FC<ReportDiffViewProps> = ({ oldContent, newContent }) => {
  const sections = useMemo(() => computeReportDiff(oldContent, newContent), [oldContent, newContent])

  return (
    <Flex direction="col" className="h-full">
      <DiffLegend />
      <div className="flex-1 overflow-y-auto">
        {sections.map((section, i) => (
          <SectionView key={`${section.heading}-${i}`} section={section} />
        ))}
      </div>
    </Flex>
  )
}
