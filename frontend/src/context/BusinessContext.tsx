import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import type { LocationSummary } from '../lib/types'

const STORAGE_KEY = 'rra.selectedBusiness'

interface BusinessContextValue {
  business: LocationSummary | null
  selectBusiness: (business: LocationSummary) => void
  clearBusiness: () => void
}

const BusinessContext = createContext<BusinessContextValue | null>(null)

function readStored(): LocationSummary | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as LocationSummary) : null
  } catch {
    return null
  }
}

export function BusinessProvider({ children }: { children: ReactNode }) {
  const [business, setBusiness] = useState<LocationSummary | null>(readStored)

  const selectBusiness = useCallback((next: LocationSummary) => {
    setBusiness(next)
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch {
      /* storage unavailable — keep in-memory only */
    }
  }, [])

  const clearBusiness = useCallback(() => {
    setBusiness(null)
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch {
      /* ignore */
    }
  }, [])

  const value = useMemo<BusinessContextValue>(
    () => ({ business, selectBusiness, clearBusiness }),
    [business, selectBusiness, clearBusiness],
  )

  return <BusinessContext.Provider value={value}>{children}</BusinessContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useBusiness(): BusinessContextValue {
  const ctx = useContext(BusinessContext)
  if (!ctx) throw new Error('useBusiness must be used within a BusinessProvider')
  return ctx
}
