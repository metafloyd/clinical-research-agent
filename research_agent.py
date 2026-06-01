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

def make_tools_resilient(tools):
    """Wrap each MCP tool so a tool error returns a recoverable message instead of
    raising and crashing the agent run. MCP tools use response_format=
    'content_and_artifact' → their coroutine must return a (content, artifact)
    tuple, so match that shape on error (a bare string raises ValueError)."""
    for t in tools:
        orig = getattr(t, "coroutine", None)
        if orig is None:
            continue
        async def _wrapped(*args, _orig=orig, _name=t.name,
                           _fmt=getattr(t, "response_format", None), **kwargs):
            try:
                return await _orig(*args, **kwargs)
            except Exception as e:
                _log.warning("Tool %s error (recovered): %r", _name, e)
                msg = (f"Tool '{_name}' returned an error: {e}. Do not retry it the "
                       f"same way — use clinicaltrials_search_studies instead and "
                       f"aggregate from the results.")
                return (msg, None) if _fmt == "content_and_artifact" else msg
        t.coroutine = _wrapped
    return tools


async def build_agent():
    """Build the ReAct agent with resilient MCP tools. Single source of truth."""
    client = MultiServerMCPClient({
        "clinicaltrials": {"url": CLINICALTRIALS_MCP_URL, "transport": "streamable_http"},
        "pubmed":          {"url": PUBMED_MCP_URL,         "transport": "streamable_http"},
    })
    tools = make_tools_resilient(await client.get_tools())
    return create_react_agent(model, tools, prompt=SYSTEM_PROMPT)

# ── CLI runner ────────────────────────────────────────────────────────────────

async def _run_cli(user_input: str, agent) -> None:
    result = await agent.ainvoke({"messages": [HumanMessage(content=user_input)]})
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
