"""
Internal trial-operations (CTMS) layer — a local SQLite database of *our own*
study portfolio and per-site enrollment, plus a natural-language → SQL tool that
the ReAct agent calls alongside the public ClinicalTrials.gov / PubMed MCP tools.

Why this is the purposeful complement: ClinicalTrials.gov is the *public* registry
(what the world is doing) and PubMed is the *published* evidence; neither knows our
internal accrual. The `studies.nct_id` column links our portfolio straight to
ClinicalTrials.gov, so the agent can answer "how is our enrollment tracking vs. the
competitive landscape?" by combining this tool with the public ones.

Design (see plan): a SINGLE self-contained tool — one LLM pass turns the question
into a SQLite SELECT against an embedded schema + few-shot examples, a guard enforces
read-only, it executes against a read-only connection, and (on error) self-corrects
once. No introspection round-trips, no new dependencies (sqlite3 is stdlib; SQL
generation reuses the existing ChatOpenAI model).

The data is fully SYNTHETIC (no PHI). NCT IDs are real ClinicalTrials.gov studies so
the cross-tool demo returns real public data when the agent looks them up.
"""

import logging
import re
import sqlite3

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool

from config import TRIAL_OPS_DB_PATH

_log = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA_DDL = """
CREATE TABLE studies (
    internal_id           TEXT PRIMARY KEY,   -- our protocol ID, e.g. 'MAYO-2023-007'
    nct_id                TEXT,               -- ClinicalTrials.gov ID linking to the public registry
    title                 TEXT,               -- our internal short title
    therapeutic_area      TEXT,               -- Cardiometabolic | Oncology | Neurology
    phase                 TEXT,               -- Phase 1 | Phase 2 | Phase 3
    status                TEXT,               -- Recruiting | Active, not recruiting | Completed
    principal_investigator TEXT,
    target_enrollment     INTEGER,            -- our institution-wide target across all sites
    start_date            TEXT,               -- YYYY-MM-DD
    sponsor_type          TEXT                -- Industry | Investigator-initiated | Federal
);

CREATE TABLE sites (
    site_id   TEXT PRIMARY KEY,   -- e.g. 'SITE-RST'
    site_name TEXT,
    city      TEXT,
    state     TEXT,
    region    TEXT
);

CREATE TABLE enrollment (
    id          INTEGER PRIMARY KEY,
    internal_id TEXT,    -- FK -> studies.internal_id
    site_id     TEXT,    -- FK -> sites.site_id
    as_of_date  TEXT,    -- snapshot date, YYYY-MM-DD
    screened    INTEGER, -- patients screened at this site
    enrolled    INTEGER, -- patients enrolled at this site
    target      INTEGER, -- this site's enrollment target for the study
    status      TEXT     -- Ahead | On track | Behind | Complete
);
"""

# A compact, plain-text version of the schema for the NL→SQL prompt.
SCHEMA_DESCRIPTION = """\
Tables (SQLite):
- studies(internal_id, nct_id, title, therapeutic_area, phase, status,
          principal_investigator, target_enrollment, start_date, sponsor_type)
    therapeutic_area ∈ {Cardiometabolic, Cardiology, Oncology, Neurology,
                        Hematology, Infectious Disease}
    phase ∈ {Phase 1, Phase 2, Phase 3}
    status ∈ {Recruiting, Active, not recruiting, Completed}
    sponsor_type ∈ {Industry, Investigator-initiated, Federal}
    target_enrollment is the institution-wide target (sum of this study's site targets).
    nct_id links a study to the public ClinicalTrials.gov registry; it is NULL for
    unregistered studies (e.g. some investigator-initiated ones) — exclude NULLs when
    the question is about NCT/registered trials.
    start_date is 'YYYY-MM-DD'; for "started in <year>" use start_date LIKE '<year>-%'.
- sites(site_id, site_name, city, state, region)
    state is a 2-LETTER code (not the full name). The four sites are:
      ('SITE-RST','Mayo Clinic Rochester','Rochester','MN','Midwest')
      ('SITE-JAX','Mayo Clinic Jacksonville','Jacksonville','FL','Southeast')
      ('SITE-PHX','Mayo Clinic Arizona','Scottsdale','AZ','Southwest')
      ('SITE-MHS','Mayo Clinic Health System','La Crosse','WI','Midwest')
    Map a place mentioned in the question to the right column: e.g. "Arizona"→state='AZ'
    (or site_name LIKE '%Arizona%'), "Rochester"→city='Rochester'. Prefer matching
    site_name with LIKE when unsure which column the user means.
- enrollment(id, internal_id, site_id, as_of_date, screened, enrolled, target, status)
    internal_id → studies.internal_id ; site_id → sites.site_id
    status ∈ {Ahead, On track, Behind, Complete}
Join studies↔enrollment on internal_id, enrollment↔sites on site_id."""

