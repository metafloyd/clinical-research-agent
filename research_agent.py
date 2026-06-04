import asyncio
import logging
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.prebuilt import create_react_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

from config import (
    CLINICALTRIALS_MCP_URL, PUBMED_MCP_URL,
    MODEL_ID, MAIN_MAX_TOKENS,
)
from prompts import SYSTEM_PROMPT
from trial_ops import make_trial_ops_tool, make_plot_tool, ensure_db

load_dotenv()
_log = logging.getLogger(__name__)

# ── LLM endpoint ──────────────────────────────────────────────────────────────

model = ChatOpenAI(
    model=MODEL_ID,
    api_key=os.getenv("OPENAI_API_KEY"),
    temperature=0,
    max_tokens=MAIN_MAX_TOKENS,
)


# ── Shared agent build (used by app.py, evals.py, and the CLI) ──────────────────

_MAX_TOOL_CHARS = 8000  # hard cap per tool result — bounds context growth so a
                        # pagination/aggregation runaway can't explode tokens/cost.


def _cap(content):
    if isinstance(content, str) and len(content) > _MAX_TOOL_CHARS:
        return content[:_MAX_TOOL_CHARS] + "\n…[truncated — refine the query, do not paginate further]"
    return content


def make_tools_resilient(tools):
    """Wrap each MCP tool to (1) turn tool errors into a recoverable message instead
    of crashing the run, and (2) cap output size. MCP tools use response_format=
    'content_and_artifact' → the coroutine must return a (content, artifact) tuple,
    so match that shape (a bare string raises ValueError)."""
    for t in tools:
        orig = getattr(t, "coroutine", None)
        if orig is None:
            continue
        async def _wrapped(*args, _orig=orig, _name=t.name,
                           _fmt=getattr(t, "response_format", None), **kwargs):
            try:
                result = await _orig(*args, **kwargs)
            except Exception as e:
                _log.warning("Tool %s error (recovered): %r", _name, e)
                msg = (f"Tool '{_name}' returned an error: {e}. Do not retry it the "
                       f"same way — adjust the query or use a different tool, then "
                       f"answer from what you can retrieve.")
                return (msg, None) if _fmt == "content_and_artifact" else msg
            if _fmt == "content_and_artifact" and isinstance(result, tuple) and len(result) == 2:
                return (_cap(result[0]), result[1])
            return _cap(result)
        t.coroutine = _wrapped
    return tools


async def build_agent():
    """Build the ReAct agent with resilient MCP tools + the internal trial-operations
    (NL→SQL) tool. Single source of truth used by app.py, evals.py, and the CLI."""
    ensure_db()  # create/seed the local SQLite CTMS DB if missing (idempotent)
    client = MultiServerMCPClient({
        "clinicaltrials": {"url": CLINICALTRIALS_MCP_URL, "transport": "streamable_http"},
        "pubmed":          {"url": PUBMED_MCP_URL,         "transport": "streamable_http"},
    })
    mcp_tools = await client.get_tools()
    tools = make_tools_resilient([*mcp_tools, make_trial_ops_tool(), make_plot_tool()])
    return create_react_agent(model, tools, prompt=SYSTEM_PROMPT)

# ── CLI runner ────────────────────────────────────────────────────────────────

async def _run_cli(user_input: str, agent) -> None:
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=user_input)]},
        config={"recursion_limit": 6},  # same hard cap as the app — bounds runaways
    )
    for msg in result["messages"][1:]:
        if isinstance(msg, AIMessage):
            if msg.tool_calls:
                for call in msg.tool_calls:
                    print(f"  [tool call] {call['name']}({call['args']})")
            elif msg.content:
                print(f"Agent: {msg.content}")
        elif isinstance(msg, ToolMessage):
            print(f"  [tool result] {msg.name} → {msg.content[:300]}")
    print()


async def main() -> None:
    agent = await build_agent()
    print(f"{CLINICALTRIALS_MCP_URL.split('/')[2]} — Clinical Research Intelligence Agent\n")
    print("Type 'quit' to exit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSession ended.")
            break
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Session ended.")
            break
        await _run_cli(user_input, agent)


if __name__ == "__main__":
    asyncio.run(main())
