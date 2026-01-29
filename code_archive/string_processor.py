import random
import traceback

from .core_tools.web_browser import jina_ai_reader
from .core_tools.google_search import search_for_llm
# from .code_tools.semantic_codebase_search import semantic_search_codebase
from .code_tools import (
    read_file_tool,
    partial_edit_file_tool,
    full_file_write_tool,
    delete_file_tool,
    list_dir_tool,
    file_search_tool,
    grep_search_tool,
    run_terminal_cmd_tool,
)
from .config import VALID_TAGS
from .prompt import TOOL_INFO_TEMPLATE, CODE_TOOLS_INFO
from .core_tools.code_interpreter import run_like_jupyter
from .code_tools.code_interpreter import code_interpreter
from .code_sandbox.session_manager import session_manager


def find_last_valid_tag(s: str):
    """
    Return the starting index and the last tag in `s` that matches one of the
    predefined valid tags. If no matching tag is found, return (-1, None).
    """
    # Predefined valid tags
    potential_tags = VALID_TAGS
    max_tag_len = max(len(tag) for tag in potential_tags) if potential_tags else 0

    pos = len(s)
    while True:
        end = s.rfind('>', 0, pos)
        if end == -1:
            return -1, None

        start = s.rfind('<', max(0, end - max_tag_len), end)
        if start == -1:
            pos = end - 1
            if pos < 0:
                return -1, None
            continue

        tag = s[start:end + 1]
        if tag in potential_tags:
            return start, tag

        pos = start


def show_tool_info(tool_query: str):
    """
    Provides information about available JSON tools
    defined in TOOL_INFO_TEMPLATE from prompt.py.

    If tool_query is empty, lists all tools from TOOL_INFO_TEMPLATE along with general instructions.
    If tool_query specifies a tool name, shows details for that specific tool.
    """
    normalized_query = tool_query.strip().lower()

    if not normalized_query:  # General request
        output_parts = [TOOL_INFO_TEMPLATE.get("_general_instructions", "No general instructions available.")]
        output_parts.append("\nAvailable JSON tools (use <tool_box>tool_name</tool_box> for details):")
        for tool_name in TOOL_INFO_TEMPLATE.keys():
            if tool_name != "_general_instructions":
                output_parts.append(f"- {tool_name}")
        return "\n".join(output_parts)

    if normalized_query in TOOL_INFO_TEMPLATE:
        return TOOL_INFO_TEMPLATE[normalized_query]
    else:
        available_tool_names = [name for name in TOOL_INFO_TEMPLATE.keys() if name != "_general_instructions"]
        return (
            f"JSON tool '{normalized_query}' not found. \n"
            f"Available JSON tools: {', '.join(available_tool_names)}. \n"
            f"Use <tool_box>tool_name</tool_box> for specific details, or <tool_box></tool_box> for an overview."
        )


def show_code_tools_info(_unused_parameter):
    """
    Display information about the code tools set.
    """
    return CODE_TOOLS_INFO


def run_tool_call(tool_call: str):
    """
    Placeholder function for running tool calls.
    """
    return "Tool call functionality is not available."


def parse_semantic_search_params(content: str) -> dict:
    """Parse semantic codebase search parameters from content."""
    pass
    # lines = content.strip().split('\n')
    # params = {'query': '', 'target_directories': None, 'max_results': 10}

    # for line in lines:
    #     line = line.strip()
    #     if line.startswith('query:'):
    #         params['query'] = line[6:].strip()
    #     elif line.startswith('target_directories:'):
    #         dirs_str = line[18:].strip()
    #         if dirs_str and dirs_str != 'None':
    #             params['target_directories'] = [d.strip() for d in dirs_str.split(',')]
    #     elif line.startswith('max_results:'):
    #         try:
    #             params['max_results'] = int(line[12:].strip())
    #         except ValueError:
    #             pass

    # # If no structured format, treat entire content as query
    # if not params['query'] and content.strip():
    #     params['query'] = content.strip()

    # return params


def parse_file_op_params(content: str) -> dict:
    """Parse file operation parameters from content."""
    params = {}

    code_edit_marker = 'code_edit:'
    marker_pos = content.find(code_edit_marker)

    if marker_pos != -1:
        # Everything before the marker is other params
        other_params_str = content[:marker_pos]
        # Everything after the marker is code
        code_str = content[marker_pos + len(code_edit_marker):]

        # A single leading newline is often present after the marker.
        # We remove it, but preserve all other whitespace to maintain indentation.
        if code_str.startswith('\n'):
            code_str = code_str[1:]
        params['code_edit'] = code_str

        lines = other_params_str.strip().split('\n')
        for line in lines:
            if ':' in line:
                key, value = line.split(':', 1)
                params[key.strip()] = value.strip()
    else:
        # No code_edit block, parse everything as key-value
        lines = content.strip().split('\n')
        for line in lines:
            if ':' in line:
                key, value = line.split(':', 1)
                params[key.strip()] = value.strip()

    return params


