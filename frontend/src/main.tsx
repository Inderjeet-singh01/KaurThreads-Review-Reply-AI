import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { ToastProvider } from './context/ToastContext'
import { BusinessProvider } from './context/BusinessContext'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <ToastProvider>
        <BusinessProvider>
          <App />
        </BusinessProvider>
      </ToastProvider>
    </BrowserRouter>
  </StrictMode>,
)
