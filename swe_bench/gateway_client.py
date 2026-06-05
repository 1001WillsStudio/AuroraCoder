"""
Async HTTP + SSE client for the AuroraCoder Gateway API.

Talks to one worker's gateway and consumes the SSE chat stream.
No knowledge of Docker or workspaces — pure HTTP.
"""

import asyncio
import json
import logging
import os
from typing import AsyncIterator, Optional

import httpx

logger = logging.getLogger(__name__)

# ── SSE event types ───────────────────────────────────────────────────

class SSEEvent:
    """A single SSE event from the gateway."""
    event: str
    data: dict

    def __init__(self, event: str, data: dict):
        self.event = event
        self.data = data

    def __repr__(self) -> str:
        return f"SSEEvent({self.event!r}, keys={list(self.data.keys())})"


class DoneEvent(SSEEvent):
    """Terminal event — the agent finished."""
    @property
    def status(self) -> str:
        return self.data.get("status", "unknown")


# ── Client ─────────────────────────────────────────────────────────────

class GatewayClient:
    """
    Async HTTP client for one AuroraCoder gateway instance.

    Usage:
        client = GatewayClient("http://localhost:8081")
        async for event in client.chat("my-id", "fix bug", "deepseek"):
            if event.event == "done":
                print("Finished:", event.data["status"])
    """

    def __init__(self, base_url: str, timeout: float = 1800.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._password: Optional[str] = os.environ.get("ACCESS_PASSWORD")

    # ── Auth helpers ───────────────────────────────────────────────

    def _headers(self) -> dict:
        hdrs: dict = {}
        if self._password:
            hdrs["Authorization"] = f"Bearer {self._password}"
        return hdrs

    # ── API methods ────────────────────────────────────────────────

    async def health(self) -> bool:
        """Check if the gateway is alive."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/health", headers=self._headers())
                return resp.status_code == 200
        except Exception:
            return False

    async def chat(
        self,
        conversation_id: str,
        message: str,
        provider: str,
    ) -> AsyncIterator[SSEEvent]:
        """
        POST /api/chat and yield SSE events.

        The gateway returns a text/event-stream. Each event has:
            event: <type>
            data: <json>

        Terminal event is 'done'. Non-terminal events include
        'thinking', 'message', 'tool_call', 'tool_result', 'error'.
        """
        payload = {
            "conversation_id": conversation_id,
            "message": message,
            "provider": provider,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
                headers=self._headers(),
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise GatewayError(
                        f"POST /api/chat returned {response.status_code}: {body.decode()[:500]}"
                    )

                async for event in self._parse_sse(response):
                    yield event

    async def get_conversation(self, conversation_id: str) -> dict:
        """
        GET /api/conversations/{id}

        Returns the full conversation object in gateway-compatible format:
            {id, type, status, messages: [...]}
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self.base_url}/api/conversations/{conversation_id}",
                headers=self._headers(),
            )
            if resp.status_code != 200:
                raise GatewayError(
                    f"GET /api/conversations/{conversation_id} returned {resp.status_code}"
                )
            return resp.json()

    async def cancel(self, conversation_id: str) -> None:
        """POST /api/conversations/{id}/cancel"""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self.base_url}/api/conversations/{conversation_id}/cancel",
                headers=self._headers(),
            )
            if resp.status_code not in (200, 404):
                logger.warning(
                    "Cancel %s returned %d", conversation_id, resp.status_code
                )

    # ── High-level helpers ─────────────────────────────────────────

    async def wait_for_done(
        self,
        conversation_id: str,
        message: str,
        provider: str,
        timeout: float,
    ) -> DoneEvent:
        """
        Send a chat message and block until the 'done' event or timeout.

        Returns the DoneEvent.
        Raises asyncio.TimeoutError if timeout exceeded.
        """
        last_event: Optional[SSEEvent] = None

        async def _consume():
            nonlocal last_event
            async for event in self.chat(conversation_id, message, provider):
                last_event = event
                if event.event == "done":
                    return DoneEvent(event="done", data=event.data)
                if event.event == "error":
                    logger.error("Agent error: %s", event.data)
                    return DoneEvent(event="done", data={"status": "error", **event.data})

        try:
            return await asyncio.wait_for(_consume(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Instance %s timed out after %.0fs — cancelling", conversation_id, timeout)
            await self.cancel(conversation_id)
            return DoneEvent(event="done", data={"status": "timeout"})

    # ── SSE parser ─────────────────────────────────────────────────

    async def _parse_sse(self, response: httpx.Response) -> AsyncIterator[SSEEvent]:
        """
        Parse a text/event-stream response into SSEEvent objects.

        Handles the standard SSE format:
            event: <type>
            data: <json>

        Also handles raw SSE lines without 'event:' prefix (treat as 'message').
        """
        event_type: str = ""
        data_buffer: str = ""

        async for line in response.aiter_lines():
            line = line.strip()

            if not line:
                # Empty line = dispatch event
                if data_buffer:
                    yield self._build_event(event_type, data_buffer)
                event_type = ""
                data_buffer = ""
                continue

            if line.startswith(":"):
                # SSE comment — ignore
                continue

            if ":" in line:
                field, _, value = line.partition(":")
                field = field.strip()
                value = value.strip()
                if field == "event":
                    event_type = value
                elif field == "data":
                    data_buffer = value
                else:
                    # Unknown field — ignore
                    pass
            else:
                # Line without colon — treat as data with no event type
                data_buffer = line
                if not event_type:
                    event_type = "message"

        # Flush any remaining event
        if data_buffer:
            yield self._build_event(event_type, data_buffer)

    @staticmethod
    def _build_event(event_type: str, data_str: str) -> SSEEvent:
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            data = {"raw": data_str}
        return SSEEvent(event=event_type, data=data)


class GatewayError(Exception):
    """Raised when the gateway returns a non-200 response."""
    pass
