import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { LanguageProvider } from './i18n/LanguageContext'
import './styles/tokens.css'
import './styles/reset.css'
import './styles/layout.css'
import './styles/sidebar.css'
import './styles/file-tree.css'
import './styles/code-panel.css'
import './styles/messages.css'
import './styles/tool-activity.css'
import './styles/input.css'
import './styles/welcome.css'
import './styles/responsive.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <LanguageProvider>
      <App />
    </LanguageProvider>
  </React.StrictMode>,
)
