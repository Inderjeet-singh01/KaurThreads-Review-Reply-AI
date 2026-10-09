import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

/** Send the user to the login page when the review data reports an expired session. */
export function useSignOutOnAuthError(isAuthError: boolean, onAuthExpired: () => void) {
  const navigate = useNavigate()
  useEffect(() => {
    if (isAuthError) {
      onAuthExpired()
      navigate('/login')
    }
  }, [isAuthError, onAuthExpired, navigate])
}