def file_op_dispatcher(params: dict):
    """Dispatcher for file operations."""
    op = params.pop('operation', None)
    if not op:
        return "Error: 'operation' not specified for file_op"

    op = op.strip().lower()

    try:
        if op == 'read':
            return read_file_tool(**params)
        elif op == 'full_file_write':
            return full_file_write_tool(**params)
        elif op == 'partial_edit':
            # Only pass expected arguments to avoid TypeError from extra params
            allowed_keys = {k: v for k, v in params.items() if k in {'target_file', 'code_edit'}}
            return partial_edit_file_tool(**allowed_keys)
        elif op == 'delete':
            return delete_file_tool(**params)
        elif op == 'list':
            return list_dir_tool(**params)
        elif op == 'search':
            return file_search_tool(**params)
        else:
            return f"Error: Unknown file operation '{op}'"
    except Exception as e:
        return f"Error during file operation '{op}': {str(e)}"


def handle_file_op(content: str) -> str:
    """
    Handles file operations.
    """
    params = parse_file_op_params(content)
    return file_op_dispatcher(params)


def parse_grep_search_params(content: str) -> dict:
    """Parse grep search parameters from content."""
    lines = content.strip().split('\n')
    params = {'query': '', 'include_pattern': None, 'exclude_pattern': None, 'case_sensitive': True}

    for line in lines:
        line = line.strip()
        if line.startswith('query:'):
            params['query'] = line[6:].strip()
        elif line.startswith('include_pattern:'):
            params['include_pattern'] = line[16:].strip()
        elif line.startswith('exclude_pattern:'):
            params['exclude_pattern'] = line[16:].strip()
        elif line.startswith('case_sensitive:'):
            params['case_sensitive'] = line[15:].strip().lower() == 'true'

    # If no structured format, treat entire content as query
    if not params['query'] and content.strip():
        params['query'] = content.strip()

    return params


def parse_terminal_cmd_params(content: str) -> dict:
    """Parse terminal command parameters from content."""
    lines = content.strip().split('\n')
    params = {'command': ''}

    for line in lines:
        line = line.strip()
        if line.startswith('command:'):
            params['command'] = line[8:].strip()

    # If no structured format, treat entire content as command
    if not params['command'] and content.strip():
        params['command'] = content.strip()

    return params


def process_string(input_string):
    # Find the last valid tag
    tag_start, last_tag = find_last_valid_tag(input_string)
    if tag_start == -1:
        return input_string

    # Extract content after the tag
    tag_end = tag_start + len(last_tag) - 1
    content_after = input_string[tag_end + 1:]

    # Define tag handlers
    tag_handlers = {
        '<python>': {
            'execute': run_like_jupyter,
            'closing_tag': '</python>',
            'result_tag': 'python_response',
        },
        '<search>': {
            'execute': search_for_llm,
            'closing_tag': '</search>',
            'result_tag': 'search_response',
        },
        '<browser>': {
            'execute': jina_ai_reader,
            'closing_tag': '</browser>',
            'result_tag': 'browser_response',
        },
        '<tool_box>': {
            'execute': show_tool_info,
            'closing_tag': '</tool_box>',
            'result_tag': 'tool_help_response',
        },
        '<code_tools>': {
            'execute': show_code_tools_info,
            'closing_tag': '</code_tools>',
            'result_tag': 'code_tools_response',
        },
        '<file_op>': {
            'execute': handle_file_op,
            'closing_tag': '</file_op>',
            'result_tag': 'file_op_response',
        },
        '<grep_search>': {
            'execute': lambda content: grep_search_tool(**parse_grep_search_params(content)),
            'closing_tag': '</grep_search>',
            'result_tag': 'grep_search_response',
        },
        '<run_terminal_cmd>': {
            'execute': lambda content: run_terminal_cmd_tool(**parse_terminal_cmd_params(content)),
            'closing_tag': '</run_terminal_cmd>',
            'result_tag': 'terminal_response',
        },
    }

    # Get the handler for the last tag
    handler = tag_handlers.get(last_tag)
    if handler:
        # Remove last </think> if it exists before the tag
        last_think_tag_index = input_string.rfind('</think>', 0, tag_start)
        if last_think_tag_index != -1:
            input_string = input_string[:last_think_tag_index] + input_string[last_think_tag_index + len('</think>'):]

        try:
            execution_result = handler['execute'](content_after)
        except Exception:
            execution_result = traceback.format_exc()

        tool_response = execution_result
        interpreter_display = ""

        if last_tag == '<file_op>':
            params = parse_file_op_params(content_after)
            operation = params.get('operation', '').strip().lower()
            target_file = params.get('target_file')

            display_trigger_operations = ['full_file_write', 'partial_edit', 'replace_lines', 'read']

            if operation in display_trigger_operations and target_file:
                try:
                    root_path = session_manager.get_session_working_directory()
                    code_interpreter.set_root_path(root_path)
                    code_interpreter.set_relative_file(target_file)
                    interpreter_display = code_interpreter.display_code()
                except Exception as e:
                    interpreter_display = f"\n\nError displaying code interpreter: {e}"

        candidate_guiding_word = ["I", "Now", "The"]
        # Construct the result
        selected_guiding_word = random.choice(candidate_guiding_word)

        response_parts = [
            f"{input_string}{handler['closing_tag']}",
            f"## Tool Response\n<{handler['result_tag']}>\n{tool_response.strip()}\n</{handler['result_tag']}>"
        ]

        if interpreter_display:
            response_parts.append(interpreter_display)

        response_parts.append(f"\n{selected_guiding_word}")

        return "\n\n".join(response_parts)

    return input_string
