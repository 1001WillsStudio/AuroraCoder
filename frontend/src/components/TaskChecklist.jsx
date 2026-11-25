import { useEffect, useState } from 'react'
import { Check, ChevronDown, ChevronRight, Circle } from 'lucide-react'
import useLanguage from '../hooks/useLanguage'
import { readTask } from '../services/api'
import '../styles/task-checklist.css'

export default function TaskChecklist({ conversationId, isStreaming, onOpenConversation }) {
  const { t } = useLanguage()
  const [task, setTask] = useState(null)
  const [expanded, setExpanded] = useState(true)
  const active = task?.status === 'active'

  useEffect(() => {
    if (!conversationId) return
    const controller = new AbortController()
    const refresh = async () => {
      try {
        const next = await readTask(conversationId, controller.signal)
        if (!controller.signal.aborted) {
          setTask(previous => previous?.id === next?.id && previous?.revision > next?.revision ? previous : next)
        }
      } catch {
        // The conversation can still be used if its checklist is temporarily unavailable.
      }
    }
    refresh()
    const timer = (active || isStreaming) ? setInterval(refresh, 1500) : null
    return () => {
      controller.abort()
      if (timer) clearInterval(timer)
    }
  }, [conversationId, active, isStreaming])

  if (!task) return null

  const items = task.progress.items
  const verified = items.filter(item => item.evidence).length

  return (
    <section className="task-checklist" aria-label={t('task.title')}>
      <div className="task-checklist-card">
        <div className="task-checklist-header">
          <button className="task-checklist-summary" type="button" aria-expanded={expanded}
            onClick={() => setExpanded(value => !value)}>
            {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
            <span>{task.objective}</span>
            <small>{verified}/{items.length} · {t(`task.status.${task.status}`)}</small>
          </button>
          {task.conversation_id !== conversationId && (
            <button type="button" className="task-checklist-open"
              onClick={() => onOpenConversation(task.conversation_id)}>
              {t('task.openCurrent')}
            </button>
          )}
        </div>
        {expanded && (
          <div className="task-checklist-details">
            <ul>
              {items.map(item => (
                <li key={item.text}>
                  {item.evidence ? <Check className="task-checklist-check" size={16} /> : <Circle size={13} />}
                  <div>
                    <div>{item.text}</div>
                    {item.evidence && <small>{item.evidence}</small>}
                  </div>
                </li>
              ))}
            </ul>
            {task.status !== 'completed' && task.progress.checkpoint && <p>{task.progress.checkpoint}</p>}
            {task.progress.blocker && <p className="task-checklist-notice">{task.progress.blocker}</p>}
          </div>
        )}
        {task.reason && task.reason !== 'paused' && (
          <p className="task-checklist-notice" role="status">{t(`task.reason.${task.reason}`)}</p>
        )}
      </div>
    </section>
  )
}
