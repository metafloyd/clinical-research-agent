"""
ROUTING RED-TEAM — aggressively try to MIS-ROUTE the agent, then validate the answer
against GROUND TRUTH computed from the seeded SQLite DB.

Unlike stress_redteam.py (log-only, manual inspection), every case here is a hard
PASS/FAIL assertion on BOTH:
  (a) which tool fired   — the routing decision (internal vs public vs chart vs none)
  (b) the answer content — checked against deterministic ground truth where the data
                           is internal (so a mis-route OR a fabrication both fail)

This is the demo gate: a green run means the routing logic holds under adversarial,
ambiguous, and trap phrasings. Run:  .venv\\Scripts\\python.exe stress_routing.py
Non-zero exit on any failure. Throttled + 429-retry (gpt-4o NL→SQL has a 30k TPM cap).

GROUND TRUTH (recomputed from trial_ops.db on 2026-06-11):
  22 studies · 15 active (status != Completed) · 7 completed · 8 recruiting · 16 Phase 3
  donanemab (PROT-2023-022 / NCT04437511) = 61/75
  worst study by fill rate = PROT-2024-018 @ 46%  ·  worst site = London @ 53%
  total current enrolled = 1431  ·  7 sites  ·  Dr. Helen Park leads 4 studies
"""

import asyncio
import sys

from langchain_core.messages import AIMessage, HumanMessage
from research_agent import build_agent

# ── check DSL (evaluated against answer_lower + tool_names) ─────────────────────
def has(*subs):       return ("contains", subs)      # ALL substrings must be present
def has_any(*subs):   return ("contains_any", subs)  # at least ONE must be present
def absent(*subs):    return ("absent", subs)        # NONE may be present
def used(sub):        return ("used", sub)           # a tool whose name contains `sub` fired
def not_used(sub):    return ("not_used", sub)       # no tool name contains `sub`

# Tool-name fragments:  query_trial_operations · plot_trial_operations · clinicaltrials · pubmed

