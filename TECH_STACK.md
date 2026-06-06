# Tech Stack — Clinical Research Assistant

A reference for the technologies powering this application: what each one is, why it's
here, and how the pieces connect. Grounded in the actual code (`requirements.txt`,
`render.yaml`, module imports) as of HEAD `7125838`.

> **One-line summary:** A Chainlit chat UI fronts a single LangGraph ReAct agent
> (gpt-4o-mini) that reasons over **three live data sources** — ClinicalTrials.gov and
> PubMed (via remote MCP servers) plus an internal CTMS (local SQLite, queried in natural
> language) — and renders **interactive charts** of the internal data. Auth is Google
> OAuth; chat history and chart figures persist in Supabase (Postgres + Storage); it's
> deployed on Render.

---

## 1. The big picture (data flow)

```
                          ┌─────────────────────────────────────────────┐
   Browser (user)         │                  RENDER (host)              │
   ────────────           │                                             │
   Google login  ───────► │  Chainlit (app.py) ── Google OAuth (Authlib)│
        │                 │        │                                    │
   chat message  ───────► │        ▼                                    │
        │                 │  LangGraph ReAct agent  (research_agent.py) │
        │                 │  model = gpt-4o-mini  (routing + synthesis) │
        │                 │        │                                    │
        │                 │        ├─ tool: clinicaltrials_* ──► MCP ───┼──► ClinicalTrials.gov MCP
        │                 │        ├─ tool: pubmed_*          ──► MCP ───┼──► PubMed / EuropePMC MCP
        │                 │        ├─ tool: query_trial_operations ──────┼──► SQLite (trial_ops.db, local)
        │                 │        │        └─ NL→SQL via gpt-4o          │
        │                 │        └─ tool: plot_trial_operations ───────┼──► SQLite → Plotly figure
        │                 │                 └─ chart-spec via gpt-4o      │
   answer + charts ◄──────┤        ▼                                    │
                          │  stream tokens + cl.Plotly element          │
                          │        │                                    │
                          │        ▼                                    │
                          │  persist:  steps/threads ──► Supabase Postgres
                          │            chart figures  ──► Supabase Storage (signed URLs)
                          └─────────────────────────────────────────────┘
                                     │                          │
                              OpenAI API (LLM)          LangSmith (tracing)
```

---

## 2. Core framework & runtime

| Tech | Version | What it is | Why we use it / how it connects |
|---|---|---|---|
| **Python** | 3.x | Language/runtime | Everything runs here; entry point is `app.py`. |
| **Chainlit** | `2.11.0` | Chat-UI framework (ShadCN/Tailwind frontend + Python backend) | The whole app shell: renders the chat, starter prompts, sidebar, streaming, and chart elements (`cl.Plotly`). Hosts the lifecycle hooks (`@cl.on_chat_start`, `@cl.on_message`, `@cl.on_chat_resume`, `@cl.oauth_callback`, `@cl.data_layer`). Started via `start.sh` → `chainlit run app.py`. |
| **LangGraph** | `>=1.2.2` | Agent orchestration (graph of model + tools) | `create_react_agent(model, tools, prompt)` in `research_agent.py` builds a **single-path ReAct agent** — the model decides which tool(s) to call, loops until done. `recursion_limit=6` hard-caps the loop to bound cost/latency. |
| **LangChain Core** | `>=0.3.0` | Message types + tool abstractions | `HumanMessage`/`AIMessage`/`ToolMessage`, and `StructuredTool` (how `query_trial_operations` and `plot_trial_operations` are defined in `trial_ops.py`). |
| **langchain-openai** | `>=0.3.0` | OpenAI ↔ LangChain adapter | `ChatOpenAI(...)` — the LLM client for both the main agent and the NL→SQL/chart-spec model. |

---

## 3. The LLMs (two models, deliberately split)

| Model | Where | Role | Why this model |
|---|---|---|---|
| **gpt-4o-mini** | `research_agent.py` (`MODEL_ID` in `config.py`) | **MAIN agent** — tool routing AND answer synthesis | Cheap + fast; good enough for routing and writing answers when guided by a tight prompt. |
| **gpt-4o** | `trial_ops.py` `_get_sql_model()` (lazy singleton) | **NL→SQL generation** + **chart-spec generation** | gpt-4o-mini was too weak at text-to-SQL (missed simple phrasings → false NO_MATCH/wrong column). gpt-4o is materially better at structured generation. Used only inside the two internal-data tools. |

