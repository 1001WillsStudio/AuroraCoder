import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_queued_steering_is_sent_once_after_a_safe_terminal_snapshot():
    frontend = ROOT / "frontend" / "src"
    script = """
import fs from 'node:fs'
const source = fs.readFileSync(__CALLBACKS__, 'utf-8')
  .replace("'../constants'", __CONSTANTS__)
  .replace("'../utils/streamUtils'", __UTILS__)
const { createStreamCallbacks } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))
const pendingInterruptRef = { current: { message: 'Use the previous branch', attachedFiles: ['scope.md'] } }
const called = []
const navigations = []
const restored = []
const unfinished = [
  { role: 'assistant', tool_calls: [{id: 'tool-1'}] },
  { role: 'tool', tool_call_id: 'tool-1', content: 'done' }
]
const raw = [...unfinished, { role: 'assistant', content: 'final' }]
const noop = () => {}
const callbacks = createStreamCallbacks({
  setMessages: noop, setRawMessages: noop, setConversationId: noop, setCanContinue: noop,
  setIsStreaming: noop, setHistoryRefreshTrigger: noop, setSubagentChildIds: noop,
  handleSend: (...args) => called.push(args), withInterrupt: true, pendingInterruptRef,
  abortControllerRef: { current: { abort: () => called.push('abort') } },
  continuationNavigatedRef: {current: new Set()},
  handleLoadConversation: (...args) => navigations.push(args),
  onStreamEnd: () => restored.push(pendingInterruptRef.current),
})
callbacks.onMessages([], 'running', {conversation_id: 'cid', raw_messages: unfinished})
callbacks.onDone({status: 'completed', conversation_id: 'cid', raw_messages: raw})
callbacks.onDone({status: 'completed', conversation_id: 'cid', raw_messages: raw})
await new Promise(resolve => setTimeout(resolve, 80))
const pending = pendingInterruptRef.current
pendingInterruptRef.current = { message: 'Take this draft back', attachedFiles: [] }
callbacks.onDone({status: 'completed', conversation_id: 'cid', raw_messages: raw})
pendingInterruptRef.current = null
await new Promise(resolve => setTimeout(resolve, 80))
const transferred = { message: 'Check the newly clarified path', attachedFiles: ['scope.md'] }
pendingInterruptRef.current = transferred
callbacks.onMessages([], 'running', {conversation_id: 'cid', raw_messages: unfinished})
const continuation = {status: 'continued', conversation_id: 'cid', new_conversation_id: 'next', raw_messages: raw}
callbacks.onDone(continuation)
callbacks.onMessages([], 'continued', continuation)
await new Promise(resolve => setTimeout(resolve, 550))
console.log(JSON.stringify({called, pending, navigations, restored, carried: pendingInterruptRef.current}))
""".replace(
        "__CALLBACKS__", json.dumps(str(frontend / "hooks" / "createStreamCallbacks.js")),
    ).replace(
        "__CONSTANTS__", json.dumps(f"'{(frontend / 'constants.js').as_uri()}'"),
    ).replace(
        "__UTILS__", json.dumps(f"'{(frontend / 'utils' / 'streamUtils.js').as_uri()}'"),
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT, text=True, capture_output=True, check=True, timeout=10,
    )
    output = json.loads(result.stdout)
    assert output["pending"] is None
    assert len(output["called"]) == 1
    raw, message, options = output["called"][0]
    assert raw[-1]["content"] == "final"
    assert message == "Use the previous branch"
    assert options == {"attachedFiles": ["scope.md"], "interruptStream": False}
    assert output["navigations"] == [["next", {"carryPendingSteering": True}]]
    assert output["restored"] == []
    assert output["carried"] == {"message": "Check the newly clarified path", "attachedFiles": ["scope.md"]}
