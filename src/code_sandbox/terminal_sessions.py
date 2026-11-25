"""Own the stateful terminal used by each agent conversation."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic

from .sandbox import PersistentShell, shell


_IDLE_SECONDS = 3600


@dataclass
class _TerminalSession:
    terminal: PersistentShell | None = None
    lock: Lock = field(default_factory=Lock)
    busy: int = 0
    last_used: float | None = None


_sessions: dict[str, _TerminalSession] = {}
_sessions_lock = Lock()
_legacy_lock = Lock()


@contextmanager
def use_terminal(conversation_id: str | None, reset: bool):
    if conversation_id is None:
        with _legacy_lock:
            if reset:
                shell.restart()
            yield shell
        return

    expired = []
    now = monotonic()
    with _sessions_lock:
        for session in _sessions.values():
            if (not session.busy and session.terminal and session.last_used is not None
                    and now - session.last_used > _IDLE_SECONDS):
                expired.append(session.terminal)
                session.terminal = None

        if conversation_id not in _sessions:
            _sessions[conversation_id] = _TerminalSession(terminal=PersistentShell())
        session = _sessions[conversation_id]
        session.busy += 1

    try:
        for terminal in expired:
            terminal.stop()
        with session.lock:
            fresh = session.last_used is None
            session.last_used = now
            if reset:
                if session.terminal is None:
                    session.terminal = PersistentShell()
                session.terminal.restart()
            elif fresh:
                session.terminal.start()
            yield session.terminal
    finally:
        with _sessions_lock:
            session.last_used = monotonic()
            session.busy -= 1


def stop_terminal_sessions() -> None:
    with _sessions_lock:
        sessions = tuple(_sessions.values())
        _sessions.clear()
    for session in sessions:
        if session.terminal is not None:
            session.terminal.stop()