> **Provider:** OpenAI API (`OPENAI_API_KEY`). Both models are OpenAI; the only reason
> there are two is capability-vs-cost tuning. `MAIN_MAX_TOKENS=2048`, `NLSQL_MAX_TOKENS=256`.

> Note: an earlier Groq experiment was removed from the deploy config — we moved off Groq
> (its per-request token cap was too tight for tool-heavy turns). A stale `GROQ_API_KEY` may
> still linger in a local `.env`; it is unused by the current code path.

---

## 4. The three data sources (the product's core)

### 4a. ClinicalTrials.gov & PubMed — via **MCP** (remote)
| Tech | What it is | How it connects |
|---|---|---|
| **MCP (Model Context Protocol)** | Open standard for exposing tools to an LLM agent | Two hosted MCP servers expose ClinicalTrials.gov and PubMed/EuropePMC as tool sets. |
| **langchain-mcp-adapters** (`>=0.1.0`) | Bridges MCP servers → LangChain tools | `MultiServerMCPClient({...}).get_tools()` in `research_agent.py` fetches the tool list **once per server lifetime** (cached in a module-level singleton to avoid a 15–20s refetch every session). |
| **Endpoints** (`config.py`) | The MCP server URLs | `clinicaltrials.caseyjhand.com/mcp` and `pubmed.caseyjhand.com/mcp` (HTTP, public). |

These give tools like `clinicaltrials_search_studies`, `clinicaltrials_get_study_record`,
`pubmed_europepmc_search`, `pubmed_find_related`, etc. The agent calls them like any other tool.

