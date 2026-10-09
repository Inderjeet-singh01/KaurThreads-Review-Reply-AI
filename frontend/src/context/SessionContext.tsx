import { createContext, useContext } from 'react'

/** Lets shell components (outside the routed pages) report an expired Google session. */
export const SessionContext = createContext<{ onAuthExpired: () => void }>({
  onAuthExpired: () => undefined,
})

// eslint-disable-next-line react-refresh/only-export-components
export function useSession() {
  return useContext(SessionContext)
}
