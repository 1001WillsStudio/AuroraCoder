"""Training data logging — append request→response pairs to daily JSONL files.

Extracted from main_flow.py to keep the agent loop lean and to remove
config/file-system imports from the hot path.
"""

import json
import datetime

from .config import DATA_DIR, TRAINING_DATA_DIR


def record_api_call(request_messages: list, response_message: dict, *, enabled: bool = True):
    """Append one request→response pair to today's training log.

    Args:
        request_messages: The full message list sent to the LLM.
        response_message: The assistant's response dict.
        enabled: If ``False`` the call returns immediately (the user has
            disabled training-data logging via the Settings panel).
    """
    if not enabled:
        return
    try:
        TRAINING_DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = TRAINING_DATA_DIR / f"{datetime.datetime.now():%Y-%m-%d}.jsonl"
        entry = {"request": request_messages, "response": response_message}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def load_save_training_flag() -> bool:
    """Read ``other.agent.save_training_data`` from settings.json *once*.

    Returns ``False`` only when the user has explicitly disabled training
    logging.  Defaults to ``True`` (the historical behaviour).
    """
    try:
        settings_path = DATA_DIR / "settings.json"
        if settings_path.exists():
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            return settings.get("other", {}).get("agent", {}).get("save_training_data", True)
    except Exception:
        pass
    return True
