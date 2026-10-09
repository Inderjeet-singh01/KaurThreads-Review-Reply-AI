import { LENGTH_OPTIONS, TONE_OPTIONS } from '../lib/replyPreferences'
import type { ReplyLength, ReplyTone } from '../lib/types'
import { SelectField } from './FormControls'

interface ReplyControlsProps {
  tone: ReplyTone
  length: ReplyLength
  onToneChange: (tone: ReplyTone) => void
  onLengthChange: (length: ReplyLength) => void
  disabled?: boolean
}

/** Tone and length hints for AI reply generation. */
export function ReplyControls({ tone, length, onToneChange, onLengthChange, disabled }: ReplyControlsProps) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <SelectField
        label="Tone"
        showLabel
        value={tone}
        options={TONE_OPTIONS}
        onChange={onToneChange}
        disabled={disabled}
      />
      <SelectField
        label="Length"
        showLabel
        value={length}
        options={LENGTH_OPTIONS}
        onChange={onLengthChange}
        disabled={disabled}
      />
    </div>
  )
}
