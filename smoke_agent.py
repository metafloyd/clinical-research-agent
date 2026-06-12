"""
Full-AGENT regression smoke test — catches SYNTHESIS fabrication and ROUTING bugs
that the tool-layer smoke_ctms.py cannot. It runs the real agent (incl. MCP), so it's
slower; each case asserts on the rendered answer and/or which tools fired.

Why both exist: smoke_ctms.py checks the NL→SQL TOOL output (data vs decline). But the
worst bugs this project hit were in the AGENT's *rendering* — e.g. a count query that
fabricated a "Study 1 — 20/50" list, or "what Alzheimer's study?" dropping one of two
studies and inventing "enrolled 15/75". Those only show at the agent layer, here.

Run:  .venv\\Scripts\\python.exe smoke_agent.py
Non-zero exit on regression (can gate a deploy). Internal-data checks are deterministic;
routing checks assert which tool fired (stable even though public data is live).
"""

import asyncio
import sys

from langchain_core.messages import AIMessage, HumanMessage
from research_agent import build_agent

# Check DSL — each is (kind, arg); evaluated against (answer_lower, tool_names).
def has(*subs):      return ("contains", subs)
def absent(*subs):   return ("absent", subs)
def used(sub):       return ("used", sub)
def not_used(sub):   return ("not_used", sub)

# (question, [checks]) — each case encodes a bug we fixed this session.
CASES = [
    # count → exact number, NO fabricated "Study 1 — 20/50" list (synthesis fabrication)
    # (counts reflect the 2026-06-05 enriched DB: 22 studies, 15 active / 7 completed)
    ("how many active studies do we have",        [has("15"), absent("study 1", "study 2")]),
    ("how many completed studies do we have",     [has("7"), absent("study 1")]),
    # Alzheimer's → BOTH studies, no omission, no invented enrollment
    ("what study are we doing related to alzheimers", [has("prot-2023-022", "prot-2023-041")]),
    # study→PI and PI→studies (indirect-phrasing class)
    ("who is leading the donanemab trial",        [has("susan cole"), used("query_trial_operations")]),
    ("what is Dr. Raj Patel researching",         [has("prot-2023-007"), used("query_trial_operations")]),
    # ROUTING: bare count → internal; condition count → public
    ("how many studies are recruiting",           [used("query_trial_operations"), not_used("clinicaltrials")]),
    ("how many recruiting trials are there for pancreatic cancer", [used("clinicaltrials")]),
    # worst enrollment = fill rate (PROT-2024-018 @ 46%), not raw count
    ("which study has the worst enrollment",      [has("prot-2024-018")]),
    # cross-source: our 61 kept separate; both tools fire
    ("how does our enrollment on the donanemab trial compare to the field",
                                                  [has("61"), used("query_trial_operations"), used("clinicaltrials")]),
    # out-of-schema → declines, never fabricates studies
    ("what is our trial budget",                  [absent("study 1", "prot-2023")]),
    # CHART requests → route to the visualization tool (not the text query tool)
    ("chart our enrollment by site",              [used("plot_trial_operations")]),
    ("show the donanemab enrollment trend over time", [used("plot_trial_operations")]),
    # a plain (non-chart) internal question must NOT route to the chart tool
    ("how is enrollment tracking across our trials", [used("query_trial_operations"), not_used("plot")]),
]


def _eval(kind, arg, answer_l, tools):
    if kind == "contains":
        miss = [s for s in arg if s not in answer_l]
        return (not miss, f"contains {list(arg)}" + (f" — MISSING {miss}" if miss else ""))
    if kind == "absent":
        bad = [s for s in arg if s in answer_l]
        return (not bad, f"absent {list(arg)}" + (f" — FOUND {bad}" if bad else ""))
    if kind == "used":
        return (any(arg in t for t in tools), f"used ~{arg} (tools={tools})")
    if kind == "not_used":
        return (not any(arg in t for t in tools), f"not_used ~{arg} (tools={tools})")
    return (False, f"unknown check {kind}")


async def _invoke_with_retry(agent, q, tries=4):
    # gpt-4o (SQL/chart gen) has a 30k TPM cap; back off on 429 so rate limits
    # don't look like regressions.
    for i in range(tries):
        try:
            return await agent.ainvoke({"messages": [HumanMessage(content=q)]},
                                       config={"recursion_limit": 6})
        except Exception as e:
            if i < tries - 1 and ("rate_limit" in str(e).lower() or "429" in str(e)):
                await asyncio.sleep(8 * (i + 1))
                continue
            raise


async def main() -> int:
    agent = await build_agent()
    fails = []
    for q, checks in CASES:
        try:
            r = await _invoke_with_retry(agent, q)
            tools = [tc["name"] for m in r["messages"] if isinstance(m, AIMessage)
                     for tc in (m.tool_calls or [])]
            answer = ""
            for m in r["messages"]:
                if isinstance(m, AIMessage) and isinstance(m.content, str) and m.content.strip():
                    answer = m.content
            results = [_eval(k, a, answer.lower(), tools) for k, a in checks]
        except Exception as e:  # pragma: no cover
            results = [(False, f"ERROR {e}")]
        ok = all(r[0] for r in results)
        if not ok:
            fails.append(q)
        print(f"[{'PASS' if ok else 'FAIL'}] {q}")
        for passed, label in results:
            if not passed:
                print(f"         ↳ {label}")

    print(f"\n{len(CASES) - len(fails)}/{len(CASES)} passed.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