### 4b. Internal CTMS — local **SQLite** + **NL→SQL** (the differentiator)
| Tech | What it is | How it connects |
|---|---|---|
| **SQLite** (`sqlite3`, stdlib) | Embedded file database | `trial_ops.db` — our synthetic internal trial portfolio (studies, sites, monthly enrollment time series). Path in `config.py` (`TRIAL_OPS_DB_PATH`). Deterministically regenerated on boot via `ensure_db()` (Render's disk is ephemeral — harmless, we only read). |
| **NL→SQL layer** | Custom, in `trial_ops.py` | `query_trial_operations(question)` → gpt-4o turns the NL question into a single read-only `SELECT` against an embedded schema + few-shot examples → executes → returns compact rows. **Read-only enforced two ways:** a keyword guard (`_assert_read_only`) + a `mode=ro` connection with `PRAGMA query_only`. One self-correction retry on error. |

> This composes with the public tools: `studies.nct_id` links our internal record to the
> public ClinicalTrials.gov registry, enabling "our accrual vs. the competitive landscape"
> in one turn.

---

## 5. Data visualization (charts)

| Tech | Version | What it is | How it connects |
|---|---|---|---|
| **Plotly** | `>=5.20` | Interactive charting | `plot_trial_operations(question)` in `trial_ops.py`: gpt-4o produces a **chart spec** (JSON: sql, chart_type, x, y, color, title, optional `note`) → executes the SQL → `_build_figure()` builds a Plotly figure → returned as the tool's artifact. |
| **cl.Plotly** | (Chainlit) | Renders the figure in the message | `app.py`'s `on_tool_end` captures the figure JSON artifact → attaches a `cl.Plotly` element → interactive (hover/zoom) chart in the chat. |

Chart types: **bar** (enrolled vs target), **line** (trend over time), **donut**
(portfolio breakdown), **funnel** (screen→enroll→randomize→complete). The optional `note`
field powers "assume-and-state" — when a request is ambiguous, the chart explains what
default it picked.

> **Historical note:** an earlier static-PNG approach (kaleido, then matplotlib) was tried
> for reload-persistence but reverted because it lost interactivity. The static matplotlib
> version is preserved as git tag `matplotlib-fallback`. Current = interactive Plotly +
> Supabase Storage (see §7).

---

## 6. Authentication

| Tech | What it is | How it connects |
|---|---|---|
| **Google OAuth** | Sign-in provider | `@cl.oauth_callback` in `app.py`. Only emails on `BETA_WHITELIST` (`config.py`) are admitted. The user's Google profile photo + name appear in the header. |
| **Authlib** (`>=1.3.2`) | OAuth library Chainlit uses under the hood | Handles the OAuth handshake. Configured via `OAUTH_GOOGLE_CLIENT_ID` / `OAUTH_GOOGLE_CLIENT_SECRET`. |
| **CHAINLIT_AUTH_SECRET** | JWT signing secret | Signs the session auth token Chainlit issues after login. |

---

## 7. Persistence (Supabase)

Supabase provides **two** distinct services to this app:

| Service | Library | What it stores | How it connects |
|---|---|---|---|
| **Postgres (chat history + analytics)** | `supabase` (`>=2.4.0`), `asyncpg` (`>=0.29.0`) | Chainlit threads/steps (so reloading a chat restores it) + our own `users`/`chat_sessions`/`messages` analytics tables (`db.py`) | Chainlit's native data layer, subclassed as `_PatchedDataLayer` in `app.py` (fixes a threadId backfill bug + caps the sidebar to 5 chats). Connection string = `DATABASE_URL`. `db.py` uses the Supabase client for the analytics tables. |
| **Storage (chart figures)** | `supabase` SDK (Storage API) | Plotly figure JSON for each chart, so charts survive **reload AND deploy** | Custom `_SupabaseStorageClient` (`app.py`) — uploads element content to the `elements` bucket, returns time-limited **signed URLs** (private bucket). Chainlit only persists element content when a `storage_client` is set. |

> **Why a custom storage client?** Chainlit's built-in `S3StorageClient` emits AWS
> virtual-hosted URLs that don't resolve against Supabase Storage (would 404 on reload).
> The custom client uses the Supabase SDK directly + signed URLs. Falls back to a local
> served dir (`_LocalElementStorage`) for dev when the Supabase Storage env vars are absent.

**Storage env vars:** `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_STORAGE_BUCKET=elements`.
(All declared in `render.yaml`; values set in the Render dashboard.)

> **One service-role key, app-wide:** a single `SUPABASE_SERVICE_KEY` is used for BOTH
> Postgres (`db.py`) and Storage (`app.py`) — verified to work for both paths.

---

## 8. Deployment & ops

| Tech | What it is | How it connects |
|---|---|---|
| **Render** | PaaS host | `render.yaml` defines the web service: `pip install -r requirements.txt` → `bash start.sh`. Auto-deploys on push to `main`. (Free tier spins down when idle → first hit after idle is slow; mitigate by warming before a demo.) |
| **start.sh** | Launch script | `chainlit run app.py --port $PORT --host 0.0.0.0`. |
| **python-dotenv** (`>=1.0.0`) | Loads `.env` locally | `load_dotenv()` reads local secrets in dev; Render injects env vars directly in prod. |
| **LangSmith** | LLM tracing/observability | `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY` → every agent run is traced to the `research-assistant` project (used to diagnose latency/routing/groundedness). |
| **GitHub** | Source + CI trigger | `metafloyd/clinical-research-agent`; push to `main` triggers the Render deploy. |

---

## 9. Quality / regression harnesses (not shipped, but part of the stack)

| File | What it checks |
|---|---|
| `smoke_ctms.py` | 38 tool-layer cases — NL→SQL correctness + chart decline/render. |
| `smoke_agent.py` | 13 full-agent cases — routing (internal vs public) + chart routing + counts. |
| `stress_battery.py` | Broad edge/multi-source/adversarial queries (outputs gitignored). |
| `stress_clarify.py` | Ambiguity battery (adversarial-plot + layman-ambiguous) for the clarification/assume-and-state work. |
| `evals.py` | LangSmith eval suite — groundedness (LLM-judge), no-overcalling, correct-scope. |

> Run after any prompt/tool change: `smoke_ctms.py` (38/38) + `smoke_agent.py` (13/13)
> must stay green.

---

## 10. Frontend customization

| File | Purpose |
|---|---|
| `public/custom.css` / `public/custom.js` | Header (3-source badge, Google avatar top-right), sidebar theming, greeting, light-only theme. (Cache-busted with `?v=N` on edits.) |
| `public/theme.json`, `.chainlit/config.toml` | Chainlit theme + app config. |
| `questions.py` | The pool of starter prompts shown on the landing screen. |

---

## Dependency cheat-sheet (`requirements.txt`)

```
chainlit==2.11.0            # chat UI + lifecycle + data layer + cl.Plotly
langgraph>=1.2.2            # ReAct agent orchestration
langchain-core>=0.3.0      # messages + StructuredTool
langchain-openai>=0.3.0    # OpenAI LLM client (gpt-4o-mini + gpt-4o)
langchain-mcp-adapters>=0.1.0  # MCP servers → LangChain tools
python-dotenv>=1.0.0       # local .env loading
supabase>=2.4.0            # Postgres (history/analytics) + Storage (charts)
Authlib>=1.3.2             # Google OAuth
asyncpg>=0.29.0            # async Postgres driver (Chainlit data layer)
plotly>=5.20               # interactive charts
# sqlite3 — stdlib (internal CTMS DB)
```
