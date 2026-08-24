"""Task-instruction and session-handoff boilerplate must not become memories."""
import os
import sys
import json
import pathlib
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AURORACODER_DATA_DIR", tempfile.mkdtemp())
os.environ["AURORACODER_DOCKER"] = "0"

pathlib.Path(os.environ["AURORACODER_DATA_DIR"]).mkdir(parents=True, exist_ok=True)
pathlib.Path(os.environ["AURORACODER_DATA_DIR"], "settings.json").write_text(
    json.dumps({"other": {"memory": {"enabled": True}}}), encoding="utf-8"
)

from memory.store import MemoryRepository
from memory.ops import extractor
from memory.ops.prompts import EXTRACTION_SYSTEM_PROMPT

_TASK_BODY = (
    "Always respond in Chinese.\n"
    "Git conventions for AuroraCoder: set the commit author, include a "
    "Co-authored-by trailer, and never push commits to the remote.\n"
    "NEVER invoke real Docker.\n"
    "These instructions apply if relevant; ignore if irrelevant."
)

_TASK_WRAPPED = (
    f"[TASK INSTRUCTION]\n{_TASK_BODY}\n[/TASK INSTRUCTION]\n\n"
    "test ur memory module a bit and see how it works"
)

_HANDOFF = (
    "[Continued from previous agent session]\n\n"
    "Always respond in Chinese.\n"
    "Git conventions for AuroraCoder including author, trailer, and no-push "
    "rule. Never push commits to the remote.\n"
    "Docker invocations are prohibited in AuroraCoder. NEVER invoke real docker.\n"
    "Continue the previous work."
)

def _cand(plane, mtype, content, description):
    return {
        "plane": plane, "type": mtype, "scope": "project", "content": content,
        "description": description, "confidence": "high", "source": "discovered",
    }


_SCAFFOLD_CANDIDATES = [
    _cand("world", "communication", "Always respond in Chinese.",
          "Language preference for project AuroraCoder"),
    _cand("world", "convention", "Never push commits to the remote.",
          "Git conventions for AuroraCoder including author, trailer, and no-push rule"),
    _cand("world", "landmine", "Never invoke real Docker.",
          "Docker invocations are prohibited in AuroraCoder"),
    _cand("stance", "convention",
          "AuroraCoder git author must be set and commits must include a Co-authored-by trailer.",
          "Git author and trailer convention for AuroraCoder"),
]


def _msgs(user_content, extra_user=None):
    out = [
        {"role": "user", "content": user_content},
        {"role": "assistant", "content": "Looking into it."},
        {"role": "user", "content": extra_user or "go ahead"},
        {"role": "assistant", "content": "Done."},
    ]
    return out


def _isolated_repo(tmp_path):
    return MemoryRepository(tmp_path / "memory")


def test_transcript_does_not_treat_task_instruction_as_user_speech():
    text = extractor._transcript_to_text(_msgs(_TASK_WRAPPED))
    assert "test ur memory module" in text
    user_lines = [ln for ln in text.splitlines() if ln.startswith("USER:")]
    joined = "\n".join(user_lines)
    assert "Always respond in Chinese" not in joined
    assert "never push" not in joined.lower()
    assert "Co-authored-by" not in joined
    assert "NEVER invoke real Docker" not in joined
    assert "[TASK INSTRUCTION]" not in text


def test_transcript_does_not_treat_handoff_as_user_speech():
    text = extractor._transcript_to_text(_msgs(_HANDOFF, extra_user="if not, push"))
    user_lines = [ln for ln in text.splitlines() if ln.startswith("USER:")]
    joined = "\n".join(user_lines)
    assert "if not, push" in joined
    assert "Always respond in Chinese" not in joined
    assert "Never push commits" not in joined
    assert "USER: [Continued from previous agent session]" not in text


def test_task_instruction_only_chat_does_not_persist_scaffold_memories(tmp_path):
    repo = _isolated_repo(tmp_path)
    written = extractor.apply_extraction_plan(
        _SCAFFOLD_CANDIDATES, "d7691757", repo=repo, messages=_msgs(_TASK_WRAPPED),
    )
    assert written == [], written
    assert repo.all_items() == []


def test_handoff_rules_not_persisted_when_user_later_asks_to_push(tmp_path):
    repo = _isolated_repo(tmp_path)
    msgs = _msgs(_HANDOFF, extra_user="check if there is any uncommitted changes, if not, push")
    written = extractor.apply_extraction_plan(
        _SCAFFOLD_CANDIDATES, "e4845db6", repo=repo, messages=msgs,
    )
    assert written == [], written
    assert repo.all_items() == []


def test_genuine_user_preference_outside_scaffold_is_still_saved(tmp_path):
    repo = _isolated_repo(tmp_path)
    msgs = _msgs("Always run ruff before you say you're done.")
    cand = [{
        "plane": "stance", "type": "preference", "scope": "user",
        "content": "Run ruff before declaring a task done.",
        "description": "User preference: run ruff before finishing",
        "confidence": "high", "source": "discovered",
    }]
    written = extractor.apply_extraction_plan(cand, "conv-real", repo=repo, messages=msgs)
    assert len(written) == 1, written
    assert repo.get(written[0]["id"]).content.startswith("Run ruff")


def test_remember_nomination_of_task_instruction_is_dropped(tmp_path):
    """The agent still sees the live wrapped message and may call remember.
    Transcript stripping cannot catch that — the write-pass must."""
    repo = _isolated_repo(tmp_path)
    cand = [_cand("world", "communication", "Always respond in Chinese.",
                  "Language preference for project AuroraCoder")]
    cand[0]["source"] = "nominated"
    written = extractor.apply_extraction_plan(
        cand, "conv-nom", repo=repo, messages=_msgs(_TASK_WRAPPED),
    )
    assert written == [], written


def test_user_restatement_of_a_task_instruction_is_kept(tmp_path):
    repo = _isolated_repo(tmp_path)
    msgs = _msgs(_TASK_WRAPPED, extra_user="please always respond in Chinese for this project")
    cand = [{
        "plane": "world", "type": "communication", "scope": "project",
        "content": "Always respond in Chinese.",
        "description": "Language preference for project AuroraCoder",
        "confidence": "high", "source": "discovered",
    }]
    written = extractor.apply_extraction_plan(cand, "conv-restate", repo=repo, messages=msgs)
    assert len(written) == 1, written


def test_extraction_prompt_forbids_mining_session_scaffold():
    prompt = EXTRACTION_SYSTEM_PROMPT.lower()
    assert "task instruction" in prompt
    assert "continued from previous" in prompt or "handoff" in prompt
