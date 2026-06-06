"""
Two-class stress battery for the CLARIFICATION / ambiguity workstream (Workstream D).
Goal: surface where the agent GUESSES today, so we can calibrate a decline/assume/clarify
triage to REAL failures (not imagined ones). Writes stress_clarify_results.txt.

Class A — adversarial-plot: hard / out-of-schema / edge chart requests. Probes the chart
          spec DIRECTLY (_generate_chart_spec) so we see exactly what the model chose
          (sql + chart_type), which reveals a sensible-default vs a wrong/fabricated guess.
Class B — layman-ambiguous: vague / underspecified NL queries run through the FULL agent
          (routing + synthesis matter here), capturing tools + answer.

For each case we record an ANNOTATION of the IDEAL behavior (decline / assume+state /
clarify) so categorization in step 2 is grounded. Throttled + 429-retry (gpt-4o 30k TPM).
Run in the background.
"""

import asyncio
from langchain_core.messages import AIMessage, HumanMessage
import trial_ops as t
from config import TRIAL_OPS_DB_PATH
from research_agent import build_agent

# ── Class A: adversarial / ambiguous PLOT requests ──────────────────────────────
# (label, query, ideal-behavior note)
PLOT = [
    # -- out-of-schema (should DECLINE — we have no such data) --
    ("oos-budget",        "chart our trial budgets by study",                 "DECLINE (no $ data)"),
    ("oos-demographics",  "show me a chart of patient demographics",          "DECLINE (no demographics)"),
    ("oos-pvalue",        "plot the p-values of our trial outcomes",          "DECLINE (no stats/outcomes)"),
    ("oos-ae",            "graph adverse events across our studies",          "DECLINE (no safety data)"),
    ("oos-cost-site",     "visualize cost per patient by site",               "DECLINE (no cost data)"),
    ("oos-staff",         "chart how many coordinators each site has",        "DECLINE (no staffing data)"),
    # -- undefined METRIC (genuinely ambiguous → maybe CLARIFY) --
    ("amb-performance",   "chart our performance",                            "CLARIFY/ASSUME (perf=enrollment? fill rate? completion?)"),
    ("amb-howdoing",      "show me how we're doing as a graph",               "CLARIFY/ASSUME (metric undefined)"),
    ("amb-success",       "visualize the success of our trials",              "CLARIFY/ASSUME (success undefined)"),
    ("amb-progress",      "plot our progress",                                "CLARIFY/ASSUME (progress=enrollment vs target? time?)"),
    # -- unbounded SCOPE (→ ASSUME a sensible view + state, or CLARIFY) --
    ("scope-everything",  "chart everything about our portfolio",             "ASSUME a default breakdown + state (or CLARIFY which)"),
    ("scope-all-data",    "graph all our data",                               "ASSUME/CLARIFY"),
    ("scope-overview",    "give me a visual overview",                        "ASSUME a portfolio breakdown + state"),
    # -- snapshot vs TREND ambiguity (→ ASSUME current snapshot + state) --
    ("st-enroll-site",    "chart our enrollment by site",                     "ASSUME current snapshot (bar) + state; clear enough"),
    ("st-enrollment",     "chart our enrollment",                             "ASSUME a default (by study/site) + state"),
    ("st-bare-trend",     "show the trend",                                   "CLARIFY/ASSUME (trend of WHAT?)"),
    # -- chart TYPE mismatch (data shape vs requested type) --
    ("type-pie-trend",    "show a pie chart of our enrollment over time",     "ASSUME sensible (trend=line, not pie) or note mismatch"),
    ("type-funnel-area",  "make a funnel of our therapeutic areas",           "ASSUME/handle (areas aren't a funnel)"),
    # -- typo / fuzzy entity --
    ("fuzzy-typo",        "chart enrollement for donanemab",                  "ASSUME (tolerate typo) + render"),
    ("fuzzy-unknown",     "chart enrollment for our XYZ-999 trial",           "NO-DATA gracefully (unknown study)"),
    # -- clearly fine (CONTROL — must NOT clarify/decline; render cleanly) --
    ("ok-bysite",         "chart our enrollment by site",                     "RENDER (control — no question)"),
    ("ok-byarea",         "visualize our portfolio by therapeutic area",      "RENDER (control)"),
    ("ok-funnel",         "show our recruitment funnel",                      "RENDER (control)"),
    ("ok-trend",          "show the enrollment trend over time",              "RENDER (control)"),
    ("ok-byphase",        "graph our studies by phase",                       "RENDER (control)"),
]

