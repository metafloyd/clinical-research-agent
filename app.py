import asyncio
import re
import random
from typing import Optional

# asyncpg default pool size (10) hits Supabase session pooler's 15-connection cap.
# Cap it at 3 — sufficient for a demo, stays within the free-tier limit.
import asyncpg as _asyncpg
_orig_create_pool = _asyncpg.create_pool
async def _small_pool(dsn=None, *, min_size=10, max_size=10, **kw):
    return await _orig_create_pool(dsn, min_size=1, max_size=3, statement_cache_size=0, **kw)
_asyncpg.create_pool = _small_pool

import logging

import chainlit as cl
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, AIMessage
from langchain_mcp_adapters.client import MultiServerMCPClient

_log = logging.getLogger(__name__)

from config import (
    CLINICALTRIALS_MCP_URL, PUBMED_MCP_URL, BETA_WHITELIST,
)
from db import (
    upsert_user, create_session, set_session_title,
    save_message,
)
from prompts import SYSTEM_PROMPT
from questions import QUESTION_POOL
from research_agent import model


_CT_KEYWORDS = {"clinical", "trial", "nct", "study"}


def _is_ct_tool(name: str) -> bool:
    return any(k in name.lower() for k in _CT_KEYWORDS)


# ── Auth ───────────────────────────────────────────────────────────────────────

@cl.oauth_callback
def oauth_callback(
    provider_id: str,
    token: str,
    raw_user_data: dict,
    default_user: cl.User,
) -> Optional[cl.User]:
    email = raw_user_data.get("email", "")
    if email.lower() in [e.lower() for e in BETA_WHITELIST]:
        name = raw_user_data.get("given_name") or raw_user_data.get("name") or email.split("@")[0]
        return cl.User(identifier=email, display_name=name, metadata=raw_user_data)
    return None


# ── Starter questions (shown before first message) ─────────────────────────────

@cl.set_starters
async def set_starters():
    starters = random.sample(QUESTION_POOL, 4)
    return [
        cl.Starter(
            label=f"{badge} {q}",
            message=q,
        )
        for badge, q in starters
    ]


# Module-level agent singleton — MCP tools fetched once per server lifetime,
# not on every session start. Eliminates 15-20s reload latency.
_shared_agent = None


async def _ensure_agent():
    """Build the shared agent once (MCP tools fetched once per server lifetime)."""
    global _shared_agent
    if _shared_agent is None:
        client = MultiServerMCPClient({
            "clinicaltrials": {"url": CLINICALTRIALS_MCP_URL, "transport": "streamable_http"},
            "pubmed":         {"url": PUBMED_MCP_URL,         "transport": "streamable_http"},
        })
        mcp_tools = await client.get_tools()
        _shared_agent = create_react_agent(model, mcp_tools, prompt=SYSTEM_PROMPT)
        _log.info("Agent initialised with %d tools", len(mcp_tools))
    return _shared_agent


def _first_name(user) -> str:
    email = user.identifier if user else ""
    meta = (user.metadata or {}) if user else {}
    return meta.get("given_name") or meta.get("name") or email.split("@")[0].capitalize()


async def _new_db_session(user) -> str:
    """Upsert the user and open a fresh chat_sessions row (off the event loop)."""
    email = user.identifier if user else ""
    uid = await asyncio.to_thread(upsert_user, email=email, display_name=_first_name(user))
    return await asyncio.to_thread(create_session, uid)


# ── Session start ──────────────────────────────────────────────────────────────

@cl.on_chat_start
async def on_chat_start():
    user = cl.user_session.get("user")
    try:
        cl.user_session.set("db_session_id", await _new_db_session(user))
    except Exception as exc:
        _log.error("on_chat_start db init failed (non-fatal): %r", exc)
    cl.user_session.set("lc_messages", [])
    cl.user_session.set("turn", 0)
    try:
        cl.user_session.set("agent", await _ensure_agent())
    except Exception as exc:
        _log.error("on_chat_start agent init failed: %r", exc)
        raise


# ── Session resume (page reload) ────────────────────────────────────────────────
# Chainlit calls this on reload INSTEAD of on_chat_start. Without it, persisted
# threads render read-only. Rebuild the backend session state so the user can
# continue: re-attach the agent, restore lc_messages from the persisted steps.

