// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client'

import { type FC } from 'react'
import { Flex, Text } from '@/adapters/ui'
import { useChatStore, selectReportVersions } from '@/features/chat'

/**
 * Horizontal pill row for switching between report versions.
 * Hidden when only one version exists.
 */
export const ReportVersionSelector: FC = () => {
  const reportVersions = useChatStore(selectReportVersions)
  const selectedReportVersionId = useChatStore((s) => s.selectedReportVersionId)
  const selectReportVersion = useChatStore((s) => s.selectReportVersion)

  if (reportVersions.length <= 1) return null

  return (
    <Flex align="center" gap="1" className="shrink-0 border-b border-base pb-2 mb-3 flex-wrap">
      {reportVersions.map((version, index) => {
        const isSelected = version.versionId === selectedReportVersionId
        const isCurrent = index === reportVersions.length - 1
        const label = `v${index + 1}${isCurrent ? ' (current)' : ''}`
        const tooltip = `${version.triggeringQuery.slice(0, 80)}${version.triggeringQuery.length > 80 ? '...' : ''}\n${new Date(version.createdAt).toLocaleString()}`

        return (
          <button
            key={version.versionId}
            onClick={() => selectReportVersion(version.versionId)}
            title={tooltip}
            aria-label={`Select report ${label}`}
            aria-pressed={isSelected}
            className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
              isSelected
                ? 'bg-brand text-on-brand'
                : 'bg-surface-secondary text-secondary hover:bg-surface-tertiary'
            }`}
          >
            {label}
          </button>
        )
      })}
    </Flex>
  )
}
