#!/usr/bin/env python3
"""
Minimal stdio MCP server — a working example to test the ToolStore connection flow.

Provides two tools:
  - echo: repeats a message back
  - add: adds two numbers

Run directly:
    python echo_server.py
"""

import sys
import json
import time


def handle_request(request: dict) -> dict | None:
    """Process a single JSON-RPC request, return the response or None for notifications."""
    method = request.get("method", "")
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "echo-server", "version": "1.0.0"},
            },
        }

    if method == "notifications/initialized":
        return None  # no response for notifications

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Repeat a message back to you",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "message": {"type": "string", "description": "The message to echo back"}
                            },
                            "required": ["message"],
                        },
                    },
                    {
                        "name": "add",
                        "description": "Add two numbers together",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "a": {"type": "number", "description": "First number"},
                                "b": {"type": "number", "description": "Second number"},
                            },
                            "required": ["a", "b"],
                        },
                    },
                ]
            },
        }

    if method == "tools/call":
        params = request.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "echo":
            text = arguments.get("message", "")
            result_text = f"Echo: {text}"
        elif tool_name == "add":
            a = float(arguments.get("a", 0))
            b = float(arguments.get("b", 0))
            result_text = f"{a} + {b} = {a + b}"
        else:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}}

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": result_text}]
            },
        }

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}}


def main():
    # Minimal logging to stderr so it doesn't interfere with stdio protocol
    sys.stderr.write(f"[echo-server] started (pid={__import__('os').getpid()})\n")
    sys.stderr.flush()

    buffer = ""
    while True:
        try:
            line = sys.stdin.readline()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break

        buffer += line
        try:
            request = json.loads(buffer)
            buffer = ""
        except json.JSONDecodeError:
            continue  # partial read, wait for more

        response = handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()

    sys.stderr.write("[echo-server] shutting down\n")
    sys.stderr.flush()


if __name__ == "__main__":
    main()