# (label, question, [checks]) — each probes a specific routing trap.
CASES = [
    # ───────── INTERNAL must fire, PUBLIC must NOT (the #1 demo risk) ─────────
    ("int-tracking", "how are we tracking against our enrollment targets",
        [used("query_trial_operations"), not_used("clinicaltrials")]),
    ("int-worst-site", "which of our sites is furthest behind its target",
        [used("query_trial_operations"), not_used("clinicaltrials"), has("london")]),
    ("int-total-enr", "how many patients have we enrolled across all our studies in total",
        [used("query_trial_operations"), has_any("1431", "1,431"), not_used("clinicaltrials")]),
    ("int-donanemab", "what is our current enrollment on the donanemab study",
        [used("query_trial_operations"), has("61"), not_used("plot")]),
    ("int-pi", "what is Dr. Helen Park working on",
        [used("query_trial_operations"), not_used("clinicaltrials")]),
    ("int-bare-count", "how many studies are we running",
        [used("query_trial_operations"), not_used("clinicaltrials")]),
    ("int-onc-list", "list our oncology trials",
        [used("query_trial_operations"), not_used("clinicaltrials")]),
    ("int-sites-count", "how many sites do we have",
        [used("query_trial_operations"), not_used("plot")]),
    ("int-worst-study", "what is our worst performing study by enrollment",
        [used("query_trial_operations"), has("prot-2024-018"), not_used("plot")]),
    ("int-recruiting", "how many of our trials are still recruiting",
        [used("query_trial_operations"), not_used("clinicaltrials")]),

    # ───────── PUBLIC must fire, INTERNAL must NOT ─────────
    ("pub-panc", "how many recruiting trials are there for pancreatic cancer",
        [used("clinicaltrials"), not_used("query_trial_operations")]),
    ("pub-ms", "find recruiting trials for multiple sclerosis",
        [used("clinicaltrials"), not_used("query_trial_operations")]),
    ("pub-alz-world", "how many alzheimer's disease trials are there worldwide",
        [used("clinicaltrials"), not_used("query_trial_operations")]),

    # ───────── CHART must fire (visualization tool, not the text tool) ─────────
    ("chart-area", "visualize our portfolio by therapeutic area",
        [used("plot_trial_operations")]),
    ("chart-site", "chart our enrollment by site",
        [used("plot_trial_operations")]),
    ("chart-trend", "graph the donanemab enrollment trend over time",
        [used("plot_trial_operations")]),
    ("chart-pie", "break down our studies by phase as a pie chart",
        [used("plot_trial_operations")]),
    ("chart-plot-verb", "plot how many patients we have at each site",
        [used("plot_trial_operations")]),
    # FORECAST (no chart verb) must route to the projection chart, NOT the text tool, and
    # the verdict must match the deterministic projection (donanemab lands ~71 vs 75 = behind).
    ("chart-forecast-behind", "will the donanemab trial hit its target by its planned end date",
        [used("plot_trial_operations"), has_any("71", "fall short", "behind", "short of"),
         absent("likely to hit", "on track to meet")]),
    ("chart-forecast-pace", "is our lecanemab study on pace to finish enrollment",
        [used("plot_trial_operations")]),
    # CONTROL: a CURRENT-status question (no forecast wording) stays TEXT, not the chart.
    ("int-current-status", "are we currently behind target on donanemab",
        [used("query_trial_operations"), not_used("plot")]),

    # ───────── NO-TOOL: concept / greeting must not hit a data tool ─────────
    ("none-concept", "what is a phase 3 clinical trial",
        [not_used("query_trial_operations"), not_used("clinicaltrials"), not_used("plot")]),
    ("none-greet", "hello there",
        [not_used("query_trial_operations"), not_used("clinicaltrials"),
         not_used("pubmed"), not_used("plot")]),

    # ───────── OFF-TOPIC must decline, no tools ─────────
    ("oos-weather", "what's the weather in Rochester today",
        [not_used("query_trial_operations"), not_used("clinicaltrials"), not_used("plot")]),

    # ───────── CROSS-SOURCE: both internal AND public, kept separate ─────────
    ("cross-vs-global", "compare our donanemab accrual against its global registered enrollment",
        [used("query_trial_operations"), used("clinicaltrials"), has("61")]),

    # ───────── ADVERSARIAL routing traps ─────────
    # "the <drug> trial" we actually run → internal-first (memory flags this borderline).
    ("adv-tezep", "tell me about our tezepelumab trial",
        [used("query_trial_operations")]),
    # "our" + a drug we do NOT run → must NOT fabricate; say we don't have it.
    ("adv-fake-drug", "how is our aspirin trial enrolling",
        [used("query_trial_operations"),
         has_any("don't", "do not", "no aspirin", "not have", "doesn't", "no matching",
                 "isn't", "couldn't", "no internal", "not currently")]),
    # text question must NOT escalate to a chart just because it's about numbers.
    ("adv-no-chart", "what is our total enrollment number",
        [used("query_trial_operations"), not_used("plot")]),
]


def _eval(kind, arg, answer_l, tools):
    if kind == "contains":
        miss = [s for s in arg if s not in answer_l]
        return (not miss, f"contains {list(arg)}" + (f" — MISSING {miss}" if miss else ""))
    if kind == "contains_any":
        ok = any(s in answer_l for s in arg)
        return (ok, f"contains_any {list(arg)}" + ("" if ok else " — NONE present"))
    if kind == "absent":
        bad = [s for s in arg if s in answer_l]
        return (not bad, f"absent {list(arg)}" + (f" — FOUND {bad}" if bad else ""))
    if kind == "used":
        return (any(arg in t for t in tools), f"used ~{arg} (tools={tools})")
    if kind == "not_used":
        return (not any(arg in t for t in tools), f"not_used ~{arg} (tools={tools})")
    return (False, f"unknown check {kind}")


async def _invoke_with_retry(agent, q, tries=5):
    for i in range(tries):
        try:
            return await agent.ainvoke({"messages": [HumanMessage(content=q)]},
                                       config={"recursion_limit": 6})
        except Exception as e:
            if i < tries - 1 and ("rate_limit" in str(e).lower() or "429" in str(e)):
                await asyncio.sleep(10 * (i + 1))
                continue
            raise


async def main() -> int:
    agent = await build_agent()
    fails = []
    for label, q, checks in CASES:
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
            answer = ""
        ok = all(res[0] for res in results)
        if not ok:
            fails.append(label)
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: {q}")
        for passed, lbl in results:
            if not passed:
                print(f"         ↳ {lbl}")
        if not ok:
            print(f"         answer: {answer[:200].replace(chr(10), ' ')}")
        await asyncio.sleep(3)   # ease the gpt-4o TPM cap

    print(f"\n{len(CASES) - len(fails)}/{len(CASES)} passed.")
    if fails:
        print("FAILED:", ", ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
