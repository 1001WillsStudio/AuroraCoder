/**
 * Shared status constants used between backend and frontend.
 *
 * These values flow from main_flow.py → gateway/streaming.py → SSE → App.jsx.
 * Keep in sync with any backend status constants.
 */
export const STATUS = {
  RUNNING: 'running',
  COMPLETED: 'completed',
  ERROR: 'error',
  MAX_ITERATIONS_REACHED: 'max_iterations_reached',
  INTERRUPTED: 'interrupted',
  CONTINUED: 'continued',
  STOPPED: 'stopped',
}
