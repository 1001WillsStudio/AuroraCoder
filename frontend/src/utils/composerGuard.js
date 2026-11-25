/**
 * Guards the composer so a double-click on Send (or double Enter) cannot
 * abort the in-flight /api/chat or open a second conversation.
 *
 * Two races share one cause: handleSend is async and isStreaming is React
 * state, so a second activation arrives while the first is still in
 * getActiveStreams / streamChat. The second call used to abort() the first
 * controller, which left a user bubble and an empty assistant.
 */

/**
 * The 2nd click of a double-click has detail === 2, even if React swapped
 * Send for Stop between the two clicks (same screen position).
 * @param {{detail?: number}|null|undefined} event
 * @returns {boolean}
 */
export function isPrimaryClick(event) {
  return (event?.detail ?? 1) <= 1
}

/**
 * A terminal follow-up can start while the previous response is closing.
 * @param {boolean} inFlight
 * @param {boolean} afterTurn
 * @returns {boolean}
 */
export function shouldBeginSend(inFlight, afterTurn) {
  return Boolean(afterTurn) || !inFlight
}

/** Ignore Stop for this long after Send so a double-click cannot abort. */
export const STOP_ARM_MS = 500

/**
 * React may swap Send for Stop between the two clicks of a double-click.
 * Some browsers then report the second click as detail 1 on the new node.
 * @param {number} sendStartedAt
 * @param {number} [now]
 * @param {number} [armMs]
 * @returns {boolean}
 */
export function shouldAcceptStop(sendStartedAt, now = Date.now(), armMs = STOP_ARM_MS) {
  if (!sendStartedAt) return true
  return (now - sendStartedAt) >= armMs
}

/**
 * Tokened send lock. A completed request cannot clear its follow-up's lock.
 */
export function createSendLock() {
  let inFlight = false
  let token = 0
  return {
    get inFlight() {
      return inFlight
    },
    begin(afterTurn) {
      if (!shouldBeginSend(inFlight, afterTurn)) return null
      inFlight = true
      token += 1
      return token
    },
    end(claimedToken) {
      if (claimedToken === token) inFlight = false
    },
  }
}
