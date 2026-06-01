"""
Offline evaluation suite — quality regression check + a showable LangSmith
experiment for the demo. STANDALONE: imported by nothing, never touches the
running app, adds zero latency to user requests.

Run it:  .venv\\Scripts\\python.exe evals.py
Then open smith.langchain.com -> Experiments to view / screenshot the scores.

Uses reference-free, criteria-based evaluators (no hand-written golden answers,
which would go stale as ClinicalTrials.gov/PubMed data changes):
  - groundedness    : every factual claim in the answer is supported by the
                      tool output the agent actually retrieved (anti-hallucination)
  - no_overcalling  : the agent didn't loop the same search / over-call tools
  - correct_scope   : research answered with tools, definitions answered without,
                      capability answered, off-topic declined

Needs OPENAI_API_KEY + LANGSMITH_API_KEY in .env (already set for tracing).
"""

import asyncio
import json
import re
from collections import Counter

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langsmith import Client, aevaluate

from config import MODEL_ID
from research_agent import build_agent  # shared resilient agent; also runs load_dotenv()

DATASET_NAME = "research-assistant-evals"

# Test battery — (query, type). Reference-free; type drives the scope evaluator.
QUERIES = [
    ("Find Phase 3 trials for semaglutide in cardiovascular disease", "research"),
    ("Find recruiting trials for Alzheimer's disease", "research"),
    ("What do we know about semaglutide for Type 2 Diabetes — active trials and published evidence?", "research"),
    ("What phases and locations dominate current CRISPR gene therapy trial activity globally?", "landscape"),
    ("How many recruiting trials are there for pancreatic cancer?", "count"),
    ("What is a Phase 3 trial?", "definition"),
    ("What tools do you have access to?", "capability"),
    ("What's the weather today?", "offtopic"),
]


# ── Agent under test = the SAME build_agent() the live app uses ─────────────────

def _make_target(agent):
    async def target(inputs: dict) -> dict:
        try:
            result = await agent.ainvoke(
                {"messages": [HumanMessage(content=inputs["query"])]},
                config={"recursion_limit": 6},
            )
        except Exception as exc:
            return {"answer": f"ERROR: {exc}", "tool_calls": [], "tool_outputs": []}

        tool_calls, tool_outputs, answer = [], [], ""
        for m in result["messages"]:
            if isinstance(m, AIMessage):
                for tc in (m.tool_calls or []):
                    tool_calls.append(tc["name"])
                if isinstance(m.content, str) and m.content.strip():
                    answer = m.content  # last non-empty AIMessage = final answer
            elif isinstance(m, ToolMessage):
                tool_outputs.append({"name": m.name, "content": str(m.content)[:4000]})
        return {"answer": answer, "tool_calls": tool_calls, "tool_outputs": tool_outputs}

    return target


# ── Evaluators (reference-free) ─────────────────────────────────────────────────

def no_overcalling(run, example):
    """Pass if the agent didn't loop searches: <=6 total calls and <=2 of any one."""
    calls = (run.outputs or {}).get("tool_calls", [])
    counts = Counter(calls)
    max_rep = max(counts.values()) if counts else 0
    ok = len(calls) <= 6 and max_rep <= 2
    return {"key": "no_overcalling", "score": int(ok),
            "comment": f"{len(calls)} calls, max {max_rep}/tool: {dict(counts)}"}


def correct_scope(run, example):
    """Right behavior for the query type (catches deflection / over-deflection)."""
    t = (example.inputs or {}).get("type", "research")
    out = run.outputs or {}
    answer = (out.get("answer") or "").lower()
    n_calls = len(out.get("tool_calls", []))
    redirected = "clinical trial and biomedical literature discovery only" in answer
    if t == "offtopic":
        ok = redirected
    elif t == "definition":
        ok = (n_calls == 0) and not redirected
    elif t == "capability":
        ok = not redirected  # should describe capabilities, not deflect
    else:  # research / landscape / count
        ok = (n_calls >= 1) and not redirected
    return {"key": "correct_scope", "score": int(ok),
            "comment": f"type={t}, tool_calls={n_calls}, redirected={redirected}"}


_judge = ChatOpenAI(model=MODEL_ID, temperature=0, max_tokens=300)

async def groundedness(run, example):
    """LLM-as-judge: is every factual claim supported by the retrieved tool output?"""
    t = (example.inputs or {}).get("type", "research")
    out = run.outputs or {}
    if t in ("definition", "capability", "offtopic"):
        return {"key": "groundedness", "score": None, "comment": "n/a (no retrieval expected)"}
    answer = out.get("answer", "")
    ctx = "\n\n".join(f"[{o['name']}]\n{o['content']}" for o in out.get("tool_outputs", []))
    if not ctx:
        return {"key": "groundedness", "score": 0, "comment": "no tool output retrieved"}
    prompt = (
        "You audit a clinical-research assistant for hallucination.\n\n"
        f"RETRIEVED CONTEXT (the only facts the assistant had):\n{ctx[:9000]}\n\n"
        f"ASSISTANT ANSWER:\n{answer[:4000]}\n\n"
        "Is EVERY specific factual claim in the answer (NCT IDs, PMIDs, enrollment "
        "counts, phases, sponsors, statuses, study findings) supported by the context? "
        "Generic framing/Key-Insight prose is fine. Reply ONLY with JSON: "
        '{"grounded": true|false, "reason": "<=1 sentence"}.'
    )
    resp = await _judge.ainvoke(prompt)
    txt = resp.content if isinstance(resp.content, str) else str(resp.content)
    try:
        data = json.loads(re.search(r"\{.*\}", txt, re.DOTALL).group(0))
        grounded, reason = bool(data.get("grounded")), str(data.get("reason", ""))
    except Exception:
        grounded, reason = ("true" in txt.lower()[:60]), txt[:200]
    return {"key": "groundedness", "score": int(grounded), "comment": reason[:300]}


# ── Runner ──────────────────────────────────────────────────────────────────────

async def main():
    client = Client()
    if not client.has_dataset(dataset_name=DATASET_NAME):
        client.create_dataset(DATASET_NAME, description="Research Assistant quality eval battery")
        client.create_examples(
            dataset_name=DATASET_NAME,
            examples=[{"inputs": {"query": q, "type": t}} for q, t in QUERIES],
        )
        print(f"Created dataset '{DATASET_NAME}' with {len(QUERIES)} examples.")
    else:
        print(f"Using existing dataset '{DATASET_NAME}'.")

    agent = await build_agent()
    results = await aevaluate(
        _make_target(agent),
        data=DATASET_NAME,
        evaluators=[no_overcalling, correct_scope, groundedness],
        experiment_prefix="research-assistant",
        max_concurrency=2,
    )
    print("\nDone. Open smith.langchain.com -> Experiments to view/screenshot scores.")
    return results


if __name__ == "__main__":
    asyncio.run(main())
