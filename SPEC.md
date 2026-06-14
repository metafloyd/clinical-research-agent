# Clinical Research Assistant — Solution Specification

> **Purpose of this document.** A single source of truth for *what the system is, how it
> works, and where its edges are*. It serves three audiences: (1) anyone onboarding to the
> codebase, (2) a reusable **solution-design / SOW template** for similar agentic builds, and
> (3) the backbone for the solution walkthrough deck. It is deliberately honest about
> non-goals and limitations — the design philosophy is **predictable behavior > features**.

---

## 1. Overview

**What it is.** A conversational clinical-research intelligence assistant that answers
questions spanning three normally-siloed sources in one turn:

- our **private trial operations** (internal CTMS — enrollment, sites, study status),
- the **public trial registry** (ClinicalTrials.gov),
- the **published literature** (PubMed / EuropePMC),

and renders **live charts** of the internal portfolio — engineered so that it **does not
fabricate**: it answers only from retrieved data and declines cleanly when asked for
something it doesn't have.

**Who it's for.** Investigators, research coordinators, and clinicians.

**Why it's differentiated.** A general chatbot has neither the private data nor the refusal
discipline. The value is *grounded cross-source synthesis* in a regulated domain where a
fabricated number is a dealbreaker.

**Current status.** Live on Render; demo-ready; ~94 automated regression assertions gate
every change.

---

## 2. Capabilities

| # | Capability | Tool | Example question |
|---|---|---|---|
| 1 | **Internal portfolio** (NL→SQL over our CTMS) | `query_trial_operations` | "Which of our studies is furthest behind on enrollment?" |
| 2 | **Public trial landscape** | `clinicaltrials_*` (MCP) | "How many Alzheimer's trials are recruiting nationally?" |
| 3 | **Published evidence** | `pubmed_* / europepmc_*` (MCP) | "What does recent literature say about donanemab?" |
| 4 | **Live charts of our data** | `plot_trial_operations` | "Chart our studies ranked by fill rate" |
| — | **Cross-source synthesis** | (1) + (2)/(3) in one turn | "How does our donanemab enrollment compare to the field?" |

