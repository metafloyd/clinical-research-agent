"""
Supabase persistence layer.

One connection per process via @lru_cache.
All functions are synchronous — the supabase-py SDK is sync by default.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

import os
from supabase import create_client, Client


@lru_cache(maxsize=1)
def _get_client() -> Client:
    url: str = os.environ["SUPABASE_URL"]
    # Canonical name is SUPABASE_SERVICE_KEY (used app-wide); fall back to the legacy
    # SUPABASE_SERVICE_ROLE_KEY so the app keeps booting during/after the rename.
    key: str = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── users ─────────────────────────────────────────────────────────────────────

def upsert_user(email: str, display_name: Optional[str] = None) -> str:
    """Insert or update user on login. Returns user UUID."""
    client = _get_client()
    result = (
        client.table("users")
        .upsert(
            {
                "email": email,
                "display_name": display_name or email.split("@")[0],
                "last_seen_at": _now(),
            },
            on_conflict="email",
        )
        .execute()
    )
    return result.data[0]["id"]


# ── chat_sessions ─────────────────────────────────────────────────────────────

def create_session(user_id: str) -> str:
    """Create a new chat session row. Returns session UUID."""
    client = _get_client()
    result = (
        client.table("chat_sessions")
        .insert({"user_id": user_id})
        .execute()
    )
    return result.data[0]["id"]


def set_session_title(session_id: str, first_user_message: str) -> None:
    """Set session title from the first user message (called once, on first turn)."""
    client = _get_client()
    client.table("chat_sessions").update({"title": first_user_message[:120]}).eq("id", session_id).execute()


# ── chat_messages ─────────────────────────────────────────────────────────────

def save_message(
    session_id: str,
    msg_index: int,
    role: str,
    content: str,
    tools_used: list,
    sources: list,
) -> str:
    """
    Persist one chat_display item. Returns the message UUID.
    sources is list of (type, id) tuples or lists — coerced to list-of-lists for JSONB.
    """
    client = _get_client()
    result = (
        client.table("chat_messages")
        .insert(
            {
                "session_id": session_id,
                "msg_index": msg_index,
                "role": role,
                "content": content,
                "tools_used": list(tools_used),
                "sources": [list(s) for s in sources],
            }
        )
        .execute()
    )
    return result.data[0]["id"]


