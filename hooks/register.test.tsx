import type { On } from 'claude-code'
import { expect, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'

import { formatContextTokens, formatTokens } from './register'

// The test runner provides setTimeout; the plugin environment's typings, which tests share, leave it out.
declare function setTimeout(callback: (value: unknown) => void, ms: number): unknown

const BAND_PROPS = {
  hasSurvey: false,
  isWorking: false,
  maxRows: 4,
  bodyColumns: 120,
  scroll: { offset: 0, bodyRows: 4 },
  view: {},
}

test('token counts are compact at every magnitude', async () => {
  expect(formatTokens(0)).toBe('0')
  expect(formatTokens(999)).toBe('999')
  expect(formatTokens(1_000)).toBe('1k')
  expect(formatTokens(134_212)).toBe('134k')
  expect(formatTokens(1_000_000)).toBe('1.00M')
  expect(formatTokens(2_979_040)).toBe('2.98M')
})

test('context is its token count alone, a dash until the first response reports one', async () => {
  expect(formatContextTokens(null)).toBe('–')
  expect(formatContextTokens({ window: 1_000_000 })).toBe('–')
  expect(formatContextTokens({ window: 1_000_000, tokens: 200_400, percent: 20 })).toBe('200k')
})

test('band shows bold labels and plain values, without the window size or limit windows', async ($, on) => {
  on('session.measure', ($, e) => ({ changed: e.changed }))
  await $.session.measure({
    context: { window: 1_000_000, tokens: 200_400, percent: 20 },
    rateLimits: [{ kind: 'five_hour', percentUsed: 23.5 }],
    changed: ['context', 'rateLimits'],
  })
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ plugin: 'usage-meters', surface, component: 'AbovePrompt', props: BAND_PROPS })
    const texts = await ui.findAll({ type: 'Text' })
    expect(texts.map(text => text.text)).toEqual(['Context', '200k', 'NCR tok', '…', 'Last hour', '…'])
    expect(texts.map(text => text.props.bold === true)).toEqual([true, false, true, false, true, false])
    expect(texts.some(text => text.props.color !== undefined)).toBe(false)
    await ui.unmount()
  }
})

async function mountAfterTally(
  $: Engine,
  on: On,
  run: { exitCode: number; stdout: string; stderr: string },
): Promise<string[]> {
  on('session.measure', ($, e) => ({ changed: e.changed }))
  on('session.id', () => ({ value: 'test-session' }))
  on('process.run', () => ({ value: { ...run, isStdoutTruncated: false, isStderrTruncated: false } }))
  await $.session.measure({ context: { window: 1_000_000, tokens: 5_000, percent: 1 }, rateLimits: [], changed: ['cost'] })
  // The plugin starts the count without awaiting it, so the measurement hook stays fast; let it land.
  await new Promise(resolve => setTimeout(resolve, 50))
  const ui = await $.ui.mount({ plugin: 'usage-meters', surface: 'terminal', component: 'AbovePrompt', props: BAND_PROPS })
  const texts = (await ui.findAll({ type: 'Text' })).map(text => text.text)
  await ui.unmount()
  return texts
}

test('band shows today under NCR tok and the last hour as its own item once counted', async ($, on) => {
  const texts = await mountAfterTally($, on, { exitCode: 0, stdout: '{"today": 2979040, "lastHour": 84000}', stderr: '' })
  expect(texts).toEqual(['Context', '5k', 'NCR tok', '2.98M', 'Last hour', '84k'])
})

test('band shows a failed count as an error, never as zero', async ($, on) => {
  const texts = await mountAfterTally($, on, { exitCode: 1, stdout: '', stderr: 'Traceback\nRuntimeError: no transcript' })
  expect(texts).toEqual(['Context', '5k', 'NCR tok', 'error: RuntimeError: no transcript'])
})

test('band yields to a survey', async ($, on) => {
  on('ui.render', { component: 'AbovePrompt' }, ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>engine band</Text>
  })
  const ui = await $.ui.mount({
    plugin: 'usage-meters',
    surface: 'terminal',
    component: 'AbovePrompt',
    props: { ...BAND_PROPS, hasSurvey: true },
  })
  expect(await ui.find({ type: 'Text', text: /^Context/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: 'engine band' })).toBeDefined()
  await ui.unmount()
})
