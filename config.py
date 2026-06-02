# ── MCP endpoints ─────────────────────────────────────────────────────────────
CLINICALTRIALS_MCP_URL = "https://clinicaltrials.caseyjhand.com/mcp"
PUBMED_MCP_URL         = "https://pubmed.caseyjhand.com/mcp"

# ── Model configuration ───────────────────────────────────────────────────────
MODEL_ID               = "gpt-4o-mini"               # OpenAI — main agent
MAIN_MAX_TOKENS        = 2048
NLSQL_MAX_TOKENS       = 256                          # NL→SQL generation (short output)

# ── Internal trial-operations DB (local SQLite, read-only) ────────────────────
import os as _os
TRIAL_OPS_DB_PATH      = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "trial_ops.db")

# ── Beta access whitelist ─────────────────────────────────────────────────────
BETA_WHITELIST: list[str] = [
    "swapno777@gmail.com",
    # add up to 19 more addresses here
]
