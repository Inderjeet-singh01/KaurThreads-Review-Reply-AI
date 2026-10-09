import type { ReactNode } from 'react'
import { useSession } from '../context/SessionContext'
import { BusinessSwitcher } from './BusinessSwitcher'

interface PageHeaderProps {
  title: string
  description?: ReactNode
  /** Page-specific controls (date range, refresh, bulk actions…). */
  actions?: ReactNode
}

/** Title row of every app page, with the business switcher on the right. */
export function PageHeader({ title, description, actions }: PageHeaderProps) {
  const { onAuthExpired } = useSession()
  return (
    <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
      <div className="min-w-0">
        <h1 className="text-2xl font-bold tracking-tight text-ink sm:text-[26px]">{title}</h1>
        {description && <p className="mt-1 text-sm text-ink-muted">{description}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2 xl:justify-end">
        {actions}
        <div className="min-w-0 flex-1 sm:flex-none">
          <BusinessSwitcher onAuthExpired={onAuthExpired} />
        </div>
      </div>
    </div>
  )
}
