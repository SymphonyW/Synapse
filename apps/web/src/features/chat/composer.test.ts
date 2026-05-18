import { describe, expect, it } from 'vitest'
import { shouldSubmitComposerOnKeyDown } from './composer'

describe('shouldSubmitComposerOnKeyDown', () => {
  it('submits on plain Enter when the composer has content', () => {
    expect(
      shouldSubmitComposerOnKeyDown({
        key: 'Enter',
        shiftKey: false,
        isComposing: false,
        disabled: false,
        value: 'hello',
      }),
    ).toBe(true)
  })

  it('keeps Shift + Enter for line breaks', () => {
    expect(
      shouldSubmitComposerOnKeyDown({
        key: 'Enter',
        shiftKey: true,
        isComposing: false,
        disabled: false,
        value: 'hello',
      }),
    ).toBe(false)
  })

  it('does not submit while IME composition is active', () => {
    expect(
      shouldSubmitComposerOnKeyDown({
        key: 'Enter',
        shiftKey: false,
        isComposing: true,
        disabled: false,
        value: '你好',
      }),
    ).toBe(false)
  })

  it('does not submit empty or whitespace-only content', () => {
    expect(
      shouldSubmitComposerOnKeyDown({
        key: 'Enter',
        shiftKey: false,
        isComposing: false,
        disabled: false,
        value: '   ',
      }),
    ).toBe(false)
  })

  it('does not submit while disabled', () => {
    expect(
      shouldSubmitComposerOnKeyDown({
        key: 'Enter',
        shiftKey: false,
        isComposing: false,
        disabled: true,
        value: 'hello',
      }),
    ).toBe(false)
  })
})
