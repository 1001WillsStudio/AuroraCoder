import json
import subprocess
from pathlib import Path


def test_desktop_reattaches_accepted_turn_without_posting_again():
    api = Path(__file__).resolve().parents[1] / "frontend" / "src" / "services" / "api.js"
    script = """
import assert from 'node:assert/strict'
const { streamChat, resumeStream } = await import(__MODULE__)
const event = (type, data) => `event: ${type}\\ndata: ${JSON.stringify(data)}\\n\\n`
const requests = [], results = [], errors = [], acceptedIds = []
let turnId
const callbacks = {
  onConversationId: id => acceptedIds.push(id),
  onDone: data => results.push(data),
  onError: data => errors.push(data),
}
globalThis.fetch = async (url, options) => {
  requests.push([options.method || 'GET', url])
  if (requests.length === 1) {
    turnId = options.headers['X-Aurora-Turn-ID']
    return new Response(event('delta', {delta: {content: 'preview'}, seq: 1}), {
      headers: {'X-Conversation-ID': 'accepted-conversation'}
    })
  }
  return new Response(event('done', {conversation_id: 'accepted-conversation', status: 'completed'}))
}
await streamChat('Resume this turn only once', null, callbacks)
assert.deepEqual(requests, [
  ['POST', '/api/chat'], ['GET', `/api/conversations/accepted-conversation/stream?turn_id=${turnId}`]
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
""".replace("__MODULE__", json.dumps(api.resolve().as_uri()))
    subprocess.run(["node", "--input-type=module", "-e", script], check=True, timeout=10, capture_output=True)


def test_desktop_recovers_when_post_response_headers_are_lost():
    api = Path(__file__).resolve().parents[1] / "frontend" / "src" / "services" / "api.js"
    script = """
import assert from 'node:assert/strict'
const { streamChat } = await import(__MODULE__)
for (const outcome of ['network', 'timeout', 'other-turn']) {
  const requests = [], ids = [], results = [], errors = []
  let cid, receipt
  const expected = new TypeError('Response headers lost')
  globalThis.fetch = async (url, options) => {
    requests.push(options.method || 'GET')
    if (requests.length === 1) {
      cid = JSON.parse(options.body).conversation_id
      receipt = options.headers['X-Aurora-Turn-ID']
      assert.match(cid, /^[a-f0-9-]{36}$/)
      assert.match(receipt, /^[a-f0-9-]{36}$/)
      if (outcome === 'timeout') return new Response('', {status: 504})
      throw expected
    }
    assert.equal(url, `/api/conversations/${cid}/stream?turn_id=${receipt}`)
    if (outcome === 'other-turn') return new Response('', {status: 409})
    const payload = JSON.stringify({conversation_id: cid, status: 'completed'})
    return new Response(`event: done\\ndata: ${payload}\\n\\n`, {headers: {'X-Conversation-ID': cid}})
  }
  const pending = streamChat('Submit once', null, {
    onConversationId: id => ids.push(id),
    onDone: data => results.push(data),
    onError: data => errors.push(data),
  })
  if (outcome === 'other-turn') {
    await assert.rejects(pending, expected)
    assert.deepEqual(ids, [])
    assert.deepEqual(results, [])
    assert.equal(errors.length, 1)
  } else {
    await pending
    assert.deepEqual(ids, [cid])
    assert.equal(results.length, 1)
    assert.deepEqual(errors, [])
  }
  assert.deepEqual(requests, ['POST', 'GET'])
}
""".replace("__MODULE__", json.dumps(api.resolve().as_uri()))
    subprocess.run(["node", "--input-type=module", "-e", script], check=True, timeout=10, capture_output=True)
