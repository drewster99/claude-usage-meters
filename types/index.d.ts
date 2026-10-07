/** The live context window's fill; `tokens`/`percent` absent until the window's first response. */
export type ContextReading = {
  window: number
  tokens?: number
  percent?: number
}

/** This session's (and its subagents') non-cache-read tokens, or why they could not be counted. */
export type TallyReading =
  | { kind: 'pending' }
  | { kind: 'awaitingTranscript' }
  | { kind: 'counted'; today: number; lastHour: number }
  | { kind: 'failed'; reason: string }

declare module 'claude-code' {
  interface PluginState {
    'usage-meters': {
      /** The context fill from the latest usage measurement; null before the first. */
      context: ContextReading | null
      /** The latest token tally. */
      tally: TallyReading
    }
  }
}
