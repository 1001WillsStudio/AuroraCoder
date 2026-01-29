import React from 'react'
import { Brain, Loader2 } from 'lucide-react'

function ThinkingIndicator({ content }) {
  // Truncate long thinking for display
  const displayContent = content.length > 500 
    ? content.slice(-500) + '...' 
    : content

  return (
    <div className="thinking-indicator">
      <div className="thinking-header">
        <Brain size={18} className="thinking-brain" />
        <span>Thinking</span>
        <Loader2 size={16} className="spin" />
      </div>
      <div className="thinking-preview">
        <pre>{displayContent}</pre>
      </div>
    </div>
  )
}

export default ThinkingIndicator
