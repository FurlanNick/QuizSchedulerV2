"""
Simple in-memory session store.
In production this would be backed by a database (SQLite, Postgres, etc.)
"""
import uuid
from typing import Dict, Optional
from app.models import ProgramState


_sessions: Dict[str, ProgramState] = {}
_name_to_session: Dict[str, str] = {}   # name (lowercase) -> session_id


def get_or_create_session(name: str) -> tuple[ProgramState, bool]:
    """Return (state, is_new). Matches by lowercase name so sign-in is case-insensitive."""
    key = name.strip().lower()
    if key in _name_to_session:
        sid = _name_to_session[key]
        return _sessions[sid], False
    
    sid = str(uuid.uuid4())
    state = ProgramState(session_id=sid, owner_name=name.strip())
    _sessions[sid] = state
    _name_to_session[key] = sid
    return state, True


def get_session(session_id: str) -> Optional[ProgramState]:
    return _sessions.get(session_id)


def save_session(state: ProgramState) -> None:
    _sessions[state.session_id] = state


def list_sessions() -> list[str]:
    return [s.owner_name for s in _sessions.values()]