**Chart features:** bar / line / donut / funnel; **RAG (red/amber/green) fill-rate coloring**;
a **100% target reference line**; and **deterministic enrollment projection** ("will this
study hit its target by its planned end date?" → velocity forecast + on-pace/behind verdict).

---

## 3. Architecture

```
User ──▶ Chainlit (React UI + FastAPI/Starlette backend, auth, sessions, streaming)
            │
            ▼
     LangGraph ReAct agent  (single path, model-routed)
     create_react_agent(model, tools, prompt=SYSTEM_PROMPT)
     reason → act (call a tool) → observe → repeat   (hard cap: 6 steps)
            │
            ├─ MCP CLIENT ──▶ community MCP servers (HTTP):
            │                   • ClinicalTrials.gov   • PubMed / EuropePMC
            │
            └─ LOCAL TOOLS:
                 • query_trial_operations  (NL→SQL over local SQLite CTMS)
                 • plot_trial_operations   (NL→chart-spec → Plotly figure)
```

**Key design decisions (and the rationale):**

- **Single ReAct agent, model-routed.** The model decides which tool(s) to call, guided by
  routing rules in the system prompt. An earlier regex+classifier router was **removed** — it
  was brittle and once caused a hallucination. *A capable model + a clear prompt beats
  hand-written routing code.*
- **Two-model split.** Main agent (routing + synthesis) = **gpt-4o-mini** (cheap, fast,
  reliable tool-calling, emits no inter-tool prose → enables clean token streaming).
  NL→SQL **and** chart-spec generation = **gpt-4o** (text-to-SQL is the step a small model was
  genuinely weak at — upgrading *just that step* ended a long tail of false "no match" errors).
- **MCP as the tool standard.** The agent is an MCP *client*; external sources are MCP
  *servers* (deterministic API wrappers — **no LLM on the server side**). The internal CTMS
  tool is built in-house, not via MCP, because it lives in our own process.
- **Streaming hygiene.** The UI streams only the main agent node's tokens
  (`langgraph_node == "agent"`), so the nested NL→SQL model call inside a tool doesn't leak
  raw SQL into the chat.
- **Context window.** Only the last 6 messages are kept in context — payload discipline, not
  the 128k window, is the real constraint.

---

## 4. Data model (internal CTMS)

Local **SQLite** (`trial_ops.db`), **read-only**, **synthetic** (no PHI), regenerated
deterministically on boot from an embedded seed. Chosen over hosted Postgres for demo
reliability (no connection/pooler/IPv6 failure surface). **~22 studies** across ~9 therapeutic
areas, **7 sites**, monthly cumulative enrollment.

| Table | Grain | Key columns |
|---|---|---|
| `studies` | one row per study | `internal_id` (PROT-…), `nct_id` (links to ClinicalTrials.gov; nullable), `title`, `therapeutic_area`, `phase`, `status`, `principal_investigator`, `target_enrollment`, `enrollment_open_date`, `planned_end_date`, `sponsor_type` |
| `sites` | one row per site | `site_id`, `site_name`, `city`, `state`, `country`, `region`, `site_type` |
| `enrollment` | study × site × month | `as_of_date`, `screened`, `enrolled`, `randomized`, `completed`, `withdrawn`, `target`, `status` |
| `enrollment_current` (view) | latest snapshot per study × site | = current state |

`studies.nct_id` is the join key that makes "our accrual vs. the public landscape" possible.

---

## 5. Tool contracts

**`query_trial_operations(question: str)` — NL→SQL over the CTMS**
- One gpt-4o pass turns English → a single SQLite `SELECT` against an **embedded schema +
  curated few-shot Q→SQL examples** (each observed bug becomes one example — the tuning lever).
- **Single round-trip** (no list-tables→schema→query toolkit dance — round-trips were the #1
  latency driver). One **self-correction retry** on execution error.
- Returns a **grounded, structured** result: the formatter declares which columns the rows
  actually contain and names the top-ranked row, so the agent can't invent fields or re-rank.

**`plot_trial_operations(question: str)` — NL→chart**
- gpt-4o emits a chart spec (`chart_type`, `sql`, `x`, `y`, optional `note` / `projection`).
- `_build_figure` renders Plotly: many-category bars flip horizontal; **fill-rate bars get
  RAG colors + a 100% target line**; forecast questions get a **deterministic velocity
  projection** (math in code, never the model — so the verdict can't be fabricated).

**MCP tools** (`clinicaltrials_*`, `pubmed_* / europepmc_*`) — community-hosted API wrappers.
Always requested with a **lean field set** (only what's rendered) to bound tokens.

---

## 6. Trust & guardrails (the core engineering)

The hardest problem in a regulated domain isn't capability — it's **not making things up**.
Mechanisms, in the order of the escalation ladder we learned the hard way:

1. **Grounding rules** (`_GROUNDING` in the prompt) — state only retrieved values; "Not
   specified" for missing fields; no superlatives about un-retrieved data.
2. **Structure beats prose.** When a prompt rule fails under load, the guard moves *into the
   tool output* (e.g., the tool declares which columns exist, so the model has no blank to
   fill). Directives in tool results beat directives in the system prompt.
3. **Name the answer.** For ranking questions, the tool output names the specific top-ranked
   row, so the model can't re-rank by a salient-but-wrong column.
4. **Hard caps > prompt rules.** Cost/latency runaways are bounded in **code**, not requests:
   `recursion_limit=6` (max agent loop steps) + an 8,000-char cap per tool result.
5. **Read-only, defense-in-depth.** NL→SQL is guarded by both a parser check (SELECT/WITH
   only, no DDL/DML, no `;`-chaining) **and** a `PRAGMA query_only` / `mode=ro` connection.
6. **Safety-rule position.** The prompt-injection override sits at the *top* of the system
   prompt (early instructions carry more weight).
7. **Clean refusal.** Out-of-schema questions (budget, demographics, adverse events) decline
   rather than substitute a different column.

---

## 7. Quality & evaluation

**Regression harnesses** (run before any change; non-zero exit gates a deploy):

| Suite | Cases | Layer | What it catches |
|---|---|---|---|
| `smoke_ctms.py` | 38 | NL→SQL tool | data-vs-decline, correct SQL across counts/lookups/rankings |
| `smoke_agent.py` | 13 | full agent | rendered-answer fabrication + tool routing |
| `stress_routing.py` | 29 | routing + ground truth | mis-routing & content vs. deterministic ground truth |
| `smoke_projection.py` | 14 | chart math (offline) | forecast math + chart overlay + fill-rate normalization |

Plus `stress_battery.py` / `stress_clarify.py` / `stress_redteam.py` (broad/adversarial).

**Evals** (`evals.py`, LangSmith): `groundedness` (LLM-as-judge, gpt-4o), `no_overcalling`,
`correct_scope`. Caught a real hallucination pre-ship (groundedness 0.80 → 1.00 after the fix).

**Observability:** LangSmith tracing on every run — per-step latency/tokens (this is how the
latency bottleneck was correctly diagnosed as tool-output size, not model speed).

**Ground-truth anchors** (recomputed from the seed): 22 studies · 15 active · 7 completed ·
8 recruiting · donanemab 61/75 · worst study ~46% · total enrolled 1431 · 7 sites.

---

## 8. Tech stack & deployment

| Layer | Choice | Notes |
|---|---|---|
| UI + backend | **Chainlit 2.11** | a pre-assembled React + FastAPI chat app, configured via Python callbacks |
| Agent | **LangGraph** `create_react_agent` | single ReAct path |
| Models | **OpenAI** gpt-4o-mini (main) + gpt-4o (NL→SQL, chart specs) | no per-request token ceiling, reliable tool-calling |
| Tools | **MCP** (langchain-mcp-adapters) + local Python tools | 2 community MCP servers + 2 in-house tools |
| Internal data | **SQLite** (synthetic, read-only) | regenerated on boot; gitignored |
| Persistence | **Supabase** (Postgres + Storage) | Chainlit native data layer + custom storage client for Plotly charts |
| Auth | **Google OAuth** | beta whitelist |
| Hosting | **Render** (web service) | auto-deploy on push to `main` |
| Tracing | **LangSmith** | per-step latency/token traces |

---

## 9. Non-goals (deliberate)

- **No multi-agent / planner-sub-agent ("deep agent") orchestration** — four tools and
  single-domain Q&A don't warrant it.
- **No multimodality** — text in / text out (with generated charts); no image upload.
- **No heavy text-to-SQL platform** (Vanna / WrenAI / SQLDatabaseToolkit) — overkill for three
  fixed tables; the toolkit's multi-round-trip flow would add latency.
- **No write access to any data source** — read-only by construction.
- **Features that hurt predictability are cut**, not shipped (e.g., a follow-up-suggestion
  feature was removed when it leaked raw markup).

---

## 10. Known limitations (honest)

- **Internal data is synthetic** (the public sources are live). The pattern drops onto a real
  CTMS unchanged.
- **MCP servers are community-hosted** — we don't control their uptime/versioning/freshness.
- **Enrollment projection is a linear (constant-velocity) extrapolation** — it answers "if the
  current pace holds," not a saturation-aware forecast; it correctly declines for
  completed/past-end studies.
- **One documented adversarial residual** — a contrived "combined total of our N + registry N"
  phrasing can still produce a meaningless sum; the real "vs the field" path is unaffected.
- **Latency** — heavy multi-source queries run ~10–25s (OpenAI throughput × output size);
  mitigated by lean payloads, compact output, streaming, and cache warming.

---

## 11. PoC → production roadmap

A PoC proves the concept works *and is trustworthy*; production is mostly the scaffolding
*around* the agent. The agent core (LangGraph + tools + guardrails + harness) largely carries
over; the lift is:

| Dimension | Today (PoC) | Production |
|---|---|---|
| **Data** | synthetic SQLite | real CTMS/warehouse + schema retrieval + semantic layer |
| **Governance** | read-only, synthetic | RBAC, PII masking, row-level security, audit, read-replica |
| **Compliance** | none needed | HIPAA / GxP / 21 CFR Part 11; **no PII to third-party model APIs**; human-in-the-loop |
| **Tools** | community MCP servers | self-hosted / contracted MCP with SLAs |
| **Auth** | Google OAuth | enterprise SSO (SAML/OIDC), secrets vault |
| **Scale** | single Render service | horizontally scaled, load-balanced, rate-limit handling, provider fallback |
| **UI** | Chainlit shell | custom React frontend or embed in the client product |
| **CI/CD** | harnesses run manually | harnesses gate deploys automatically + drift monitoring |

---

*This spec reflects the system as built. Treat code as the ultimate source of truth; update
this document when the architecture changes.*
