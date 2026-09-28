import { ChevronDown } from 'lucide-react'
import type { ReplyLength, ReplyTone } from '../lib/types'

const TONE_OPTIONS: ReplyTone[] = [
  'Friendly & Professional',
  'Warm & Personal',
  'Professional',
  'Apologetic',
]

const LENGTH_OPTIONS: ReplyLength[] = ['Short', 'Medium', 'Long']

interface ReplyControlsProps {
  tone: ReplyTone
  length: ReplyLength
  onToneChange: (tone: ReplyTone) => void
  onLengthChange: (length: ReplyLength) => void
  disabled?: boolean
}

function LabeledSelect<T extends string>({
  label,
  value,
  options,
  onChange,
  disabled,
}: {
  label: string
  value: T
  options: T[]
  onChange: (value: T) => void
  disabled?: boolean
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-semibold text-slate-600">{label}</span>
      <div className="relative">
        <select
          className="select pr-9"
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value as T)}
        >
          {options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
        <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
      </div>
    </label>
  )
}

export function ReplyControls({
  tone,
  length,
  onToneChange,
  onLengthChange,
  disabled,
}: ReplyControlsProps) {
  return (
    <div className="grid grid-cols-2 gap-3">
      <LabeledSelect
        label="Tone"
        value={tone}
        options={TONE_OPTIONS}
        onChange={onToneChange}
        disabled={disabled}
      />
      <LabeledSelect
        label="Length"
        value={length}
        options={LENGTH_OPTIONS}
        onChange={onLengthChange}
        disabled={disabled}
      />
    </div>
  )
}
