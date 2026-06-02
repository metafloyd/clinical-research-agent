import asyncio
import os
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
from langchain_core.messages import HumanMessage, AIMessage

_log = logging.getLogger(__name__)

from config import BETA_WHITELIST
from db import (
    upsert_user, create_session, set_session_title,
    save_message,
)
from questions import QUESTION_POOL
from research_agent import build_agent


# ── Data layer patch ────────────────────────────────────────────────────────
# Chainlit's ChainlitDataLayer.create_step auto-creates a placeholder parent
# "run" step WITHOUT a threadId when a child step's parent isn't persisted yet
# (chainlit_data_layer.py:330-343), violating the NOT NULL constraint on
# Step.threadId — which silently drops steps, so resumed threads lose messages.
# Backfill the threadId from the active session before delegating to Chainlit.
if os.getenv("DATABASE_URL"):
    from chainlit.context import context as _cl_context
    from chainlit.data.chainlit_data_layer import ChainlitDataLayer

    class _PatchedDataLayer(ChainlitDataLayer):
        async def create_step(self, step_dict):
            if not step_dict.get("threadId"):
                try:
                    tid = _cl_context.session.thread_id
                    if tid:
                        step_dict["threadId"] = tid
                except Exception:
                    pass
            return await super().create_step(step_dict)

    @cl.data_layer
    def _get_data_layer():
        return _PatchedDataLayer(database_url=os.environ["DATABASE_URL"])


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
_agent_lock = asyncio.Lock()


async def _ensure_agent():
    """Build the shared agent once via research_agent.build_agent() (resilient tools).
    Concurrency-safe so a background warm-up and the first message can't double-build.
    NEVER await this inside on_chat_start/on_chat_resume — those run in the websocket
    connection path, and a cold ~15s build there blocks the socket and triggers a
    reload loop. Warm it in the background there; await it only in handle_query."""
    global _shared_agent
    if _shared_agent is not None:
        return _shared_agent
    async with _agent_lock:
        if _shared_agent is None:
            _shared_agent = await build_agent()
            _log.info("Agent initialised")
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
    # Warm the agent in the background — do NOT await (would block the connection).
    asyncio.create_task(_ensure_agent())


# ── Session resume (page reload) ────────────────────────────────────────────────
# Chainlit calls this on reload INSTEAD of on_chat_start. Without it, persisted
# threads render read-only. Rebuild the backend session state so the user can
# continue: re-attach the agent, restore lc_messages from the persisted steps.

@cl.on_chat_resume
async def on_chat_resume(thread):
    try:
        # Warm the agent in the background — do NOT await here. on_chat_resume is
        # awaited inline in the websocket connection handler, so a cold ~15s agent
        # build would block the socket and cause the frontend reload loop.
        asyncio.create_task(_ensure_agent())

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
    lc_msgs = cl.user_session.get("lc_messages", [])
    sid     = cl.user_session.get("db_session_id")
    turn    = cl.user_session.get("turn", 0)

    # Build the agent on first message (warmed in the background at chat start/resume).
    try:
        agent = await _ensure_agent()
    except Exception as exc:
        _log.error("Agent init failed: %r", exc)
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
            config={"recursion_limit": 6},  # hard cap on the agent loop — bounds runaways
        ):
            kind = event["event"]
            if kind == "on_tool_start":
                _tname = event["name"]
                if _tname == "query_trial_operations":
                    badge = "🔍"                       # internal DB query
                elif _is_ct_tool(_tname):
                    badge = "🏥"                       # ClinicalTrials.gov
                else:
                    badge = "📚"                       # PubMed
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
                # Only stream the MAIN agent's synthesis tokens (langgraph_node ==
                # "agent"). The NL→SQL generator inside query_trial_operations is a
                # NESTED model call running in the "tools" node — without this guard
                # its raw SQL streams straight into the answer (the "ugly SQL" bug).
                node = event.get("metadata", {}).get("langgraph_node")
                chunk = event["data"].get("chunk")
                if (node == "agent" and chunk and isinstance(chunk.content, str)
                        and chunk.content
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