# ── Synthetic seed data (deterministic — no RNG) ───────────────────────────────
# NCT IDs are real ClinicalTrials.gov studies; titles/IDs/enrollment are our own.

# (internal_id, nct_id, title, therapeutic_area, phase, status, PI, target, start, sponsor_type)
STUDIES = [
    ("MAYO-2021-014", "NCT03574597", "Semaglutide for Cardiovascular Outcomes in Overweight/Obesity",
     "Cardiometabolic", "Phase 3", "Active, not recruiting", "Dr. Helen Park", 180, "2021-06-15", "Industry"),
    ("MAYO-2021-033", "NCT01994889", "Tafamidis in Transthyretin Amyloid Cardiomyopathy (ATTR-ACT)",
     "Cardiology", "Phase 3", "Completed", "Dr. Mark Reyes", 55, "2021-04-22", "Industry"),
    ("MAYO-2022-009", "NCT03036124", "Dapagliflozin in Heart Failure with Reduced EF (DAPA-HF)",
     "Cardiology", "Phase 3", "Completed", "Dr. Mark Reyes", 110, "2022-02-18", "Industry"),
    ("MAYO-2022-031", "NCT03548935", "Semaglutide 2.4 mg for Weight Management",
     "Cardiometabolic", "Phase 3", "Completed", "Dr. Helen Park", 120, "2022-01-10", "Industry"),
    ("MAYO-2023-007", "NCT02578680", "Pembrolizumab plus Chemotherapy in Metastatic NSCLC",
     "Oncology", "Phase 3", "Active, not recruiting", "Dr. Raj Patel", 90, "2023-03-01", "Industry"),
    ("MAYO-2023-022", "NCT04437511", "Donanemab in Early Symptomatic Alzheimer's Disease",
     "Neurology", "Phase 3", "Recruiting", "Dr. Susan Cole", 75, "2023-09-12", "Industry"),
    ("MAYO-2023-041", "NCT03887455", "Lecanemab in Early Alzheimer's Disease (Clarity AD)",
     "Neurology", "Phase 3", "Active, not recruiting", "Dr. Susan Cole", 85, "2023-11-03", "Industry"),
    ("MAYO-2023-055", "NCT03954834", "Tirzepatide for Glycemic Control in Type 2 Diabetes (SURPASS)",
     "Cardiometabolic", "Phase 3", "Active, not recruiting", "Dr. Helen Park", 95, "2023-06-27", "Industry"),
    ("MAYO-2024-003", "NCT01866319", "Pembrolizumab versus Ipilimumab in Advanced Melanoma",
     "Oncology", "Phase 3", "Completed", "Dr. Raj Patel", 60, "2024-02-05", "Industry"),
    ("MAYO-2024-012", "NCT03434379", "Atezolizumab plus Bevacizumab in Hepatocellular Carcinoma (IMbrave150)",
     "Oncology", "Phase 3", "Active, not recruiting", "Dr. Raj Patel", 70, "2024-03-19", "Industry"),
    ("MAYO-2024-018", "NCT04368728", "mRNA Vaccine Immunogenicity Substudy",
     "Infectious Disease", "Phase 2", "Recruiting", "Dr. Liam Foster", 50, "2024-07-20", "Federal"),
    ("MAYO-2024-027", "NCT02348216", "Axicabtagene Ciloleucel in Refractory Large B-Cell Lymphoma",
     "Hematology", "Phase 2", "Recruiting", "Dr. Anita Rao", 40, "2024-05-14", "Industry"),
    ("MAYO-2024-039", "NCT04381936", "Dexamethasone in Hospitalized COVID-19 (RECOVERY substudy)",
     "Infectious Disease", "Phase 3", "Completed", "Dr. Liam Foster", 65, "2024-01-30", "Investigator-initiated"),
    ("MAYO-2025-002", None, "Investigator-Initiated CAR-NK Cell Therapy in Refractory Solid Tumors",
     "Hematology", "Phase 1", "Recruiting", "Dr. Anita Rao", 24, "2025-01-15", "Investigator-initiated"),
]