# ── Class B: layman / ambiguous NL queries (full agent) ─────────────────────────
LAYMAN = [
    ("lay-doing",         "how are we doing?",                                "CLARIFY/ASSUME (scope: portfolio? a study?)"),
    ("lay-status",        "give me an update",                                "ASSUME a portfolio briefing or CLARIFY"),
    ("lay-best",          "what's our best trial?",                           "CLARIFY/ASSUME (best by what?)"),
    ("lay-problems",      "any problems?",                                    "ASSUME enrollment-behind flags or CLARIFY"),
    ("lay-trials",        "tell me about the trials",                         "CLARIFY/ASSUME (ours? public? which?)"),
    ("lay-alz",           "alzheimer's",                                      "CLARIFY/ASSUME (ours vs public? what about it?)"),
    ("lay-numbers",       "what are the numbers?",                            "CLARIFY (numbers of what?)"),
    ("lay-compare",       "how do we compare?",                               "CLARIFY (compare what, to what?)"),
    ("lay-behind",        "are we behind?",                                   "ASSUME enrollment-vs-target or CLARIFY"),
    ("lay-summary",       "summarize",                                        "CLARIFY/ASSUME (summarize what?)"),
    # clearly-fine controls (must NOT over-ask)
    ("lay-ok-count",      "how many active studies do we have?",              "ANSWER (control — clear)"),
    ("lay-ok-pi",         "what is Dr. Priya Nair working on?",               "ANSWER (control — clear)"),
    ("lay-ok-cmp",        "how does our donanemab enrollment compare to the field?", "ANSWER (control — clear)"),
]


async def _spec_with_retry(q):
    for i in range(5):
        try:
            return await t._generate_chart_spec(q)
        except Exception as e:
            if "rate_limit" in str(e).lower() or "429" in str(e):
                await asyncio.sleep(12 * (i + 1)); continue
            return {"_error": repr(e)}
    return {"_error": "rate_limit_exhausted"}


async def _invoke_with_retry(agent, msgs):
    for i in range(5):
        try:
            return await agent.ainvoke({"messages": msgs}, config={"recursion_limit": 6})
        except Exception as e:
            if "rate_limit" in str(e).lower() or "429" in str(e):
                await asyncio.sleep(12 * (i + 1)); continue
            return {"error": repr(e)}
    return {"error": "rate_limit_exhausted"}


def _summ(r):
    if "error" in r:
        return [], r["error"], ""
    msgs = r["messages"]
    tools = [tc["name"] for m in msgs if isinstance(m, AIMessage) for tc in (m.tool_calls or [])]
    answer = ""
    for m in msgs:
        if isinstance(m, AIMessage) and isinstance(m.content, str) and m.content.strip():
            answer = m.content
    return tools, None, answer


async def main():
    t.ensure_db(TRIAL_OPS_DB_PATH)
    with open("stress_clarify_results.txt", "w", encoding="utf-8") as out:
        def log(s):
            out.write(s + "\n"); out.flush()

        log("=== CLASS A: ADVERSARIAL / AMBIGUOUS PLOT (chart-spec probe) ===")
        log("    Shows what the chart-spec model CHOSE. chart_type='none' = decline.\n")
        for cat, q, ideal in PLOT:
            spec = await _spec_with_retry(q)
            ctype = spec.get("chart_type", "?")
            sql = (spec.get("sql") or "").replace(chr(10), " ")
            log(f"### [{cat}] {q}")
            log(f"   IDEAL: {ideal}")
            log(f"   chart_type={ctype!r}  title={spec.get('title','')!r}")
            log(f"   sql: {sql[:240]}")
            if spec.get("_error"):
                log(f"   ERROR: {spec['_error']}")
            log("")
            await asyncio.sleep(6)

        log("\n=== CLASS B: LAYMAN / AMBIGUOUS NL (full agent) ===\n")
        agent = await build_agent()
        for cat, q, ideal in LAYMAN:
            r = await _invoke_with_retry(agent, [HumanMessage(content=q)])
            tools, err, ans = _summ(r)
            log(f"### [{cat}] {q}")
            log(f"   IDEAL: {ideal}")
            log(f"   tools={tools} err={err}")
            log(f"   answer: {ans[:420].replace(chr(10), ' ')}")
            log("")
            await asyncio.sleep(7)

        log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
