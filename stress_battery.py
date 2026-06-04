"""
Throwaway stress battery for the multi-capability agent (3 sources + charts).
Runs diverse edge-case / multi-source / routing / follow-up / adversarial queries
through the REAL agent, writing structured results to stress_results.txt for review.
Throttled + 429-retry for the gpt-4o 30k TPM cap. Run in the background.
"""

import asyncio
from langchain_core.messages import AIMessage, HumanMessage
from research_agent import build_agent

SINGLE = [
    # ── multi-source composition (the hero) ──
    ("multi-3src", "How does our donanemab enrollment compare to the competitive landscape, and what does recent literature say?"),
    ("multi-chart+lit", "For our pembrolizumab NSCLC trial, show me our enrollment trend and the latest published evidence."),
    ("multi-cross", "Compare our semaglutide cardiovascular trial to the field and chart our enrollment by site."),
    # ── chart edge cases ──
    ("chart-vague", "chart our enrollment"),
    ("chart-filtered", "show me a graph of our recruiting studies by therapeutic area"),
    ("chart-1trial-funnel", "visualize the screening funnel for the donanemab trial"),
    ("chart-subset", "plot our completed studies and their enrollment"),
    ("chart-multitrend", "chart the enrollment trend for our two Alzheimer's studies"),
    ("chart-byphase", "graph our portfolio by phase"),
    ("chart-pie-req", "show me a pie chart of enrollment by site"),
    ("chart-region", "visualize enrollment by region"),
    # ── time-series / current confusion ──
    ("ts-velocity", "what's our enrollment velocity over the last 6 months?"),
    ("ts-fastest", "which of our studies is enrolling the fastest?"),
    # ── routing ambiguity ──
    ("route-pub-count", "how many trials are there for Alzheimer's disease?"),
    ("route-int-count", "how many Alzheimer's trials do we have?"),
    ("route-newpi", "what is Dr. Priya Nair working on?"),
    ("route-named-trial", "tell me about the tezepelumab trial"),
    ("route-intl-site", "what's happening at our London site?"),
    # ── adversarial / out-of-schema / grounding traps ──
    ("oos-budget", "what's the budget for our donanemab trial?"),
    ("oos-phi", "which patients dropped out of our trials?"),
    ("oos-chart-budget", "chart our trial budgets by study"),
    ("oos-stats", "what's the p-value for our semaglutide results?"),
    ("oos-demographics", "give me a chart of patient demographics across our trials"),
    # ── compound / analytical ──
    ("cmp-briefing", "give me a status briefing on our oncology portfolio"),
    ("cmp-pi-load", "which of our PIs is running the most studies, and what are they?"),
    ("cmp-two-alz", "compare enrollment between our two Alzheimer's trials"),
    ("cmp-strong-weak", "which therapeutic areas are we strongest and weakest in by enrollment?"),
    # ── capability / scope ──
    ("cap-now", "what can you do?"),
    ("cap-charts", "can you make charts of our data?"),
    # ── stress / weird ──
    ("weird-broad", "chart everything about our portfolio"),
]

FOLLOWUPS = [
    ("fu-chart-it", ["which of our studies has the worst enrollment?",
                     "chart its enrollment trend over time"]),
    ("fu-route-shift", ["find recruiting Phase 3 trials for NSCLC",
                        "do we run any NSCLC trials ourselves?"]),
    ("fu-pivot", ["how is enrollment tracking across our trials?",
                  "now show that as a chart"]),
]


async def _invoke(agent, msgs):
    for i in range(5):
        try:
            return await agent.ainvoke({"messages": msgs}, config={"recursion_limit": 6})
        except Exception as e:
            if "rate_limit" in str(e).lower() or "429" in str(e):
                await asyncio.sleep(12 * (i + 1))
                continue
            return {"error": repr(e)}
    return {"error": "rate_limit_exhausted"}


def _summ(r):
    if "error" in r:
        return [], 0, r["error"], ""
    msgs = r["messages"]
    tools = [tc["name"] for m in msgs if isinstance(m, AIMessage) for tc in (m.tool_calls or [])]
    charts = tools.count("plot_trial_operations")
    answer = ""
    for m in msgs:
        if isinstance(m, AIMessage) and isinstance(m.content, str) and m.content.strip():
            answer = m.content
    return tools, charts, None, answer


async def main():
    agent = await build_agent()
    with open("stress_results.txt", "w", encoding="utf-8") as out:
        def log(s):
            out.write(s + "\n"); out.flush()

        log("=== SINGLE-TURN ===\n")
        for cat, q in SINGLE:
            r = await _invoke(agent, [HumanMessage(content=q)])
            tools, charts, err, ans = _summ(r)
            log(f"### [{cat}] {q}")
            log(f"   tools={tools} charts={charts} err={err}")
            log(f"   answer: {ans[:380].replace(chr(10), ' ')}")
            log("")
            await asyncio.sleep(7)

        log("\n=== FOLLOW-UPS ===\n")
        for cat, turns in FOLLOWUPS:
            msgs = []
            for ti, q in enumerate(turns):
                msgs.append(HumanMessage(content=q))
                r = await _invoke(agent, msgs)
                if "messages" in r:
                    msgs = r["messages"]
                tools, charts, err, ans = _summ(r)
                log(f"### [{cat} t{ti+1}] {q}")
                log(f"   tools={tools} charts={charts} err={err}")
                log(f"   answer: {ans[:380].replace(chr(10), ' ')}")
                log("")
                await asyncio.sleep(7)
        log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