# (site_id, site_name, city, state, region)
SITES = [
    ("SITE-RST", "Mayo Clinic Rochester", "Rochester", "MN", "Midwest"),
    ("SITE-JAX", "Mayo Clinic Jacksonville", "Jacksonville", "FL", "Southeast"),
    ("SITE-PHX", "Mayo Clinic Arizona", "Scottsdale", "AZ", "Southwest"),
    ("SITE-MHS", "Mayo Clinic Health System", "La Crosse", "WI", "Midwest"),
]

# (id, internal_id, site_id, as_of_date, screened, enrolled, target, status)
_AS_OF = "2025-03-31"
ENROLLMENT = [
    # MAYO-2021-014 (target 180) — mature, mostly complete
    (1,  "MAYO-2021-014", "SITE-RST", _AS_OF, 142, 96, 90, "Ahead"),
    (2,  "MAYO-2021-014", "SITE-JAX", _AS_OF, 78,  52, 50, "On track"),
    (3,  "MAYO-2021-014", "SITE-PHX", _AS_OF, 49,  31, 40, "Behind"),
    # MAYO-2022-031 (target 120) — completed
    (4,  "MAYO-2022-031", "SITE-RST", _AS_OF, 95,  70, 70, "Complete"),
    (5,  "MAYO-2022-031", "SITE-JAX", _AS_OF, 63,  50, 50, "Complete"),
    # MAYO-2023-007 (target 90) — oncology, active
    (6,  "MAYO-2023-007", "SITE-RST", _AS_OF, 61,  38, 45, "Behind"),
    (7,  "MAYO-2023-007", "SITE-PHX", _AS_OF, 40,  27, 25, "Ahead"),
    (8,  "MAYO-2023-007", "SITE-MHS", _AS_OF, 22,  14, 20, "Behind"),
    # MAYO-2023-022 (target 75) — recruiting, early
    (9,  "MAYO-2023-022", "SITE-RST", _AS_OF, 55,  24, 35, "Behind"),
    (10, "MAYO-2023-022", "SITE-JAX", _AS_OF, 38,  19, 20, "On track"),
    (11, "MAYO-2023-022", "SITE-PHX", _AS_OF, 31,  18, 20, "On track"),
    # MAYO-2024-003 (target 60) — completed melanoma
    (12, "MAYO-2024-003", "SITE-PHX", _AS_OF, 48,  35, 35, "Complete"),
    (13, "MAYO-2024-003", "SITE-RST", _AS_OF, 33,  25, 25, "Complete"),
    # MAYO-2024-018 (target 50) — newest, recruiting
    (14, "MAYO-2024-018", "SITE-RST", _AS_OF, 27,  12, 25, "Behind"),
    (15, "MAYO-2024-018", "SITE-MHS", _AS_OF, 19,  11, 25, "Behind"),
    # MAYO-2021-033 (target 55) — ATTR cardiomyopathy, completed
    (16, "MAYO-2021-033", "SITE-RST", _AS_OF, 60,  35, 35, "Complete"),
    (17, "MAYO-2021-033", "SITE-JAX", _AS_OF, 35,  20, 20, "Complete"),
    # MAYO-2022-009 (target 110) — DAPA-HF, completed
    (18, "MAYO-2022-009", "SITE-RST", _AS_OF, 92,  60, 60, "Complete"),
    (19, "MAYO-2022-009", "SITE-JAX", _AS_OF, 78,  50, 50, "Complete"),
    # MAYO-2023-041 (target 85) — lecanemab, active
    (20, "MAYO-2023-041", "SITE-RST", _AS_OF, 70,  40, 40, "On track"),
    (21, "MAYO-2023-041", "SITE-JAX", _AS_OF, 45,  26, 25, "Ahead"),
    (22, "MAYO-2023-041", "SITE-PHX", _AS_OF, 32,  16, 20, "Behind"),
    # MAYO-2023-055 (target 95) — tirzepatide, active
    (23, "MAYO-2023-055", "SITE-RST", _AS_OF, 80,  50, 50, "On track"),
    (24, "MAYO-2023-055", "SITE-JAX", _AS_OF, 55,  33, 30, "Ahead"),
    (25, "MAYO-2023-055", "SITE-MHS", _AS_OF, 25,  13, 15, "Behind"),
    # MAYO-2024-012 (target 70) — atezolizumab HCC, active
    (26, "MAYO-2024-012", "SITE-RST", _AS_OF, 55,  35, 35, "On track"),
    (27, "MAYO-2024-012", "SITE-JAX", _AS_OF, 40,  24, 25, "Behind"),
    (28, "MAYO-2024-012", "SITE-PHX", _AS_OF, 18,   9, 10, "Behind"),
    # MAYO-2024-027 (target 40) — CAR-T lymphoma, recruiting
    (29, "MAYO-2024-027", "SITE-RST", _AS_OF, 38,  18, 25, "Behind"),
    (30, "MAYO-2024-027", "SITE-PHX", _AS_OF, 22,  11, 15, "Behind"),
    # MAYO-2024-039 (target 65) — dexamethasone COVID, completed
    (31, "MAYO-2024-039", "SITE-RST", _AS_OF, 70,  40, 40, "Complete"),
    (32, "MAYO-2024-039", "SITE-MHS", _AS_OF, 30,  25, 25, "Complete"),
    # MAYO-2025-002 (target 24) — investigator-initiated CAR-NK, recruiting, no NCT yet
    (33, "MAYO-2025-002", "SITE-RST", _AS_OF, 20,   8, 14, "Behind"),
    (34, "MAYO-2025-002", "SITE-PHX", _AS_OF, 12,   5, 10, "Behind"),
]


