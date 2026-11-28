"""Provider views and readback for tool evidence already owned by the transcript."""

import hashlib
import json
import re

from .config import DATA_DIR

TERMINAL_EVIDENCE = "terminal_output_evidence"
CONTEXT_CUT = "context_tool_result_cut"
READ_RESULT = "read_original_tool_result"
PAGE_CHARS = 6000
COMPACTABLE = {"run_terminal_command", "list_directory", "read_file", "grep_search", "search_files"}

READ_RESULT_SCHEMA = {
    "type": "function",
    "function": {
        "name": READ_RESULT,
        "description": "Read a page of omitted tool output from this chat, without rerunning the tool.",
        "parameters": {
            "type": "object",
            "properties": {
                "tool_call_id": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
            },
            "required": ["tool_call_id"],
        },
    },
}

# Each preset keeps the latest complete tool batches unchanged.
PRESETS = {
    "conservative": (32, 8, 0.90, None, 0.80),
    "balanced": (24, 8, 0.80, 0.192, 0.70),
    "compact": (16, 12, 0.70, 0.04, 0.55),
}


def load_mode() -> str:
    try:
        settings = json.loads((DATA_DIR / "settings.json").read_text(encoding="utf-8"))
        mode = settings["other"]["agent"]["context_compaction"]
    except (KeyError, TypeError, OSError, ValueError):
        return "balanced"
    return mode if isinstance(mode, str) and mode in {"off", *PRESETS} else "balanced"


_MIN_RESULT_CHARS = 4_000
# A newline-free run this long is generated data (a minified bundle, a base64
# blob, single-line JSON) rather than text somebody reads line by line.  The
# line planner has no boundary to cut at inside it, so it gets a fallback.
_UNBROKEN_MIN_CHARS = 16_000
# The fallback must still free most of a page of the output reader (6,000
# characters) before it is worth a marker, and it never omits a diagnostic token.
_UNBROKEN_MIN_CUT = _MIN_RESULT_CHARS
# How far a protected diagnostic token may reach past its match, so an omitted
# boundary never lands inside a path or an identifier.  Bounded on purpose: one
# huge opaque token must stay cuttable, otherwise a bundle that happens to
# contain the word "error" could never be compacted again.
_EVIDENCE_TOKEN_CHARS = 256
_EVIDENCE = re.compile(
    r"\b(?:error|fail(?:ed|ure)?|fatal|panic|traceback|exception|assert\w*|passed|"
    r"exit\s*(?:code|status)|return\s*code|job\s*id|pid|(?:output|status|log)\s*(?:file|path))\b"
    r"|/(?:tmp|var/tmp|dev/shm|workspace)/|[A-Z]:[\\/]",
    re.IGNORECASE,
)

def _plan_cut(body: str, retention: float) -> tuple[int, int] | None:
    """Omit one middle run while keeping diagnostic and reference text verbatim.

    Character offsets are indexes into ``body`` itself, so the omission marker,
    the stored cut and ``read_original`` all agree on the same Unicode text.
    """
    cut = _plan_line_cut(body, retention)
    if cut is None:
        cut = _plan_unbroken_cut(body, retention)
    return cut


def _plan_line_cut(body: str, retention: float) -> tuple[int, int] | None:
    """Omit one middle run of whole lines that hold no evidence."""
    edge = int(len(body) * retention / 2)
    first_break = body.find("\n", edge)
    end = body.rfind("\n", 0, len(body) - edge) + 1
    if first_break < 0 or first_break + 1 >= end:
        return None

    start = pos = first_break + 1
    best = (0, 0)
    for line in body[start:end].splitlines(keepends=True):
        if _EVIDENCE.search(line):
            if pos - start > best[1] - best[0]:
                best = (start, pos)
            start = pos + len(line)
        pos += len(line)
    if pos - start > best[1] - best[0]:
        best = (start, pos)
    return best if best[1] - best[0] >= 512 else None


def _plan_unbroken_cut(body: str, retention: float) -> tuple[int, int] | None:
    """Cut inside a machine-generated run that has no line break to cut at.

    Without this, a result that is one enormous line (minified output, a long
    base64 blob) can never be compacted: the line planner needs a newline inside
    the cuttable middle, so it reports "no candidate" and the whole result stays
    in every provider request.

    The same head/tail share is kept, and diagnostics stay safe: the token that
    carries an evidence match is left verbatim and only the largest gap between
    protected tokens is omitted.  A run whose gaps are all short is a log rather
    than unbroken generated data, so nothing is cut.
    """
    edge = int(len(body) * retention / 2)
    start, end = edge, len(body) - edge
    if end - start < _UNBROKEN_MIN_CUT:
        return None
    run_start = body.rfind("\n", 0, start) + 1
    run_end = body.find("\n", end)
    if run_end < 0:
        run_end = len(body)
    if run_end - run_start < _UNBROKEN_MIN_CHARS:
        return None

    best = (0, 0)
    bound = start
    for match in _EVIDENCE.finditer(body, run_start, run_end):
        if match.start() >= end:
            break
        protected_start, protected_stop = _protected_span(body, match)
        if min(protected_start, end) - bound > best[1] - best[0]:
            best = (bound, min(protected_start, end))
        bound = max(bound, min(protected_stop, end))
        if bound >= end:
            break
    if end - bound > best[1] - best[0]:
        best = (bound, end)
    return best if best[1] - best[0] >= _UNBROKEN_MIN_CUT else None


