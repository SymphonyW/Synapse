export type ComposerKeyIntent = {
  key: string
  shiftKey: boolean
  isComposing: boolean
  disabled: boolean
  value: string
}

export function shouldSubmitComposerOnKeyDown({
  key,
  shiftKey,
  isComposing,
  disabled,
  value,
}: ComposerKeyIntent): boolean {
  return (
    key === 'Enter' &&
    !shiftKey &&
    !isComposing &&
    !disabled &&
    value.trim().length > 0
  )
}
