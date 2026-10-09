import { ChevronDown, Search, X } from 'lucide-react'
import { classNames } from '../lib/utils'

/** Search box with a clear button. */
export function SearchInput({
  value,
  onChange,
  placeholder = 'Search…',
  label,
  className,
}: {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  /** Accessible name. */
  label: string
  className?: string
}) {
  return (
    <div className={classNames('relative', className)}>
      <Search
        className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
        aria-hidden
      />
      <input
        type="search"
        className="input !py-2 pl-9 pr-8 [&::-webkit-search-cancel-button]:hidden"
        placeholder={placeholder}
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange('')}
          className="focus-ring absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1 text-slate-400 hover:text-slate-600"
          aria-label="Clear search"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  )
}

/** Native select (keyboard and mobile friendly) with the app's styling. */
export function SelectField<T extends string>({
  value,
  onChange,
  options,
  label,
  showLabel = false,
  disabled,
  className,
}: {
  value: T
  onChange: (value: T) => void
  options: ReadonlyArray<{ value: T; label: string }>
  label: string
  /** Show the label above the field (otherwise it is only the accessible name). */
  showLabel?: boolean
  disabled?: boolean
  className?: string
}) {
  const select = (
    <div className="relative">
      <select
        className="select !py-2 pr-9"
        value={value}
        disabled={disabled}
        aria-label={showLabel ? undefined : label}
        onChange={(e) => onChange(e.target.value as T)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <ChevronDown
        className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
        aria-hidden
      />
    </div>
  )
  if (!showLabel) return <div className={className}>{select}</div>
  return (
    <label className={classNames('block', className)}>
      <span className="mb-1.5 block text-xs font-semibold text-slate-600">{label}</span>
      {select}
    </label>
  )
}

/** Segmented filter buttons (e.g. Needs reply / All / Replied) with optional counts. */
export function SegmentedControl<T extends string>({
  value,
  onChange,
  options,
  label,
}: {
  value: T
  onChange: (value: T) => void
  options: ReadonlyArray<{ value: T; label: string; count?: number }>
  label: string
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-[10px] bg-slate-100 p-1">
      {options.map((option) => {
        const active = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option.value)}
            className={classNames(
              'focus-ring inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-[13px] font-semibold transition-colors',
              active ? 'bg-white text-ink shadow-sm' : 'text-slate-600 hover:text-ink',
            )}
          >
            {option.label}
            {option.count != null && (
              <span
                className={classNames(
                  'rounded-full px-1.5 text-[11px] tabular-nums',
                  active ? 'bg-brand-50 text-brand-700' : 'bg-slate-200/70 text-slate-600',
                )}
              >
                {option.count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