def ensure_db(db_path: str = TRIAL_OPS_DB_PATH) -> str:
    """Create and seed the SQLite DB if it does not already exist (idempotent,
    deterministic). Safe to call on every agent build / process boot."""
    import os
    if os.path.exists(db_path):
        return db_path
    con = sqlite3.connect(db_path)
    try:
        con.executescript(SCHEMA_DDL)
        con.executemany("INSERT INTO studies VALUES (?,?,?,?,?,?,?,?,?,?)", STUDIES)
        con.executemany("INSERT INTO sites VALUES (?,?,?,?,?)", SITES)
        con.executemany("INSERT INTO enrollment VALUES (?,?,?,?,?,?,?,?)", ENROLLMENT)
        con.commit()
    finally:
        con.close()
    _log.info("Seeded trial-operations DB at %s", db_path)
    return db_path


# ── NL → SQL generation ────────────────────────────────────────────────────────

FEW_SHOT = """\
Q: How is enrollment tracking across our trials?
SQL: SELECT s.internal_id, s.title, SUM(e.enrolled) AS enrolled, s.target_enrollment
     FROM studies s JOIN enrollment e ON e.internal_id = s.internal_id
     GROUP BY s.internal_id ORDER BY enrolled DESC;

Q: Which of our sites is furthest behind its enrollment target?
SQL: SELECT si.site_name, SUM(e.enrolled) AS enrolled, SUM(e.target) AS target,
     SUM(e.target) - SUM(e.enrolled) AS gap
     FROM enrollment e JOIN sites si ON si.site_id = e.site_id
     GROUP BY e.site_id ORDER BY gap DESC;

Q: Show our active Phase 3 oncology studies.
SQL: SELECT internal_id, nct_id, title, status, principal_investigator
     FROM studies WHERE phase = 'Phase 3' AND therapeutic_area = 'Oncology'
     AND status != 'Completed';

Q: What is our enrollment on the donanemab trial?   -- a SPECIFIC named trial: return the real IDs + the study TOTAL (never a per-site row)
SQL: SELECT s.internal_id, s.nct_id, s.title, SUM(e.enrolled) AS enrolled, s.target_enrollment
     FROM studies s JOIN enrollment e ON e.internal_id = s.internal_id
     WHERE s.title LIKE '%Donanemab%' GROUP BY s.internal_id;

Q: Which of our studies has the worst enrollment?   -- "worst/best/lagging enrollment" = fill rate, NOT absolute count
SQL: SELECT s.internal_id, s.title, SUM(e.enrolled) AS enrolled, s.target_enrollment,
     ROUND(SUM(e.enrolled) * 100.0 / s.target_enrollment, 1) AS pct_of_target
     FROM studies s JOIN enrollment e ON e.internal_id = s.internal_id
     GROUP BY s.internal_id ORDER BY pct_of_target ASC;

Q: Which PIs have trials that are behind target?   -- count TRIALS, not site rows
SQL: SELECT s.principal_investigator, COUNT(DISTINCT s.internal_id) AS trials_behind
     FROM studies s JOIN enrollment e ON e.internal_id = s.internal_id
     WHERE e.status = 'Behind' GROUP BY s.principal_investigator
     ORDER BY trials_behind DESC;

Q: How are we doing?   -- vague status question → portfolio enrollment-vs-target summary
SQL: SELECT s.internal_id, s.title, SUM(e.enrolled) AS enrolled, s.target_enrollment,
     ROUND(SUM(e.enrolled) * 100.0 / s.target_enrollment, 1) AS pct_of_target
     FROM studies s JOIN enrollment e ON e.internal_id = s.internal_id
     GROUP BY s.internal_id ORDER BY pct_of_target ASC;

Q: How many patients have we enrolled at the Rochester site?
SQL: SELECT si.site_name, SUM(e.enrolled) AS enrolled
     FROM enrollment e JOIN sites si ON si.site_id = e.site_id
     WHERE si.city = 'Rochester' GROUP BY e.site_id;

Q: Enrollment by site for NCT02578680.
SQL: SELECT si.site_name, e.screened, e.enrolled, e.target, e.status
     FROM enrollment e JOIN studies s ON s.internal_id = e.internal_id
     JOIN sites si ON si.site_id = e.site_id WHERE s.nct_id = 'NCT02578680';"""

