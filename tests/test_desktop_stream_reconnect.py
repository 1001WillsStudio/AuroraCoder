import json
import subprocess
from pathlib import Path


def test_desktop_reattaches_accepted_turn_without_posting_again():
    api = Path(__file__).resolve().parents[1] / "frontend" / "src" / "services" / "api.js"
    script = """
import assert from 'node:assert/strict'
import fs from 'node:fs'
const source = fs.readFileSync(__MODULE__, 'utf-8')
  .replace("import { getAuthHeader, clearToken } from '../utils/auth.js';",
    "const getAuthHeader = () => null; const clearToken = () => {}")
const { streamChat, resumeStream } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))
const event = (type, data) => `event: ${type}\\ndata: ${JSON.stringify(data)}\\n\\n`
const requests = [], results = [], errors = [], acceptedIds = []
const callbacks = {
  onConversationId: id => acceptedIds.push(id),
  onDone: data => results.push(data),
  onError: data => errors.push(data),
}
globalThis.fetch = async (url, options) => {
  requests.push([options.method || 'GET', url])
  if (requests.length === 1) {
    return new Response(event('delta', {delta: {content: 'preview'}, seq: 1}), {
      headers: {'X-Conversation-ID': 'accepted-conversation'}
    })
  }
  return new Response(event('done', {conversation_id: 'accepted-conversation', status: 'completed'}))
}
await streamChat('Resume this turn only once', null, callbacks)
assert.deepEqual(requests, [
  ['POST', '/api/chat'], ['GET', '/api/conversations/accepted-conversation/stream']
])
assert.deepEqual(acceptedIds, ['accepted-conversation'])
assert.equal(results.length, 1)
assert.equal(errors.length, 0)

requests.length = 0
globalThis.fetch = async (url, options) => {
  requests.push([options.method || 'GET', url])
  return new Response(event('error', {message: 'Provider refused'}), {
    headers: {'X-Conversation-ID': 'accepted-conversation'}
  })
}
await streamChat('A terminal error must stay terminal', null, callbacks)
assert.equal(requests.length, 1)
assert.equal(errors.at(-1).message, 'Provider refused')

requests.length = 0
globalThis.fetch = async url => {
  requests.push(url)
  return new Response(': keepalive\\n\\n')
}
await assert.rejects(resumeStream('accepted-conversation', callbacks), {name: 'IncompleteStreamError'})
assert.equal(requests.length, 2)
assert.equal(errors.length, 2)
assert.equal(errors.at(-1).type, 'IncompleteStreamError')

const abort = new AbortController()
requests.length = 0
globalThis.fetch = async url => {
  requests.push(url)
  abort.abort()
  throw new DOMException('Stopped', 'AbortError')
}
await assert.rejects(resumeStream('accepted-conversation', callbacks, abort.signal), {name: 'AbortError'})
assert.equal(requests.length, 1)
assert.equal(errors.length, 2)
""".replace("__MODULE__", json.dumps(str(api)))
    subprocess.run(["node", "--input-type=module", "-e", script], check=True, timeout=10, capture_output=True)