def _protected_span(body: str, match: re.Match) -> tuple[int, int]:
    """The evidence match plus the whitespace-delimited token that carries it."""
    start, stop = match.span()
    low = max(0, start - _EVIDENCE_TOKEN_CHARS)
    high = min(len(body), stop + _EVIDENCE_TOKEN_CHARS)
    while start > low and not body[start - 1].isspace():
        start -= 1
    while stop < high and not body[stop].isspace():
        stop += 1
    return start, stop



class TerminalResultText(str):
    """A normal display result carrying the captured stdout alongside it."""

    def __new__(cls, preview: str, evidence: str):
        result = super().__new__(cls, preview)
        result.evidence = evidence
        return result


def tool_message(call_id: str, result: str) -> dict:
    message = {"role": "tool", "tool_call_id": call_id, "content": str(result)}
    if isinstance(result, TerminalResultText):
        message[TERMINAL_EVIDENCE] = result.evidence
    return message


def read_result(messages: list[dict], arguments: dict) -> str:
    call_id = arguments.get("tool_call_id")
    offset = arguments.get("offset", 0)
    if type(offset) is not int or offset < 0:
        return "Error: offset must be a non-negative integer."
    if not isinstance(call_id, str) or not call_id:
        return "Error: tool_call_id must be a non-empty string."
    for message in reversed(messages):
        if message.get("role") != "tool" or message.get("tool_call_id") != call_id:
            continue
        original = message.get(TERMINAL_EVIDENCE)
        if not isinstance(original, str) and CONTEXT_CUT in message:
            original = message.get("content")
        if not isinstance(original, str):
            break
        end = min(offset + PAGE_CHARS, len(original))
        if offset >= end:
            return "End of tool result."
        page = f"Original result ({offset}–{end} of {len(original)} characters):\n{original[offset:end]}"
        if end < len(original):
            page += f"\n\nNext offset: {end}"
        return page
    return "Error: no saved omitted output for that tool call in this conversation."


def has_evidence(messages: list[dict]) -> bool:
    return any(
        item.get("role") == "tool" and (TERMINAL_EVIDENCE in item or CONTEXT_CUT in item)
        for item in messages
    )


def _complete_tool_batches(messages: list[dict]) -> list[list[tuple[int, str]]]:
    batches = []
    for index, message in enumerate(messages):
        calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        if not calls:
            continue
        results = messages[index + 1:index + 1 + len(calls)]
        if len(calls) == len(results) and all(
            result.get("role") == "tool" and result.get("tool_call_id") == call.get("id")
            for call, result in zip(calls, results)
        ):
            batches.append([
                (index + offset, call.get("function", {}).get("name", ""))
                for offset, call in enumerate(calls, 1)
            ])
    return batches


def estimated_tokens(provider_messages: list[dict]) -> int:
    return (len(json.dumps(provider_messages, ensure_ascii=False).encode("utf-8", errors="replace")) + 2) // 3


def compact_provider_view(
    messages: list[dict], provider_messages: list[dict], mode: str,
    context_window: int, output_reserve: int, *, known_window: bool,
) -> None:
    """Plan omissions on original result records and apply them only to the wire copy.

    The gateway persists result metadata with the transcript and copies it when
    forking. The core has no archive database, and original result text is retained.
    """
    preset = PRESETS.get(mode)
    if preset is None:
        return
    recent, batch_size, pressure, window_fraction, retain = preset
    batches = _complete_tool_batches(messages)
    pending = []
    for batch in batches:
        untouched = []
        for index, tool_name in batch:
            record = messages[index]
            content = record.get("content")
            if tool_name not in COMPACTABLE or not isinstance(content, str) or TERMINAL_EVIDENCE in record:
                continue
            cut = record.get(CONTEXT_CUT)
            if isinstance(cut, list) and len(cut) == 3:
                begin, end, digest = cut
                if (
                    type(begin) is int and type(end) is int and 0 < begin < end < len(content)
                    and digest == _content_digest(content)
                ):
                    _omit(provider_messages[index], content, begin, end)
                    continue
            if len(content) >= _MIN_RESULT_CHARS:
                untouched.append(index)
        pending.append(untouched)

    input_budget = max(1, context_window - output_reserve)
    trigger = input_budget * pressure
    if known_window and window_fraction is not None:
        trigger = min(trigger, context_window * window_fraction)
    if estimated_tokens(provider_messages) < trigger:
        return
    for batch in [item for item in pending[:-recent] if item][:batch_size]:
        for index in batch:
            record = messages[index]
            content = record["content"]
            cut = _plan_cut(content, retain)
            if cut is None:
                continue
            start, end = cut
            record[CONTEXT_CUT] = [start, end, _content_digest(content)]
            _omit(provider_messages[index], content, start, end)


def _content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def _omit(wire: dict, content: str, start: int, end: int) -> None:
    call_id = wire["tool_call_id"]
    wire["content"] = (
        f"{content[:start]}\n[Omitted {end - start} characters. Read tool result "
        f"{call_id} from offset {start} if needed.]\n{content[end:]}"
    )