_SQLGEN_SYSTEM = f"""You translate a question about Mayo Clinic's INTERNAL trial-operations \
database into ONE SQLite SELECT query.

{SCHEMA_DESCRIPTION}

Examples:
{FEW_SHOT}

Rules:
- Return ONLY the SQL — no prose, no markdown fences, no explanation.
- A SINGLE read-only SELECT (or WITH … SELECT). Never INSERT/UPDATE/DELETE/DDL.
- Always cap rows with LIMIT 50 unless the query already aggregates to few rows.
- Use the nct_id column when the question references a ClinicalTrials.gov / NCT number.
- For a SPECIFIC named trial, SELECT internal_id, nct_id, title AND the totals — so the
  real IDs are in the result and never have to be guessed — and return the study TOTAL
  (GROUP BY internal_id), not an individual site row.
- "worst / best / lagging / leading / how is X tracking" about enrollment = rank by FILL
  RATE (SUM(enrolled)*1.0/target), not absolute count (a small-target study naturally has
  fewer patients).
- Counting trials/studies from the enrollment table uses COUNT(DISTINCT internal_id) —
  enrollment has one row PER SITE, so COUNT(*) over-counts.
- A vague status question about our work ("how are we doing?", "where do we stand?",
  "give me a status update") = the portfolio enrollment-vs-target summary (per study,
  with % of target).
- If the question is truly unrelated to our studies/sites/enrollment, return exactly:
  SELECT 'NO_MATCH';"""

