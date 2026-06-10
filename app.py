import asyncio
import os
import re
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
    from chainlit.data.storage_clients.base import BaseStorageClient

    _EL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public", "_el")

    class _LocalElementStorage(BaseStorageClient):
        """Persist element CONTENT (the Plotly figure JSON) to the Chainlit-served public/
        dir so charts survive a page RELOAD. Local-dev / fallback only: it's EPHEMERAL on
        Render (a *deploy* clears the dir, so charts in old threads won't restore after a
        deploy). Used when Supabase Storage isn't configured."""

        async def upload_file(self, object_key, data, mime="application/octet-stream",
                              overwrite=True, content_disposition=None):
            os.makedirs(_EL_DIR, exist_ok=True)
            name = object_key.replace("/", "__")
            with open(os.path.join(_EL_DIR, name), "wb") as f:
                f.write(data if isinstance(data, (bytes, bytearray)) else data.encode("utf-8"))
            return {"object_key": object_key, "url": f"/public/_el/{name}"}

        async def get_read_url(self, object_key):
            return f"/public/_el/{object_key.replace('/', '__')}"

        async def delete_file(self, object_key):
            try:
                os.remove(os.path.join(_EL_DIR, object_key.replace("/", "__")))
            except Exception:
                pass
            return True

        async def close(self):
            pass

    class _SupabaseStorageClient(BaseStorageClient):
        """Persist element CONTENT (chart figure JSON) to a Supabase Storage bucket so charts
        survive both page RELOAD and DEPLOY (durable, unlike the local dir). Reuses the
        supabase-py SDK already in requirements — no boto3. Chainlit's built-in S3StorageClient
        is NOT used because it emits AWS virtual-hosted URLs that don't resolve against Supabase.

        Reads use STATIC PUBLIC URLs (no signing, no network call) — Chainlit regenerates a read
        URL per chart element on EVERY thread reload (twice, via two get_thread calls), so a signed
        URL there meant 2N Supabase round-trips per reload and slow, chart-count-dependent reloads.
        A public URL is pure string construction → reloads are fast and constant-time. Requires the
        bucket to be PUBLIC in Supabase. Trade-off: chart figure JSON is publicly readable at its
        (unguessable UUID) URL — acceptable for synthetic demo CTMS data only.

        Configure via env:  SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_STORAGE_BUCKET."""

        def __init__(self, url, service_key, bucket):
            from supabase import create_client
            self._client = create_client(url, service_key)
            self._bucket = bucket
            self._url_cache = {}   # object_key -> public_url (no-network; cheap insurance)

        def _bkt(self):
            return self._client.storage.from_(self._bucket)

        def _public_url(self, object_key):
            url = self._url_cache.get(object_key)
            if url is None:
                url = self._bkt().get_public_url(object_key)   # pure string build, no I/O
                self._url_cache[object_key] = url
            return url

        async def upload_file(self, object_key, data, mime="application/octet-stream",
                              overwrite=True, content_disposition=None):
            payload = data if isinstance(data, (bytes, bytearray)) else data.encode("utf-8")

            def _do():
                self._bkt().upload(
                    object_key, bytes(payload),
                    {"content-type": mime, "upsert": "true" if overwrite else "false"},
                )

            try:
                await asyncio.to_thread(_do)
                return {"object_key": object_key, "url": self._public_url(object_key)}
            except Exception as exc:
                _log.warning("Supabase upload_file failed (%s): %r", object_key, exc)
                return {}

        async def get_read_url(self, object_key):
            # Public URL = no signing, no network round-trip → fast reloads regardless of
            # chart count. (See class docstring for the why + the privacy trade-off.)
            try:
                return self._public_url(object_key)
            except Exception as exc:
                _log.warning("Supabase get_read_url failed (%s): %r", object_key, exc)
                return object_key

        async def delete_file(self, object_key):
            try:
                await asyncio.to_thread(lambda: self._bkt().remove([object_key]))
                return True
            except Exception as exc:
                _log.warning("Supabase delete_file failed (%s): %r", object_key, exc)
                return False

        async def close(self):
            pass

    def _build_storage_client():
        """Prefer durable Supabase Storage; fall back to the local served dir for dev or
        if Supabase Storage env isn't set (so charts still persist across a reload locally)."""
        s_url = os.getenv("SUPABASE_URL")
        s_key = os.getenv("SUPABASE_SERVICE_KEY")
        s_bucket = os.getenv("SUPABASE_STORAGE_BUCKET")
        if s_url and s_key and s_bucket:
            try:
                client = _SupabaseStorageClient(s_url, s_key, s_bucket)
                _log.info("Chart persistence: Supabase Storage bucket %r", s_bucket)
                return client
            except Exception as exc:
                _log.warning("Supabase Storage init failed, using local fallback: %r", exc)
        else:
            _log.info("Chart persistence: local dir (Supabase Storage env not set)")
        return _LocalElementStorage()

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

        async def list_threads(self, pagination, filters):
            # Keep the sidebar uncluttered: show only the 5 most recent chats.
            # (Older threads are NOT deleted — just not listed. Search is left
            # uncapped so users can still find anything.)
            searching = bool(getattr(filters, "search", None))
            if not searching:
                try:
                    pagination.first = 5
                except Exception:
                    pass
            resp = await super().list_threads(pagination, filters)
            if not searching:
                try:
                    resp.data = resp.data[:5]
                    resp.pageInfo.hasNextPage = False
                except Exception:
                    pass
            return resp

    @cl.data_layer
    def _get_data_layer():
        # storage_client lets Chainlit persist element content (chart figures) so they
        # survive a reload/deploy — without it, charts disappear on resume.
        return _PatchedDataLayer(database_url=os.environ["DATABASE_URL"],
                                 storage_client=_build_storage_client())


