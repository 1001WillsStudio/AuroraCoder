"""
Range-replace edit tool (edit_file).

Extracted from file_operations.py to keep edit logic in a dedicated module.
The `FileOperations.range_replace_edit` method in file_operations.py still
serves as the main implementation; this module provides helpers that are
shared between the class method and its tool-wrapper.
"""


def adjust_indent(text: str, delta: int) -> str:
    """Adjust indentation of each non-blank line by *delta* spaces.

    Positive delta adds leading spaces; negative delta removes up to
    abs(delta) leading spaces.  Blank lines are left unchanged.
    """
    if delta == 0 or not text:
        return text
    lines = text.splitlines(keepends=True)
    adjusted = []
    for line in lines:
        stripped_content = line.lstrip(' ')
        if not stripped_content.strip('\n\r'):
            # blank or whitespace-only — leave alone
            adjusted.append(line)
        elif delta > 0:
            adjusted.append(' ' * delta + line)
        else:
            removed = len(line) - len(stripped_content)
            to_remove = min(abs(delta), removed)
            adjusted.append(line[to_remove:])
    return ''.join(adjusted)
