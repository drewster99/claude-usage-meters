import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { ContextReading, TallyReading } from '../types'

const REFRESH_INTERVAL_MS = 30_000
const GROUP_GAP = 4

const context = atom({ plugin: 'usage-meters', key: 'context' } as const, null as ContextReading | null)
const tally = atom({ plugin: 'usage-meters', key: 'tally' } as const, { kind: 'pending' } as TallyReading)

// Module state restarts on every reload, which is harmless for a re-entrancy guard.
let isTallyRunning = false

/** Compact token count: `950`, `134k`, `2.98M`. */
export function formatTokens(count: number): string {
  if (count < 1_000) return String(count)
  if (count < 1_000_000) return `${Math.round(count / 1_000)}k`
  return `${(count / 1_000_000).toFixed(2)}M`
}

/** The context's token count alone, or a dash until the live window's first response reports one. */
export function formatContextTokens(reading: ContextReading | null): string {
  return reading?.tokens === undefined ? '–' : formatTokens(reading.tokens)
}

/** Reads the script's JSON report; anything not in the shape it documents is a failure, never a number. */
export function parseTallyReport(stdout: string): TallyReading {
  const report = JSON.parse(stdout) as { transcriptWritten?: unknown; today?: unknown; lastHour?: unknown }
  if (report.transcriptWritten === false) return { kind: 'awaitingTranscript' }
  if (typeof report.today !== 'number' || typeof report.lastHour !== 'number') {
    return { kind: 'failed', reason: `unexpected report: ${stdout.trim()}` }
  }
  return { kind: 'counted', today: report.today, lastHour: report.lastHour }
}

/** One value of the NCR row: a count, a dash before the transcript exists, an ellipsis while counting. */
export function formatTallyValue(reading: TallyReading, pick: (counted: { today: number; lastHour: number }) => number): string {
  switch (reading.kind) {
    case 'counted':
      return formatTokens(pick(reading))
    case 'awaitingTranscript':
      return '–'
    case 'pending':
    case 'failed':
      return '…'
  }
}

async function countTokens($: EngineInterface): Promise<TallyReading> {
  try {
    const sessionId = await $.session.id()
    const script = `${$.plugin.root}/bin/tally_tokens.py`
    const result = await $.process.run(['/usr/bin/python3', '-I', script, sessionId], { timeoutMs: 10_000 })
    if (result.exitCode !== 0) {
      return { kind: 'failed', reason: result.stderr.trim().split('\n').pop() ?? `exit ${result.exitCode}` }
    }
    return parseTallyReport(result.stdout)
  } catch (error) {
    return { kind: 'failed', reason: error instanceof Error ? error.message : String(error) }
  }
}

async function refreshTally($: EngineInterface): Promise<void> {
  // The timer and per-response measurements can overlap; one run at a time is enough.
  if (isTallyRunning) return
  isTallyRunning = true
  try {
    const reading = await countTokens($)
    await update($, tally, () => reading)
  } finally {
    isTallyRunning = false
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    // An earlier version pinned these figures in the status line; the band holds them now.
    $.ui.status(undefined)
    const usage = await $.session.usage()
    await update($, context, () => usage.context)
    // The timer moves the "last 60m" window forward while no responses arrive.
    $.clock.every(REFRESH_INTERVAL_MS, () => void refreshTally($))
    void refreshTally($)
    return next(e)
  })

  // Fires when the context fill, a rate-limit window or the session's cost moves; cost grows with
  // every response, subagents' included, which makes it the per-request refresh trigger.
  on('session.measure', async ($, e, next) => {
    await update($, context, () => e.context)
    if (e.changed.includes('cost')) void refreshTally($)
    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    const fill = await read($, context)
    const tokens = await read($, tally)

    // Styled like the rate-limit rows of the status line: bold label, plain value.
    return (
      <Box flexDirection="row" gap={GROUP_GAP}>
        <Box key="context" flexDirection="row" gap={1}>
          <Text bold>Context</Text>
          <Text>{formatContextTokens(fill)}</Text>
        </Box>
        {tokens.kind === 'failed' ? (
          <Box key="ncr-failed" flexDirection="row" gap={1}>
            <Text bold>NCR tok</Text>
            <Text color="error">{`error: ${tokens.reason}`}</Text>
          </Box>
        ) : (
          <Box key="ncr" flexDirection="row" gap={GROUP_GAP}>
            <Box flexDirection="row" gap={1}>
              <Text bold>NCR tok</Text>
              <Text>{formatTallyValue(tokens, counted => counted.today)}</Text>
            </Box>
            <Box flexDirection="row" gap={1}>
              <Text bold>Last hour</Text>
              <Text>{formatTallyValue(tokens, counted => counted.lastHour)}</Text>
            </Box>
          </Box>
        )}
      </Box>
    )
  })
}