_CT_KEYWORDS = {"clinical", "trial", "nct", "study"}


def _is_ct_tool(name: str) -> bool:
    return any(k in name.lower() for k in _CT_KEYWORDS)


# Human-readable activity labels for the tool steps. The audience reads these
# during the 10-25s heavy queries — narrate what's happening instead of showing
# raw tool names like "clinicaltrials_search_studies".
_STEP_LABELS = {
    "query_trial_operations":        "🔍 Querying our trial operations…",
    "plot_trial_operations":         "📊 Building your chart…",
    "clinicaltrials_search_studies": "🏥 Searching ClinicalTrials.gov…",
    "clinicaltrials_get_study_record": "🏥 Retrieving the trial record…",
    "clinicaltrials_get_study_count":  "🏥 Counting matching trials…",
    "clinicaltrials_get_study_results": "🏥 Retrieving trial results…",
    "clinicaltrials_find_eligible":  "🏥 Matching eligibility criteria…",
    "pubmed_europepmc_search":       "📚 Searching the published literature…",
    "pubmed_search_articles":        "📚 Counting matching papers…",
    "pubmed_fetch_articles":         "📚 Retrieving article details…",
    "pubmed_fetch_fulltext":         "📚 Retrieving full text…",
    "pubmed_find_related":           "📚 Finding related papers…",
    "pubmed_lookup_mesh":            "📚 Looking up MeSH terms…",
    "pubmed_format_citations":       "📚 Formatting citations…",
}