@cl.on_chat_resume
async def on_chat_resume(thread):
    try:
        cl.user_session.set("agent", await _ensure_agent())

        lc = []
        for step in thread.get("steps", []):
            out = step.get("output") or ""
            if not out:
                continue
            if step.get("type") == "user_message":
                lc.append(HumanMessage(content=out))
            elif step.get("type") == "assistant_message":
                lc.append(AIMessage(content=out))
        cl.user_session.set("lc_messages", lc)
        cl.user_session.set("turn", len(lc) // 2)

        cl.user_session.set("db_session_id", await _new_db_session(cl.user_session.get("user")))
    except Exception as exc:
        _log.error("on_chat_resume failed: %r", exc)


# ── Message handler ────────────────────────────────────────────────────────────

@cl.on_message
async def on_message(message: cl.Message):
    await handle_query(message.content)


async def handle_query(query: str):
    agent   = cl.user_session.get("agent")
    lc_msgs = cl.user_session.get("lc_messages", [])
    sid     = cl.user_session.get("db_session_id")
    turn    = cl.user_session.get("turn", 0)

    if agent is None:
        await cl.Message(content="⚠️ Agent failed to start — check server logs.").send()
        return

    lc_msgs.append(HumanMessage(content=query))

    msg = cl.Message(content="")
    sources: set[tuple[str, str]] = set()

    # Keep last 6 messages (3 turns) — GPT-4o-mini has 128k context, no token pressure.
    ctx_msgs = lc_msgs[-6:]

    # Single path: always run the tool-capable agent. The model decides whether to
    # call tools (the prompt tells it to skip tools when the answer is already in
    # context, and to retrieve when a follow-up needs data it doesn't have).
    # GPT-4o-mini emits no prose between tool calls, so model text is final-answer
    # text and streams token-by-token as it arrives.
    streamed = False
    try:
        async for event in agent.astream_events(
            {"messages": ctx_msgs},
            version="v2",
            config={"recursion_limit": 10},
        ):
            kind = event["event"]
            if kind == "on_tool_start":
                badge = "🏥" if _is_ct_tool(event["name"]) else "📚"
                try:
                    async with cl.Step(name=f"{badge} {event['name']}", type="tool"):
                        pass
                except Exception as step_exc:
                    _log.warning("Step persistence failed (non-fatal): %r", step_exc)
            elif kind == "on_tool_end":
                raw = str(event["data"].get("output", ""))
                for nct in re.findall(r'NCT\d{8}', raw):
                    sources.add(("ct", nct))
                for pmid in re.findall(r'(?i)(?:"pmid"|pmid)["\s:]+(\d{6,8})', raw):
                    sources.add(("pm", pmid))
            elif kind == "on_chat_model_stream":
                chunk = event["data"].get("chunk")
                if (chunk and isinstance(chunk.content, str) and chunk.content
                        and not getattr(chunk, "tool_call_chunks", [])):
                    await msg.stream_token(chunk.content)
                    streamed = True
    except Exception as exc:
        _log.error("Agent error: %r", exc)

    if not streamed:
        await msg.stream_token(
            "The search did not return results. Try narrowing your query "
            "with a specific condition, drug name, or NCT ID."
        )

    # Render citations inline at the bottom of the message (no side panel).
    if sources:
        ct = sorted(i for t, i in sources if t == "ct")
        pm = sorted(i for t, i in sources if t == "pm")
        lines = []
        if ct:
            lines.append("**🏥 ClinicalTrials.gov**")
            lines.extend(f"- [{nct}](https://clinicaltrials.gov/study/{nct})" for nct in ct[:5])
        if pm:
            lines.append("**📚 PubMed**")
            lines.extend(f"- [PMID {pmid}](https://pubmed.ncbi.nlm.nih.gov/{pmid}/)" for pmid in pm[:5])
        await msg.stream_token("\n\n---\n\n**📎 Sources**\n\n" + "\n".join(lines))

    await msg.update()

    # Update session state
    lc_msgs.append(AIMessage(content=msg.content))
    cl.user_session.set("lc_messages", lc_msgs)
    cl.user_session.set("turn", turn + 1)

    # Persist to Supabase off the event loop (sync SDK would block it otherwise).
    user_idx = turn * 2
    try:
        await asyncio.to_thread(save_message, sid, user_idx, "user", query, [], [])
        await asyncio.to_thread(
            save_message, sid, user_idx + 1, "assistant", msg.content, [], list(sources)
        )
        if turn == 0:
            await asyncio.to_thread(set_session_title, sid, query)
    except Exception:
        pass
