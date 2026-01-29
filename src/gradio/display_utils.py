import html

def format_chat_messages(messages: list) -> str:
    """
    Format the latest conversation turn from a list of messages into a displayable HTML string.
    
    This function replaces the previous two-step process (format -> prepare) by directly
    rendering the message content into HTML, handling:
    - Thinking/Reasoning blocks (with collapsible UI)
    - Tool Calls (styled)
    - Tool Responses (styled)
    - Code Interpreter output (special rendering)
    - Standard content
    """
    if not messages:
        return ""
    
    # Find the start of the current turn (messages after the last user message)
    start_idx = 0
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            start_idx = i + 1
            break
            
    html_parts = []
    
    for msg in messages[start_idx:]:
        role = msg.get("role")
        content = msg.get("content") or ""
        
        if role == "assistant":
            # 1. Handle Thinking
            thinking = msg.get("thinking") or msg.get("reasoning_content")
            if thinking:
                # Determine if thinking is "done" (collapsed) or "active" (open)
                # We assume thinking is done if there is content or tool calls following it in this message
                has_content = bool(content)
                has_tool_calls = bool(msg.get("tool_calls"))
                is_open = not (has_content or has_tool_calls)
                
                html_parts.append(_render_think_block(thinking, is_open))
                
            # 2. Handle Tool Calls
            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                func = tc.get("function", {})
                name = func.get("name", "")
                try:
                    args = func.get("arguments", "")
                except:
                    args = func.get("arguments", "")
                
                tool_call_text = f"Calling tool: {name}({args})"
                # Escape the tool call text before styling
                html_parts.append(_render_styled_block(html.escape(tool_call_text), "darkred"))
            
            # 3. Handle Content
            if content:
                processed_content = _process_content(content)
                html_parts.append(processed_content)
                
        elif role == "tool":
            # 4. Handle Tool Responses
            processed_content = _process_content(content)
            html_parts.append(_render_styled_block(processed_content, "darkgreen"))
            
    return "\n\n".join(html_parts)

def _process_content(text: str) -> str:
    """Escape HTML and format special blocks like Code Interpreter."""
    escaped = html.escape(text)
    
    # Code Interpreter Markers
    code_start = html.escape("<====CODE_INTERPRETER_START====>")
    code_end = html.escape("<====CODE_INTERPRETER_END====>")
    
    if code_start in escaped:
        code_block_start = '<div class="code-interpreter-display" style="border: 1px solid #e0e0e0; border-radius: 6px; background: #fafafa; margin: 10px 0; overflow: hidden;"><div style="background: #f0f0f0; padding: 5px 10px; font-size: 0.8em; color: #666; border-bottom: 1px solid #e0e0e0;">Code Interpreter Output</div><pre style="margin: 0; padding: 10px; overflow-x: auto; font-family: Consolas, monospace;">'
        code_block_end = '</pre></div>'
        escaped = escaped.replace(code_start, code_block_start).replace(code_end, code_block_end)
        
    return escaped

def _render_styled_block(html_text: str, color: str) -> str:
    """Render a pre-formatted block with a specific color. Input text should already be HTML escaped."""
    return f'<pre style="color: {color}; white-space: pre-wrap; margin: 0.5em 0;">{html_text}</pre>'

def _render_think_block(thinking_content: str, is_open: bool) -> str:
    """Render the collapsible thinking block."""
    escaped_content = html.escape(thinking_content)
    
    open_attr = " open" if is_open else ""
    status_text = "(toggle - unclosed)" if is_open else "(toggle)"
    
    # Italicize content
    formatted_content = f"<i>{escaped_content}</i>"
    
    return (
        f'<details{open_attr} class="think-block-details" style="margin-top: 0.5em; margin-bottom: 0.5em; border: 1px solid #eee; border-radius: 4px; background-color: #f9f9f9;">'
        f'<summary class="think-block-summary" style="cursor: pointer; padding: 0.3em 0.6em; font-weight: bold; color: #555; background-color: #efefef; border-bottom: 1px solid #eee; border-top-left-radius: 4px; border-top-right-radius: 4px;">'
        f'<i>&lt;think&gt; {status_text}</i>'
        f'</summary>'
        f'<div class="think-block-content" style="padding: 0.5em 0.8em; white-space: pre-wrap;">'
        f'{formatted_content}'
        f'</div>'
        f'</details>'
    )

if __name__ == '__main__':
    # Simple test
    test_msgs = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "thinking": "Thinking...", "content": "Hello!"},
        {"role": "assistant", "tool_calls": [{"function": {"name": "test", "arguments": "{}"}}]},
        {"role": "tool", "content": "Result"}
    ]
    print(format_chat_messages(test_msgs))
