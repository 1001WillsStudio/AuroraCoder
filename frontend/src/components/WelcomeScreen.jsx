import React, { useState, useEffect } from 'react'
import { Code, Search, FileText, Terminal } from 'lucide-react'
import useLanguage from '../hooks/useLanguage'

/**
 * Typing animation hook - types out text character by character
 */
function useTypingAnimation(text, speed = 30, startDelay = 500) {
  const [displayedText, setDisplayedText] = useState('')
  const [isComplete, setIsComplete] = useState(false)

  useEffect(() => {
    setDisplayedText('')
    setIsComplete(false)
    
    const startTimeout = setTimeout(() => {
      let currentIndex = 0
      
      const intervalId = setInterval(() => {
        if (currentIndex < text.length) {
          setDisplayedText(text.slice(0, currentIndex + 1))
          currentIndex++
        } else {
          setIsComplete(true)
          clearInterval(intervalId)
        }
      }, speed)
      
      return () => clearInterval(intervalId)
    }, startDelay)
    
    return () => clearTimeout(startTimeout)
  }, [text, speed, startDelay])

  return { displayedText, isComplete }
}

function WelcomeScreen({ onExampleClick }) {
  const { t } = useLanguage()
  const subtitleText = t('welcome.subtitle')
  
  const { displayedText, isComplete } = useTypingAnimation(subtitleText, 12, 300)
  
  const examples = [
    {
      icon: <Search size={20} />,
      title: t('welcome.example.search'),
      prompt: t('welcome.example.searchPrompt')
    },
    {
      icon: <Code size={20} />,
      title: t('welcome.example.analyze'),
      prompt: t('welcome.example.analyzePrompt')
    },
    {
      icon: <FileText size={20} />,
      title: t('welcome.example.create'),
      prompt: t('welcome.example.createPrompt')
    },
    {
      icon: <Terminal size={20} />,
      title: t('welcome.example.run'),
      prompt: t('welcome.example.runPrompt')
    }
  ]

  return (
    <div className="welcome-screen">
      <div className="welcome-header">
        <div className="welcome-logo">
          <img src="/assets/logo.png" alt="1001 Wills AI Lab" />
        </div>
        <h1>AuroraCoder</h1>
        <p className="welcome-subtitle typing-text">
          {displayedText}
          <span className={`typing-cursor ${isComplete ? 'blink' : ''}`}>|</span>
        </p>
      </div>

      <div className="examples-grid">
        {examples.map((example, idx) => (
          <button 
            key={idx} 
            className="example-card"
            onClick={() => onExampleClick(example.prompt)}
          >
            <div className="example-icon">{example.icon}</div>
            <div className="example-content">
              <h3>{example.title}</h3>
              <p>{example.prompt}</p>
            </div>
          </button>
        ))}
      </div>

      <div className="welcome-footer">
        <p>Start typing or click an example above to begin</p>
      </div>
    </div>
  )
}

export default WelcomeScreen
