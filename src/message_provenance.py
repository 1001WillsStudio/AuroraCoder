HANDOFF_PREFIX = "[Continued from previous agent session]"
TASK_RESUME_SOURCE = "task_resume"
TURN_INPUT_RECEIPT = "turn_input_receipt"


def is_synthetic_handoff(content: str, source: str | None = None) -> bool:
    return source == TASK_RESUME_SOURCE or bool(content and content.lstrip().startswith(HANDOFF_PREFIX))
