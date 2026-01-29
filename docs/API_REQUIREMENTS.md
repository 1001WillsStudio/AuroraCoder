# ThinkWithTool - Web API Requirements

## Overview

This document specifies the backend API requirements for the ThinkWithTool AI Assistant web interface. The API provides a modern REST interface with Server-Sent Events (SSE) for real-time streaming of AI responses.

---

## Base URL

```
Development: http://localhost:8080
Production: https://your-domain.com/api
```

---

## Authentication

> **Note**: Current MVP version does not include authentication. Future versions will support:
> - JWT Bearer tokens
> - API key authentication
> - OAuth 2.0 integration

---

## Endpoints

### 1. Health Check

#### `GET /`
Basic health check.

**Response:**
```json
{
    "status": "ok",
    "service": "ThinkWithTool AI Assistant",
    "version": "1.0.0"
}
```

#### `GET /api/health`
Detailed health check including session status.

**Response:**
```json
{
    "status": "healthy",
    "timestamp": "2026-01-08T10:30:00.000Z",
    "session": {
        "active": true,
        "session_dir": "/path/to/session",
        "conda_env": "session_env_name"
    }
}
```

---

### 2. Chat Endpoints

#### `POST /api/chat`
Start a new chat or continue an existing conversation.

**Request Body:**
```json
{
    "message": "Your message to the AI",
    "conversation_id": "optional-uuid-to-continue"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | string | Yes | The user's message |
| `conversation_id` | string | No | UUID to continue an existing conversation |

**Response:**
Server-Sent Events (SSE) stream with the following event types:

| Event Type | Description | Data Schema |
|------------|-------------|-------------|
| `thinking` | AI reasoning/thinking content | `{ "content": "..." }` |
| `content` | AI response text | `{ "content": "..." }` |
| `tool_call` | Tool being called | `{ "id": "...", "name": "...", "arguments": "..." }` |
| `tool_result` | Tool execution result | `{ "tool_call_id": "...", "content": "..." }` |
| `done` | Stream completed | `{ "conversation_id": "...", "status": "...", "message_count": n }` |
| `error` | Error occurred | `{ "message": "...", "type": "..." }` |

**Response Headers:**
```
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
X-Conversation-ID: <conversation-uuid>
```

**SSE Event Format:**
```
event: thinking
data: {"content": "I need to analyze this request..."}

event: tool_call
data: {"id": "call_123", "name": "google_search", "arguments": "{\"query\": \"...\"}"

event: tool_result
data: {"tool_call_id": "call_123", "content": "Search results..."}

event: content
data: {"content": "Based on my research, here's what I found..."}

event: done
data: {"conversation_id": "uuid", "status": "completed", "message_count": 5}
```

---

#### `POST /api/chat/continue`
Continue a conversation that reached the maximum iteration limit.

**Request Body:**
```json
{
    "conversation_id": "uuid-of-paused-conversation"
}
```

**Response:**
Same SSE stream format as `/api/chat`

**Status Codes:**
| Code | Description |
|------|-------------|
| 200 | Stream started |
| 400 | Conversation not in continuable state |
| 404 | Conversation not found |

---

### 3. Conversation Management

#### `GET /api/conversations`
List all active conversations.

**Response:**
```json
{
    "conversations": [
        {
            "id": "uuid-1",
            "status": "completed",
            "message_count": 10,
            "created_at": "2026-01-08T10:00:00.000Z",
            "updated_at": "2026-01-08T10:05:00.000Z"
        }
    ]
}
```

---

#### `GET /api/conversations/{conversation_id}`
Get full details of a specific conversation.

**Response:**
```json
{
    "id": "uuid",
    "status": "completed",
    "message_count": 5,
    "created_at": "2026-01-08T10:00:00.000Z",
    "updated_at": "2026-01-08T10:05:00.000Z",
    "messages": [
        {
            "role": "user",
            "content": "Hello"
        },
        {
            "role": "assistant",
            "content": "Hi! How can I help you?",
            "thinking": "The user greeted me..."
        }
    ]
}
```

---

#### `DELETE /api/conversations/{conversation_id}`
Delete a conversation.

**Response:**
```json
{
    "status": "deleted",
    "conversation_id": "uuid"
}
```

---

## Message Schema

### User Message
```json
{
    "role": "user",
    "content": "User's message text"
}
```

### Assistant Message
```json
{
    "role": "assistant",
    "content": "Assistant's response",
    "thinking": "Optional reasoning content",
    "tool_calls": [
        {
            "id": "call_123",
            "type": "function",
            "function": {
                "name": "tool_name",
                "arguments": "{\"param\": \"value\"}"
            }
        }
    ]
}
```

### Tool Response Message
```json
{
    "role": "tool",
    "tool_call_id": "call_123",
    "content": "Tool execution result"
}
```

---

## Conversation Status Values

| Status | Description |
|--------|-------------|
| `active` | Conversation is ongoing |
| `completed` | Conversation finished normally |
| `max_iterations_reached` | Iteration limit hit; can continue |
| `error` | An error occurred |

---

## Available Tools

The AI assistant has access to the following tools:

| Tool | Description |
|------|-------------|
| `google_search` | Web search via Google |
| `web_browser` | Read web page content |
| `read_file` | Read file contents |
| `write_file` | Create/overwrite files |
| `edit_file` | Edit existing files |
| `delete_file` | Delete files |
| `list_directory` | List directory contents |
| `search_files` | Search for files by name |
| `grep_search` | Search file contents |
| `run_terminal_command` | Execute shell commands |
| `tool_store` | Access additional APIs |

---

## Error Responses

All endpoints return standard error responses:

```json
{
    "detail": "Error message description"
}
```

**Common Status Codes:**
| Code | Description |
|------|-------------|
| 400 | Bad Request - Invalid parameters |
| 404 | Not Found - Resource doesn't exist |
| 500 | Internal Server Error |
| 503 | Service Unavailable |

---

## Frontend Integration Guide

### Connecting to SSE Stream

```javascript
const eventSource = new EventSource('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: 'Hello!' })
});

// Using fetch for POST + SSE
async function streamChat(message, conversationId) {
    const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, conversation_id: conversationId })
    });
    
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    
    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        
        const text = decoder.decode(value);
        // Parse SSE events from text
        const events = parseSSE(text);
        for (const event of events) {
            handleEvent(event.type, event.data);
        }
    }
}
```

---

## Rate Limits

| Endpoint | Limit |
|----------|-------|
| `/api/chat` | 10 requests/minute |
| Other endpoints | 60 requests/minute |

---

## Changelog

### v1.0.0 (2026-01-08)
- Initial API release
- SSE streaming for chat responses
- Conversation management endpoints
- Tool calling support with native OpenAI format