_FENCE = re.compile(r"^```(?:sql)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|ATTACH|DETACH|PRAGMA|"
    r"VACUUM|REINDEX|TRUNCATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


def _strip_sql(text: str) -> str:
    """Remove markdown fences / stray prose and keep the SQL statement."""
    s = _FENCE.sub("", text).strip()
    # keep through the first statement terminator if the model added trailing prose
    if ";" in s:
        s = s[: s.index(";") + 1]
    return s.strip()


async def _generate_sql(model, question: str, error: str = None, prior_sql: str = None) -> str:
    user = question
    if error:
        user = (f"Question: {question}\nYour previous SQL was:\n{prior_sql}\n"
                f"It failed with: {error}\nReturn a corrected single SQLite SELECT.")
    resp = await model.ainvoke(
        [SystemMessage(content=_SQLGEN_SYSTEM), HumanMessage(content=user)]
    )
    return _strip_sql(resp.content if isinstance(resp.content, str) else str(resp.content))


def _assert_read_only(sql: str) -> str:
    """Defense-in-depth guard (the read-only connection is the hard guarantee)."""
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        raise ValueError("multiple statements are not allowed")
    if not re.match(r"(?is)^\s*(SELECT|WITH)\b", s):
        raise ValueError("only SELECT/WITH queries are allowed")
    if _FORBIDDEN.search(s):
        raise ValueError("write/DDL keywords are not allowed")
    return s


def _run_readonly(sql: str, db_path: str):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        con.execute("PRAGMA query_only = ON")
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(50)
        return cols, rows
    finally:
        con.close()


def _format_rows(cols, rows) -> str:
    if not rows:
        return "No rows matched in the internal trial-operations database."
    header = " | ".join(cols)
    body = "\n".join(
        " | ".join("" if v is None else str(v) for v in row) for row in rows
    )
    return f"{header}\n{body}"


async def _answer(model, question: str, db_path: str) -> str:
    ensure_db(db_path)
    sql = await _generate_sql(model, question)
    try:
        clean = _assert_read_only(sql)
        cols, rows = _run_readonly(clean, db_path)
    except Exception as e1:
        _log.info("NL→SQL first attempt failed (%s); self-correcting once", e1)
        sql = await _generate_sql(model, question, error=str(e1), prior_sql=sql)
        try:
            clean = _assert_read_only(sql)
            cols, rows = _run_readonly(clean, db_path)
        except Exception as e2:
            _log.warning("NL→SQL retry failed: %s", e2)
            return ("Couldn't answer that from our internal trial-operations data. "
                    "Try rephrasing — e.g. ask about enrollment by site, studies behind "
                    "target, or our active trials by phase or therapeutic area.")
    if rows == [("NO_MATCH",)]:
        return ("That question isn't answerable from our internal trial-operations "
                "database (which holds our studies, sites, and per-site enrollment).")
    table = _format_rows(cols, rows)
    return f"Internal trial operations (Mayo CTMS).\n-- SQL: {clean}\n{table}"


_TOOL_DESCRIPTION = (
    "Query Mayo Clinic's INTERNAL trial-operations database (our OWN study portfolio, "
    "sites, and per-site enrollment vs. target) with a plain-English question. Use this "
    "for anything about OUR trials, OUR enrollment/accrual, OUR sites, recruitment status, "
    "or how our studies are tracking against target. This is private operational data, "
    "distinct from the public ClinicalTrials.gov registry. Input: a natural-language question."
)


def make_trial_ops_tool(model, db_path: str = TRIAL_OPS_DB_PATH) -> StructuredTool:
    """Build the `query_trial_operations` tool, closing over the model + db path."""

    async def query_trial_operations(question: str) -> str:
        return await _answer(model, question, db_path)

    return StructuredTool.from_function(
        coroutine=query_trial_operations,
        name="query_trial_operations",
        description=_TOOL_DESCRIPTION,
    )