def _step_label(tool_name: str) -> str:
    label = _STEP_LABELS.get(tool_name)
    if label:
        return label
    # Fallback for unmapped tools: keep the source badge + prettify the name.
    pretty = tool_name.replace("clinicaltrials_", "").replace("pubmed_", "").replace("_", " ")
    badge = "🏥" if _is_ct_tool(tool_name) else "📚"
    return f"{badge} {pretty.capitalize()}…"


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
    # Deterministic: always the 4 SHOWCASE starters, same order — the landing
    # screen is identical on every reload (demo predictability over variety).
    return [
        cl.Starter(
            label=f"{badge} {q}",
            message=q,
        )
        for badge, q in QUESTION_POOL[:4]
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
            # Pre-import plotly in a thread WHILE the ~15s MCP tool fetch runs —
            # otherwise the FIRST chart query pays the import cost inside the turn.
            def _warm_plotly():
                try:
                    import plotly.graph_objects  # noqa: F401
                    import plotly.io  # noqa: F401
                except Exception as exc:
                    _log.warning("plotly pre-import failed (non-fatal): %r", exc)
            warm = asyncio.create_task(asyncio.to_thread(_warm_plotly))
            _shared_agent = await build_agent()
            await warm
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
    cl.user_session.set("lc_messages", [])
    cl.user_session.set("turn", 0)
    # Open the analytics session row in the BACKGROUND — don't block the connection
    # handler on two serial remote-Postgres round-trips. handle_query lazily ensures
    # db_session_id if a message arrives before this finishes.
    async def _open_session_bg():
        try:
            cl.user_session.set("db_session_id", await _new_db_session(user))
        except Exception as exc:
            _log.error("deferred _new_db_session (start) failed: %r", exc)
    asyncio.create_task(_open_session_bg())
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

        # Open the analytics chat_sessions row in the BACKGROUND. on_chat_resume is
        # awaited inline in the websocket connection handler, and _new_db_session is
        # TWO serial round-trips to the remote Postgres (~1-3s) — awaiting it here just
        # delays the page becoming interactive. db_session_id isn't read until the user
        # sends a message (handle_query), which lazily ensures it if not ready yet.
        _user = cl.user_session.get("user")
        async def _open_session_bg():
            try:
                cl.user_session.set("db_session_id", await _new_db_session(_user))
            except Exception as exc:
                _log.error("deferred _new_db_session (resume) failed: %r", exc)
        asyncio.create_task(_open_session_bg())
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

    # The analytics session row is opened in the background on start/resume so it
    # doesn't block the reload. If a message arrives before it's ready, open it now.
    if not sid:
        try:
            sid = await _new_db_session(cl.user_session.get("user"))
            cl.user_session.set("db_session_id", sid)
        except Exception as exc:
            _log.error("lazy _new_db_session failed (non-fatal): %r", exc)

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
    charts: list[str] = []   # Plotly figure JSONs from plot_trial_operations

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
                try:
                    async with cl.Step(name=_step_label(event["name"]), type="tool"):
                        pass
                except Exception as step_exc:
                    _log.warning("Step persistence failed (non-fatal): %r", step_exc)
            elif kind == "on_tool_end":
                # plot_trial_operations returns a Plotly figure JSON as its artifact —
                # capture it to render as a chart on the message.
                if event["name"] == "plot_trial_operations":
                    out = event["data"].get("output")
                    fig = getattr(out, "artifact", None)
                    if fig is None and isinstance(out, tuple) and len(out) == 2:
                        fig = out[1]
                    if isinstance(fig, str) and fig.strip():
                        charts.append(fig)
                    continue
                # Don't scrape the INTERNAL tool's output for citations: its rows carry
                # our studies' nct_id values (registry references), not a ClinicalTrials.gov
                # search — labeling them "Sources: ClinicalTrials.gov" on a pure-internal
                # query misleads the user. Only public tools contribute sources.
                if event["name"] != "query_trial_operations":
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

    # Attach any internal-data charts (plot_trial_operations) as inline Plotly elements.
    if charts:
        import plotly.io as _pio
        for i, figjson in enumerate(charts):
            try:
                msg.elements.append(
                    cl.Plotly(name=f"chart-{i}", figure=_pio.from_json(figjson), display="inline")
                )
            except Exception as chart_exc:
                _log.warning("Chart render failed (non-fatal): %r", chart_exc)

    await msg.update()

    # Update session state
    lc_msgs.append(AIMessage(content=msg.content))
    cl.user_session.set("lc_messages", lc_msgs)
    cl.user_session.set("turn", turn + 1)

    # Persist to Supabase in the BACKGROUND. The answer is already rendered; these
    # 2-3 serial REST round-trips were keeping the turn "running" (stop button /
    # spinner / done-sound all wait on this handler returning) for ~0.5-1.5s.
    user_idx = turn * 2
    answer = msg.content
    src = list(sources)

    def _persist():
        save_message(sid, user_idx, "user", query, [], [])
        save_message(sid, user_idx + 1, "assistant", answer, [], src)
        if turn == 0:
            set_session_title(sid, query)

    async def _persist_bg():
        try:
            await asyncio.to_thread(_persist)
        except Exception as exc:
            _log.error("background message persist failed (non-fatal): %r", exc)
    asyncio.create_task(_persist_bg())
