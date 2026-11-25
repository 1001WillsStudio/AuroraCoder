import { useEffect, useState } from 'react'
import { Check, ChevronDown, ChevronRight, Circle } from 'lucide-react'
import useLanguage from '../hooks/useLanguage'
import { readTask } from '../services/api'
import '../styles/task-checklist.css'

export default function TaskChecklist({ conversationId, isStreaming, onOpenConversation }) {
  const { t } = useLanguage()
  const [snapshot, setSnapshot] = useState(null)
  const task = snapshot?.task
  const active = task?.status === 'active'

  useEffect(() => {
    if (!conversationId) return
    const controller = new AbortController()
    const refresh = async () => {
      try {
        const next = await readTask(conversationId, controller.signal)
        if (!controller.signal.aborted) {
          setSnapshot(previous => previous?.task?.id === next.task?.id &&
            previous?.task?.revision > next.task?.revision ? previous : next)
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

  return (
    <section className="task-checklist" aria-label={t('task.title')}>
      <TaskCard key={task.id} task={task} conversationId={conversationId}
        onOpenConversation={onOpenConversation} initiallyExpanded />
      {!!snapshot.completed_tasks?.length && (
        <details className="task-checklist-history">
          <summary>{t('task.history')} ({snapshot.completed_tasks.length})</summary>
          {snapshot.completed_tasks.map(completed => (
            <TaskCard key={completed.id} task={completed} conversationId={conversationId}
              onOpenConversation={onOpenConversation} />
          ))}
        </details>
      )}
    </section>
  )
}

function TaskCard({ task, conversationId, onOpenConversation, initiallyExpanded = false }) {
  const { t } = useLanguage()
  const [expanded, setExpanded] = useState(initiallyExpanded)
  const items = task.progress.items
  const verified = items.filter(item => item.evidence).length

  return (
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
  )
}
